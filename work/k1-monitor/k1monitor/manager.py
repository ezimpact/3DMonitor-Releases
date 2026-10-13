"""Independent printer sessions and a persistent registry."""
import asyncio
import time

from .client import PrinterMonitor
from .scanner import NetworkScanner
from .credentials import CredentialsStore,validate_credentials
from .printer_order import PrinterOrder
from .language import LanguageSettings


class PrinterManager:
    def __init__(self, config, factory=PrinterMonitor):
        self.config = config
        self.language_settings = LanguageSettings(config.path.parent/'data/language.json')
        self.order_store = PrinterOrder(config.path.parent/'data/printer-order.json')
        self.credentials=CredentialsStore(config.path.parent/'printer-credentials.json')
        self.factory = factory
        self.monitors = {}
        self.scanner = NetworkScanner(config)
        self._lock = asyncio.Lock()
        self.started = False
        for entry in config.printers:
            self.monitors[entry["id"]] = self._make(entry)

    def _make(self, entry):
        if entry.get('protocol') in ('octoprint','prusalink'):
            from .http_printers import OctoPrintMonitor,PrusaLinkMonitor
            adapter=OctoPrintMonitor if entry['protocol']=='octoprint' else PrusaLinkMonitor
            return adapter(entry['ip'],entry['firmware'],api_port=entry.get('api_port',80),camera_path=entry.get('camera_path',''),
                credentials=self.credentials.get(entry['id']),poll_seconds=self.config.data['poll_seconds'],stale_after=self.config.data['stale_after_seconds'])
        if entry.get('protocol')=='moonraker':
            from .moonraker import MoonrakerMonitor
            monitor=MoonrakerMonitor(entry['ip'],entry['firmware'],api_port=entry.get('api_port',7125),camera_path=entry.get('camera_path',''),unload_macro=entry.get('unload_macro','UNLOAD_FILAMENT'),poll_seconds=self.config.data['poll_seconds'],stale_after=self.config.data['stale_after_seconds'])
            monitor.auto_unload=entry.get('auto_unload',False);monitor.auto_unload_temperature=entry.get('auto_unload_temperature',240)
            return monitor
        monitor = self.factory(entry["ip"], entry["firmware"], poll_seconds=self.config.data["poll_seconds"],
                            stale_after=self.config.data["stale_after_seconds"])
        if 'api_port' in entry:monitor.url=f"ws://{entry['ip']}:{entry['api_port']}/"
        monitor.auto_unload = entry.get('auto_unload',False)
        monitor.auto_unload_temperature = entry.get('auto_unload_temperature',240)
        return monitor

    async def set_auto_unload(self, printer_id, enabled=None, temperature=None):
        async with self._lock:
            monitor = self.get(printer_id)
            if enabled and monitor.snapshot().get('auto_unload',{}).get('supported') is False:raise ValueError('Автовыгрузка не поддерживается: нужны датчик и макрос выгрузки')
            self.config.update(printer_id,auto_unload=enabled,auto_unload_temperature=temperature)
            if enabled is not None:monitor.auto_unload = enabled
            if temperature is not None:monitor.auto_unload_temperature = temperature
            enabled=monitor.auto_unload
            if hasattr(monitor,'runout_plan'):
                monitor.runout_plan.tick(monitor.snapshot(),enabled,time.monotonic())
            return {'enabled':enabled,'temperature':monitor.auto_unload_temperature}

    def get(self, printer_id):
        if printer_id not in self.monitors:
            raise KeyError(printer_id)
        return self.monitors[printer_id]

    def entry(self, printer_id):
        self.get(printer_id)
        return next(item for item in self.config.printers if item["id"] == printer_id)

    async def start(self):
        self.started = True
        await asyncio.gather(*(monitor.start() for monitor in self.monitors.values()))

    async def stop(self):
        self.started = False
        await self.scanner.cancel()
        await asyncio.gather(*(monitor.stop() for monitor in self.monitors.values()))

    def snapshot(self):
        printers = []
        for entry in self.config.printers:
            info = self.monitors[entry["id"]].snapshot()
            info["printer"].update({key: entry[key] for key in ("id", "name", "ip")})
            info['printer'].update({key:entry[key] for key in ('protocol','api_port','camera_path','unload_macro') if key in entry})
            printers.append(info)
        return {"schema_version": 2, "printers": printers, "printer_order": self.order_store.current(self.config.printers), "scan": self.scanner.snapshot(),
                "networks": self.scanner.suggested_networks()}

    async def set_printer_order(self, ids):
        async with self._lock:
            return self.order_store.save(ids, self.config.printers)

    async def add(self, ip, name, **options):
        async with self._lock:
            credentials=options.pop('credentials',None)
            if credentials is not None:
                credentials=validate_credentials(credentials)
                if options.get('protocol') not in ('octoprint','prusalink'):raise ValueError('Авторизация доступна для OctoPrint и PrusaLink')
            entry = self.config.add(ip, name, **options)
            if credentials is not None:
                try:self.credentials.set(entry['id'],credentials)
                except OSError:
                    self.config.remove(entry['id']);raise
            monitor = self._make(entry)
            self.monitors[entry["id"]] = monitor
            if self.started:
                await monitor.start()
            return entry

    async def rename(self, printer_id, name):
        return await self.update(printer_id, name=name)

    async def update(self, printer_id, *, name=None, ip=None, **options):
        async with self._lock:
            old = self.get(printer_id)
            previous=dict(self.entry(printer_id));old_ip=previous['ip']
            credentials=options.pop('credentials',None)
            if credentials is not None:
                credentials=validate_credentials(credentials)
                if options.get('protocol',previous.get('protocol','creality')) not in ('octoprint','prusalink'):raise ValueError('Авторизация доступна для OctoPrint и PrusaLink')
            self.config.update(printer_id, name=name, ip=ip, **options)
            entry = self.entry(printer_id)
            address_changed=entry['ip']!=old_ip or any(entry.get(k)!=previous.get(k) for k in ('protocol','api_port'))
            auth_changed=(credentials is not None and credentials!=self.credentials.get(printer_id)) or address_changed
            if auth_changed:
                try:self.credentials.set(printer_id,credentials if credentials is not None else {'mode':'none'})
                except OSError:
                    self.config.save({**self.config.data,'printers':[previous if p['id']==printer_id else p for p in self.config.printers]});raise
            if auth_changed or entry["ip"] != old_ip or any(entry.get(k)!=previous.get(k) for k in ('protocol','api_port','camera_path','unload_macro')):
                replacement = self._make(entry)
                # Old UI snapshots must never authorize a command on the new
                # address, even when a job filename happens to match.
                replacement.state.connection_count = old.state.connection_count
                self.monitors[printer_id] = replacement
                await old.stop()
                if self.started: await replacement.start()
            return self.entry(printer_id)

    async def remove(self, printer_id):
        async with self._lock:
            monitor = self.get(printer_id)
            previous_credentials=self.credentials.get(printer_id)
            self.credentials.set(printer_id,{'mode':'none'})
            try:self.config.remove(printer_id)
            except OSError:
                self.credentials.set(printer_id,previous_credentials);raise
            del self.monitors[printer_id]
            await monitor.stop()
