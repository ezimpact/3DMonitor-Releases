"""Read K1Monitor's registry; never write printer records from the saver."""
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import urllib.parse


def camera_url(entry):
    path=entry.get('camera_path','')
    if not isinstance(path,str) or not path or len(path)>1024 or '\\' in path or any(ord(c)<32 for c in path):
        raise ValueError('Камера не настроена')
    parsed=urllib.parse.urlsplit(path)
    if parsed.fragment:raise ValueError('Некорректный адрес камеры')
    if path.startswith('/') and not path.startswith('//'):return 'http://'+entry['ip']+path
    if parsed.scheme!='http' or parsed.hostname!=entry['ip'] or parsed.username or parsed.password:raise ValueError('Камера должна находиться на принтере')
    if parsed.port is not None and not 1<=parsed.port<=65535:raise ValueError('Некорректный порт камеры')
    return path


class RegistryReader:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve()
        self._lock = threading.Lock()
        self._last = []

    def snapshot(self):
        with self._lock:
            try:
                config = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
                raw_path = config.get('registry_path')
                bundled_root = self.config_path.parent.parent
                if (bundled_root / 'K1Monitor.exe').is_file():
                    raw_path = '../data/printers.json'
                if not raw_path:
                    raise ValueError('Выберите общую базу K1Monitor в config.json')
                path = Path(os.path.expandvars(raw_path))
                if not path.is_absolute(): path = self.config_path.parent / path
                data = json.loads(path.read_text(encoding='utf-8-sig'))
                printers = data['printers']
                if not isinstance(printers, list) or len(printers) > 32:
                    raise ValueError('Некорректная база K1Monitor')
                disabled = set(config.get('disabled_printers', []))
                entries, ids, ips = [], set(), set()
                networks = [ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
                for item in printers:
                    key, name, address = item['id'], item['name'], item['ip']
                    ip = ipaddress.ip_address(address)
                    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', key) or not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
                        raise ValueError('Некорректный принтер в базе K1Monitor')
                    if ip.version != 4 or not any(ip in network for network in networks) or key in ids or address in ips:
                        raise ValueError('Некорректный или повторяющийся адрес в базе K1Monitor')
                    ids.add(key); ips.add(address)
                    entry={'id': key, 'name': name, 'ip': str(ip)}
                    if item.get('protocol') in ('moonraker','octoprint','prusalink'):
                        entry.update(protocol=item['protocol'],camera_path=item.get('camera_path',''))
                        if not entry['camera_path']:continue
                        camera_url(entry)
                    if key not in disabled: entries.append(entry)
                self._last = entries
                return {'printers': [dict(p) for p in entries], 'registry_error': None}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return {'printers': [dict(p) for p in self._last], 'registry_error': 'База K1Monitor временно недоступна. Повторяем чтение…'}

    def get(self, key):
        return next((entry for entry in self.snapshot()['printers'] if entry['id'] == key), None)
