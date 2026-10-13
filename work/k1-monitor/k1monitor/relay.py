"""One native WebRTC receiver per camera; bounded latest-frame delivery to viewers."""
import asyncio
from dataclasses import dataclass, field
import logging
import secrets
import time

LOG = logging.getLogger(__name__)
MAX_FRAME = 512 * 1024


@dataclass
class Source:
    generation: str
    ip: str
    viewers: set = field(default_factory=set)
    frame: bytes | None = None
    last_frame: float = 0
    created: float = field(default_factory=time.monotonic)
    error: str = ""
    idle_since: float | None = None


class CameraRelay:
    def __init__(self, manager, browser, base_url, clock=time.monotonic):
        self.manager, self.browser, self.base_url, self.clock = manager, browser, base_url, clock
        self.key = secrets.token_urlsafe(32)
        self.sources = {}
        self.changed = asyncio.Event()
        self.worker_connected = False
        self.worker_last_seen = 0
        self.task = None

    async def start(self):
        self.task = asyncio.create_task(self._maintenance())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
            self.task = None
        for printer_id in self.sources:
            self.fail(printer_id, "Служба передачи камеры остановлена")
        self.sources.clear()
        self.browser.stop()

    def source(self, printer_id, generation):
        source = self.sources.get(printer_id)
        if (not source or not secrets.compare_digest(source.generation, generation)
                or self.manager.get(printer_id).snapshot()["printer"]["ip"] != source.ip):
            raise KeyError(printer_id)
        return source

    def configuration(self):
        return {"type": "sources", "sources": [
            {"id": printer_id, "generation": source.generation,
             "camera": self.manager.get(printer_id).snapshot()["camera"]}
            for printer_id, source in self.sources.items() if printer_id in self.manager.monitors and not source.error]}

    async def subscribe(self, printer_id):
        ip = self.manager.get(printer_id).snapshot()["printer"]["ip"]
        source = self.sources.get(printer_id)
        if source and source.ip != ip:
            self.fail(printer_id, "IP принтера изменён. Переподключаем камеру")
        if source is None and len(self.sources) >= 6:
            # Snapshot subscribers have a short idle grace period. They must
            # never displace a live viewer or exhaust slots in a serial batch.
            idle=next((pid for pid,item in self.sources.items() if not item.viewers),None)
            if idle is None:raise ValueError("Через ПК можно смотреть до шести камер одновременно")
            del self.sources[idle];self.changed.set()
        if sum(len(item.viewers) for item in self.sources.values()) >= 32:
            raise ValueError("Слишком много открытых окон с камерами")
        self.browser.start(self.base_url + "internal/camera-relay#" + self.key)
        if source is None or source.error:
            previous_viewers = source.viewers if source else set()
            source = Source(generation=secrets.token_hex(16), ip=ip, created=self.clock(), viewers=previous_viewers)
            self.sources[printer_id] = source
            self.changed.set()
        source.idle_since = None
        queue = asyncio.Queue(maxsize=1)
        source.viewers.add(queue)
        if source.frame and self.clock() - source.last_frame < 2:
            queue.put_nowait(source.frame)
        return queue

    def unsubscribe(self, printer_id, queue):
        source = self.sources.get(printer_id)
        if source:
            source.viewers.discard(queue)
            if not source.viewers:
                source.idle_since = self.clock()

    async def snapshot(self,printer_id):
        queue=await self.subscribe(printer_id)
        try:
            async with asyncio.timeout(18):
                frame=await queue.get()
                if not isinstance(frame,bytes):raise ValueError('Снимок камеры недоступен')
                return frame
        finally:
            self.unsubscribe(printer_id,queue)
            # Keep the worker warm between retries and adjacent printers.
            # Normal maintenance removes unused streams after three seconds.

    @staticmethod
    def deliver(source, item):
        for queue in tuple(source.viewers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)

    def frame(self, printer_id, generation, jpeg):
        source = self.source(printer_id, generation)
        if not 16 <= len(jpeg) <= MAX_FRAME or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("Некорректный кадр камеры")
        if source.error:
            return
        now = self.clock()
        if source.frame is not None and now - source.last_frame < .15:
            return
        source.frame, source.last_frame = jpeg, now
        self.deliver(source, jpeg)

    def fail(self, printer_id, message):
        source = self.sources.get(printer_id)
        if source and not source.error:
            source.error = message[:300]
            source.frame = None
            self.deliver(source, {"type": "error", "message": source.error})
            self.changed.set()

    def sweep(self):
        now = self.clock()
        for printer_id, source in tuple(self.sources.items()):
            if printer_id not in self.manager.monitors:
                self.fail(printer_id, "Принтер удалён из списка")
                del self.sources[printer_id]
                self.changed.set()
            elif source.idle_since is not None and now - source.idle_since >= 3:
                del self.sources[printer_id]
                self.changed.set()
            elif self.manager.get(printer_id).snapshot()["printer"]["ip"] != source.ip:
                self.fail(printer_id, "IP принтера изменён. Переподключаем камеру")
            elif source.viewers and not source.error:
                if source.frame and now - source.last_frame > 12:
                    self.fail(printer_id, "Камера перестала присылать кадры")
                elif not source.frame and now - source.created > 25:
                    self.fail(printer_id, "Не удалось получить видео от камеры")
        if not self.sources:
            self.browser.stop()
        elif self.worker_connected and now - self.worker_last_seen > 10:
            for printer_id in self.sources:
                self.fail(printer_id, "Процесс передачи камеры не отвечает")
            self.browser.stop()

    async def _maintenance(self):
        while True:
            await asyncio.sleep(1)
            self.sweep()
