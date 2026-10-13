"""Private printer credentials, kept outside the shared/exported registry."""
import json
import httpx
from .config import atomic_json


def validate_credentials(value):
    if not isinstance(value,dict):raise ValueError('Некорректные параметры авторизации')
    mode=value.get('mode','none')
    if mode not in ('none','apikey','digest'):raise ValueError('Неизвестный способ авторизации')
    result={'mode':mode}
    for key in ('api_key','username','password'):
        item=value.get(key,'')
        if hasattr(item,'get_secret_value'):item=item.get_secret_value()
        if not isinstance(item,str) or len(item)>1024 or any(ord(c)<32 or ord(c)==127 for c in item):
            raise ValueError('Некорректные параметры авторизации')
        result[key]=item
    if mode=='apikey' and not result['api_key']:raise ValueError('Введите API-ключ')
    if mode=='digest' and (not result['username'] or not result['password']):raise ValueError('Введите логин и пароль PrusaLink')
    return result if mode!='none' else {'mode':'none'}


def request_options(value):
    if value.get('mode')=='apikey':return {'headers':{'X-Api-Key':value['api_key']}}
    if value.get('mode')=='digest':return {'auth':httpx.DigestAuth(value['username'],value['password'])}
    return {}


class CredentialsStore:
    def __init__(self,path):
        self.path=path
        self.entries=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        if not isinstance(self.entries,dict):raise ValueError('Некорректный файл авторизации принтеров')
        self.entries={key:validate_credentials(value) for key,value in self.entries.items()}
    def get(self,key):return dict(self.entries.get(key,{'mode':'none'}))
    def set(self,key,value):
        clean=validate_credentials(value)
        entries=dict(self.entries)
        if clean['mode']=='none':entries.pop(key,None)
        else:entries[key]=clean
        if entries!=self.entries:
            atomic_json(self.path,entries);self.entries=entries
