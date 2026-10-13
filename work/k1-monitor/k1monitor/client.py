"""Persistent telemetry and explicit, single-attempt printer controls."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from websockets.asyncio.client import connect

from .telemetry import Telemetry
from .files import FileCatalog
from .operations import Operations, RunoutPlan, UnloadHeating

LOG = logging.getLogger(__name__)
STATUS_REQUEST = json.dumps({"method": "get", "params": {"ReqPrinterPara": 1}}, separators=(",", ":"))


class PrinterMonitor(Operations):
    persistent_status = True
    def __init__(self, host: str, firmware: str, *, port: int = 9999,
                 poll_seconds: float = 5, stale_after: float = 15, retry_min: float = 1):
        self.url = f"ws://{host}:{port}/"
        self.state = Telemetry(host, firmware, stale_after, persistent_status=self.persistent_status)
        self.poll_seconds = poll_seconds
        self.retry_min = retry_min
        self._task: asyncio.Task | None = None
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._command = None
        self._requests = {}
        self.files = FileCatalog()
        self._files_lock = asyncio.Lock()
        self.auto_unload = False
        self.auto_unload_temperature = 240
        self.unload_heating = UnloadHeating()
        self.runout_plan = RunoutPlan()
        self._automation_task = None
        self._operation_requests = {}
        self._objects_lock = asyncio.Lock()
        self._objects_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="printer-monitor")
        if self._automation_task is None or self._automation_task.done():
            self._automation_task = asyncio.create_task(self._automation_loop(), name='filament-runout')

    async def stop(self) -> None:
        if self._automation_task is not None:
            self._automation_task.cancel()
            try: await self._automation_task
            except asyncio.CancelledError: pass
            self._automation_task = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.state.disconnected()

    def snapshot(self):
        result = self.state.snapshot()
        self._update_confirmation()
        result["command"] = {key: value for key, value in self._command.items() if not key.startswith("_")} if self._command else None
        pending = self._command and self._command["phase"] == "pending"
        enabled = not result["connection"]["stale"] and not pending
        result["controls"] = {"pause": result["status"] == "printing" and enabled,
                              "resume": result["status"] == "paused" and enabled,
                              "stop": result["status"] in {"printing", "paused", "preparing"} and enabled}
        result["controls"]["start"] = enabled and result["status"] in {"idle", "completed", "stopped", "failed"} and result["state_code"] in {0,2,3,4}
        result['controls']['speed'] = enabled and result['state_code'] in (1,5) and 'curFeedratePct' in result['observed_fields']
        result['controls']['exclude'] = enabled and result['state_code'] in (1,5) and len([obj for obj in result['objects'] if not obj['excluded']])>1
        for heater,field in [('nozzle','targetNozzleTemp'),('bed','targetBedTemp0')]:
            result['controls'][heater]=enabled and result['state_code'] in (0,1,2,3,4,5) and self.state.data.get('withSelfTest') in (None,0,'0') and field in result['observed_fields'] and not self.unload_heating.pending
        result['auto_unload'] = {**self.runout_plan.public(), 'enabled':self.auto_unload,'temperature':self.auto_unload_temperature}
        result['printer']['protocol']='creality'
        result['capabilities']={'objects':True,'calibration':True,'auto_unload':True,'protocol':'CrealityPrint'}
        if self.unload_heating.message and self.auto_unload:result['auto_unload']['message']=self.unload_heating.message
        return result

    async def list_files(self, *, refresh=False):
        async with self._files_lock:
            if self._ws is None or not self.snapshot()['connection']['online']:
                raise ValueError('Нет связи с принтером')
            if refresh or not self.files.updated or time.monotonic()-self.files.updated>10:
                self.files.event.clear()
                try:
                    await asyncio.wait_for(self._send(self._ws,json.dumps({'method':'get','params':{'reqGcodeFile':1}})),timeout=3)
                    await asyncio.wait_for(self.files.event.wait(),timeout=8)
                except Exception as exc:
                    raise ValueError('Не удалось получить список G-code файлов. Проверьте связь с принтером.') from exc
            return self.files

    def _trim_requests(self,now):
        self._requests={key:value for key,value in self._requests.items() if now-value[0]<(86400 if value[1]['action']=='start' else 60)}

    async def _send(self, ws, message):
        async with self._send_lock:
            await ws.send(message)

    def _update_confirmation(self):
        cmd = self._command
        if not cmd or cmd["phase"] != "pending":
            return
        status = self.state.snapshot()["status"]
        # Only a new state/pause update counts; a temperature update cannot acknowledge a command.
        if cmd.get("_ack_revision", -1) > cmd["_revision"]:
            expected = {"pause": {"paused"}, "resume": {"printing"}, "stop": {"stopped", "idle", "completed"}, "start":{"printing","preparing"}}[cmd["action"]]
            matches = cmd['action']!='start' or self.state.data.get('printFileName') in {cmd.get('_file'),(cmd.get('_file') or '').rsplit('/',1)[-1]}
            if matches and (status in expected or (cmd["action"] == "stop" and self.state.data.get("state") in (0, "0"))):
                cmd["phase"] = "confirmed"
                cmd["message"] = "Принтер подтвердил изменение состояния"
        if cmd["phase"] == "pending" and time.monotonic() - cmd["_sent"] > 12:
            cmd["phase"] = "unconfirmed"
            cmd["message"] = "Команда отправлена, подтверждение от принтера не получено. Проверьте его состояние."

    async def control(self, action: str, request_id: str, expected_session: int, expected_file: str | None, *, expected_job=None, expected_ip=None):
        async with self._control_lock:
            now = time.monotonic()
            self._trim_requests(now)
            if request_id in self._requests:
                saved = self._requests[request_id][1]
                if saved["action"] != action:
                    raise ValueError("Идентификатор команды уже использован")
                return {key: value for key, value in saved.items() if not key.startswith("_")}
            snapshot = self.snapshot()
            if expected_ip is not None:self.check_selection(expected_session,expected_ip)
            if action not in {"pause", "resume", "stop"}:
                raise ValueError("Неизвестная команда")
            if not snapshot["controls"][action] or self._ws is None:
                raise ValueError("Команда недоступна в текущем состоянии принтера")
            if snapshot["connection"]["sessions"] != expected_session or snapshot["print"]["file"] != expected_file:
                raise ValueError("Состояние задания изменилось. Обновите страницу и повторите действие.")
            if expected_job is not None and snapshot['print'].get('job_id')!=expected_job:raise ValueError('Задание изменилось. Обновите страницу.')
            params = {"pause": 1 if action == "pause" else 0} if action != "stop" else {"stop": 1}
            cmd = {"id": request_id, "action": action, "phase": "pending", "message": "Ожидаем подтверждение принтера",
                   "_sent": now, "_revision": self.state.message_count}
            # Record before sending: ambiguous transport failure must never trigger an automatic retry.
            self._command = cmd
            self._requests[request_id] = (now, cmd)
            try:
                await asyncio.wait_for(self._send(self._ws, json.dumps({"method": "set", "params": params})), timeout=3)
                await self._send(self._ws, STATUS_REQUEST)
            except Exception:
                cmd["phase"] = "unconfirmed"
                cmd["message"] = "Соединение прервалось. Получение команды не подтверждено; автоматического повтора нет."
            LOG.info("Printer control %s: %s", self.state.host, action)
            return {key: value for key, value in cmd.items() if not key.startswith("_")}

    async def start_print(self, *, file_id, revision, request_id, expected_session, expected_ip, calibrate=True):
        # User-requested command. Never exercised by our probes or automated tests.
        async with self._control_lock:
            now=time.monotonic()
            self._trim_requests(now)
            if request_id in self._requests:
                saved=self._requests[request_id][1]
                if saved['action']!='start' or saved.get('_file_id')!=file_id or saved.get('_calibrate')!=calibrate:
                    raise ValueError('Идентификатор команды уже использован')
                return {key:value for key,value in saved.items() if not key.startswith('_')}
            def check():
                snapshot=self.snapshot()
                if not snapshot['controls']['start'] or self._ws is None:
                    raise ValueError('Запуск доступен только свободному принтеру со свежими данными')
                if snapshot['connection']['sessions']!=expected_session or self.state.host!=expected_ip:
                    raise ValueError('Подключение принтера изменилось. Обновите список файлов.')
            check()
            catalog=await self.list_files(refresh=True)
            check()
            entry=catalog.get(file_id)
            if catalog.revision!=revision or entry is None:
                raise ValueError('Список файлов изменился. Обновите его и выберите файл заново.')
            cmd={'id':request_id,'action':'start','phase':'pending','message':'Запрос запуска отправлен; ожидаем состояние принтера',
                 '_sent':time.monotonic(),'_revision':self.state.message_count,'_file':entry['path'],'_file_id':file_id,'_calibrate':calibrate}
            self._command=cmd;self._requests[request_id]=(time.monotonic(),cmd)
            try:
                await asyncio.wait_for(self._send(self._ws,json.dumps({'method':'set','params':{'opGcodeFile':'printprt:'+entry['path'],'enableSelfTest':1 if calibrate else 0}})),timeout=3)
                await self._send(self._ws,STATUS_REQUEST)
            except Exception:
                cmd['phase']='unconfirmed';cmd['message']='Подтверждение запуска не получено. Проверьте принтер; автоматического повтора нет.'
            LOG.info('Printer start requested: %s',self.state.host)
            return {key:value for key,value in cmd.items() if not key.startswith('_')}

    async def _run(self) -> None:
        delay = self.retry_min
        while True:
            session_started = time.monotonic()
            try:
                async with connect(self.url, subprotocols=["wsslicer"], compression=None,
                                   ping_interval=None, proxy=None, open_timeout=5,
                                   close_timeout=2, max_size=2 * 1024 * 1024) as ws:
                    self.state.begin_session()
                    self.files.reset()
                    self._objects_event.clear()
                    self._ws = ws
                    session_started = time.monotonic()
                    LOG.info("Connected to printer %s", self.url)
                    next_poll = 0.0
                    await self._send(ws,json.dumps({'method':'get','params':{'reqGcodeFile':1}}))
                    while True:
                        now = time.monotonic()
                        if now >= next_poll:
                            await self._send(ws, STATUS_REQUEST)
                            next_poll = now + self.poll_seconds
                            # Stock K1 may send no changed values in idle. A protocol
                            # pong proves liveness without pretending telemetry is fresh.
                            try:
                                pong = await ws.ping()
                                await asyncio.wait_for(pong, timeout=3)
                                self.state.heartbeat()
                            except TimeoutError:
                                pass
                        silence = now - (self.state.last_link_rx if self.state.last_link_rx is not None else session_started)
                        if self.state.last_rx is None and now - session_started > max(self.state.stale_after * 2, self.poll_seconds * 3):
                            raise TimeoutError("No initial printer telemetry; reconnecting")
                        if silence > max(self.state.stale_after * 2, self.poll_seconds * 3):
                            raise TimeoutError("No fresh printer telemetry; reconnecting")
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=min(1, self.poll_seconds))
                        except TimeoutError:
                            continue
                        if raw == "ok" or raw == b"ok":
                            self.state.heartbeat()
                            continue
                        try:
                            payload = json.loads(raw)
                        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                            LOG.debug("Ignored non-JSON frame")
                            continue
                        if isinstance(payload, dict) and payload.get("ModeCode") == "heart_beat":
                            self.state.heartbeat()
                            await self._send(ws, "ok")
                        else:
                            try:self.files.ingest(payload)
                            except ValueError:LOG.warning('Invalid file list from %s',self.state.host)
                            ingested = self.state.ingest(payload)
                            if ingested and isinstance(payload,dict) and 'objects' in payload:self._objects_event.set()
                            if ingested and self._command and isinstance(payload, dict) and any(key in payload for key in ("state", "pause", "paused", "isPaused")):
                                self._command["_ack_revision"] = self.state.message_count
                                self._update_confirmation()
            except asyncio.CancelledError:
                self.state.disconnected()
                raise
            except Exception as exc:
                self.state.disconnected(f"{type(exc).__name__}: {exc}")
                LOG.warning("Printer connection interrupted: %s", exc)
            finally:
                self._ws = None
                self.state.transport_open = False
                if self._command and self._command["phase"] == "pending":
                    self._command["phase"] = "unconfirmed"
                    self._command["message"] = "Связь прервалась до подтверждения команды"
            # Reset backoff only after a stable session, not a successful handshake.
            if time.monotonic() - session_started >= 10:
                delay = self.retry_min
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)
