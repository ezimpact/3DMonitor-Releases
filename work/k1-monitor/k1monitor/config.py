"""Validated printer registry with atomic, durable local persistence."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import shutil
import time
import uuid

LAN_RANGES = tuple(ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def default_registry_path(app_dir):
    return Path(app_dir) / "data" / "printers.json"


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        # A short-lived reader in the screensaver may still hold a Windows
        # file handle. Never truncate the shared registry to replace it.
        for attempt in range(5):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 4: raise
                time.sleep(.02)
    finally:
        temp.unlink(missing_ok=True)


def local_ip(value: str) -> str:
    address = ipaddress.IPv4Address(value.strip())
    if not any(address in network for network in LAN_RANGES):
        raise ValueError("Укажите IPv4 принтера в локальной сети: 10.x.x.x, 172.16–31.x.x или 192.168.x.x")
    return str(address)


def printer_name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 64 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Название должно содержать от 1 до 64 символов без управляющих символов")
    return value


def scan_network(value: str) -> ipaddress.IPv4Network:
    network = ipaddress.IPv4Network(value.strip(), strict=False)
    if network.num_addresses > 1024 or network.prefixlen > 30:
        raise ValueError("Выберите подсеть от /22 до /30, например 192.168.4.0/24")
    if not any(network.subnet_of(parent) for parent in LAN_RANGES):
        raise ValueError("Сканирование доступно только для локальных подсетей")
    return network


class ConfigStore:
    def __init__(self, path: Path, shared_path: Path | None = None, *, portable=False):
        self.path = path.resolve()
        raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        configured = raw.get("registry_path")
        self.registry_path = None
        import_path = None
        if configured:
            registry = Path(os.path.expandvars(configured)).expanduser()
            self.registry_path = (registry if registry.is_absolute() else self.path.parent / registry).resolve()
        elif shared_path:
            self.registry_path = shared_path.resolve()
        if portable:
            import_path = self.registry_path
            self.registry_path = default_registry_path(self.path.parent).resolve()
        if self.registry_path == self.path:
            raise ValueError("Общая база должна быть отдельным файлом")
        poll, stale = float(raw.get("poll_seconds", 5)), float(raw.get("stale_after_seconds", 15))
        if not 2 <= poll <= 60 or not 2 * poll <= stale <= 180:
            raise ValueError("Некорректный интервал обновления")
        entries = raw.get("printers")
        legacy = entries is None and "printer_ip" in raw
        if legacy:
            entries = [{"id": "k1max", "name": "K1 Max", "ip": raw["printer_ip"], "firmware": raw.get("firmware", "unknown")}]
        if self.registry_path and self.registry_path.exists():
            entries = json.loads(self.registry_path.read_text(encoding="utf-8-sig")).get("printers")
        elif portable and import_path and import_path.exists():
            entries = json.loads(import_path.read_text(encoding="utf-8-sig")).get("printers")
        elif configured and entries is None:
            raise ValueError("База принтеров не найдена. Переносите папку 3DMonitor целиком, включая data/printers.json.")
        if not isinstance(entries, list) or len(entries) > 32:
            raise ValueError("В конфигурации должен быть список не более 32 принтеров")
        self.data = {"schema_version": 2, "poll_seconds": poll, "stale_after_seconds": stale, "printers": []}
        ids, ips = set(), set()
        for entry in entries:
            item = {"id": str(entry.get("id") or uuid.uuid4().hex), "name": printer_name(entry["name"]),
                    "ip": local_ip(entry["ip"]), "firmware": str(entry.get("firmware", "unknown"))[:128]}
            if 'auto_unload' in entry:
                if type(entry['auto_unload']) is not bool: raise ValueError('Некорректная настройка автовыгрузки')
                item['auto_unload'] = entry['auto_unload']
            if 'auto_unload_temperature' in entry:
                temp=entry['auto_unload_temperature']
                if type(temp) is not int or not 180<=temp<=300:raise ValueError('Температура выгрузки: 180–300 °C')
                item['auto_unload_temperature']=temp
            if any(key in entry for key in ('protocol','api_port','camera_path','unload_macro')):
                from .protocols import connection_options
                protocol,port,camera,macro=connection_options({**entry,'ip':item['ip']})
                for key,value in [('protocol',protocol),('api_port',port),('camera_path',camera),('unload_macro',macro)]:
                    if key in entry:item[key]=value
            if not item["id"].replace("-", "").replace("_", "").isalnum() or len(item["id"]) > 64:
                raise ValueError("Некорректный идентификатор принтера")
            if item["id"] in ids or item["ip"] in ips:
                raise ValueError("Принтеры в конфигурации повторяются")
            self.data["printers"].append(item)
            ids.add(item["id"])
            ips.add(item["ip"])
        if self.registry_path:
            if not self.registry_path.exists():
                atomic_json(self.registry_path, {"schema_version": 1, "printers": self.printers})
            relative_configured = configured and not Path(os.path.expandvars(configured)).is_absolute()
            target_path = "data/printers.json" if portable else configured if relative_configured else str(self.registry_path)
            if configured != target_path or "printers" in raw or legacy:
                backup = self.path.with_name("config.pre-portable.backup.json" if portable else "config.pre-shared.backup.json")
                if not backup.exists(): shutil.copy2(self.path, backup)
                atomic_json(self.path, {"schema_version": 4 if portable else 3, "poll_seconds": poll, "stale_after_seconds": stale,
                                        "registry_path": target_path})
        elif legacy:
            backup = self.path.with_suffix(".v0.1.backup.json")
            if not backup.exists():
                shutil.copy2(self.path, backup)
            self.save(self.data)

    @property
    def printers(self):
        return self.data["printers"]

    def save(self, data):
        if self.registry_path:
            atomic_json(self.registry_path, {"schema_version": 1, "printers": data["printers"]})
        else:
            atomic_json(self.path, data)
        self.data = data

    def add(self, ip: str, name: str, **options):
        ip, name = local_ip(ip), printer_name(name)
        if any(item["ip"] == ip for item in self.printers):
            raise ValueError("Этот IP уже добавлен")
        if len(self.printers) >= 32:
            raise ValueError("Достигнут предел: 32 принтера")
        entry = {"id": uuid.uuid4().hex, "name": name, "ip": ip, "firmware": "unknown"}
        if options:
            from .protocols import connection_options
            protocol,port,camera,macro=connection_options({**entry,**options})
            entry.update(protocol=protocol,api_port=port,camera_path=camera,unload_macro=macro)
        self.save({**self.data, "printers": [*self.printers, entry]})
        return entry

    def rename(self, printer_id: str, name: str):
        self.update(printer_id, name=name)

    def update(self, printer_id: str, *, name=None, ip=None, auto_unload=None, auto_unload_temperature=None, **options):
        if not any(item["id"] == printer_id for item in self.printers):
            raise KeyError(printer_id)
        changes = {}
        if name is not None: changes["name"] = printer_name(name)
        if auto_unload is not None:
            if type(auto_unload) is not bool: raise ValueError('Некорректная настройка автовыгрузки')
            changes['auto_unload'] = auto_unload
        if auto_unload_temperature is not None:
            if type(auto_unload_temperature) is not int or not 180<=auto_unload_temperature<=300:raise ValueError('Температура выгрузки: 180–300 °C')
            changes['auto_unload_temperature']=auto_unload_temperature
        if ip is not None:
            changes["ip"] = local_ip(ip)
            if any(item["id"] != printer_id and item["ip"] == changes["ip"] for item in self.printers):
                raise ValueError("Этот IP уже добавлен")
        current=next(item for item in self.printers if item['id']==printer_id)
        options={key:value for key,value in options.items() if value is not None}
        if options or (ip is not None and 'camera_path' in current):
            from .protocols import connection_options
            combined={**current,**changes,**options}
            if options.get('protocol',current.get('protocol','creality'))!=current.get('protocol','creality'):
                if 'api_port' not in options:combined.pop('api_port',None)
                changes['auto_unload']=False
            protocol,port,camera,macro=connection_options(combined)
            changes.update(protocol=protocol,api_port=port,camera_path=camera,unload_macro=macro)
        entries = [{**item, **changes} if item["id"] == printer_id else item for item in self.printers]
        if entries == self.printers: return
        self.save({**self.data, "printers": entries})

    def remove(self, printer_id: str):
        if not any(item["id"] == printer_id for item in self.printers):
            raise KeyError(printer_id)
        self.save({**self.data, "printers": [item for item in self.printers if item["id"] != printer_id]})
