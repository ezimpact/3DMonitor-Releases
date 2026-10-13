"""Validated connection options shared by registry, API and camera routing."""
from urllib.parse import urlsplit
from .config import local_ip

def protocol_name(value):
    if value not in ('creality','moonraker','octoprint','prusalink'):raise ValueError('Выберите поддерживаемый протокол подключения')
    return value

def camera_path(value,ip):
    if not isinstance(value,str) or len(value)>1024 or '\\' in value or any(ord(c)<32 for c in value):raise ValueError('Некорректный адрес камеры')
    if not value:return ''
    parsed=urlsplit(value)
    if value.startswith('/'):
        if value.startswith('//') or parsed.fragment:raise ValueError('Укажите путь камеры на принтере')
        return value
    if parsed.scheme!='http' or parsed.username or parsed.password or parsed.hostname!=local_ip(ip) or parsed.fragment:
        raise ValueError('Камера должна использовать HTTP на IP этого принтера')
    port=parsed.port
    if port is not None and not 1<=port<=65535:raise ValueError('Некорректный порт камеры')
    return value

def connection_options(entry):
    protocol=protocol_name(entry.get('protocol','creality'))
    port=entry.get('api_port',{'creality':9999,'moonraker':7125,'octoprint':80,'prusalink':80}[protocol])
    if type(port) is not int or not 1<=port<=65535:raise ValueError('Порт: 1–65535')
    path=camera_path(entry.get('camera_path',''),entry['ip'])
    macro=entry.get('unload_macro','UNLOAD_FILAMENT')
    from .moonraker import macro_name
    macro_name(macro)
    return protocol,port,path,macro
