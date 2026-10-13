"""Public GitHub releases: anonymous requests, verified download, explicit install."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
from . import __version__
from .config import atomic_json
from .notifications import telegram_ssl_context

REPOSITORY='ezimpact/3DMonitor-Releases'
API='https://api.github.com/repos/'+REPOSITORY
MAX_INSTALLER=512*1024*1024
REDIRECT_HOSTS={'release-assets.githubusercontent.com','objects.githubusercontent.com'}

def version_tuple(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,5}\.\d{1,5}(?:\.\d{1,5})?',value):raise ValueError('Некорректная версия обновления')
    return tuple(int(part) for part in value.split('.'))+(0,)*(3-len(value.split('.')))

def release_plan(data,current):
    if not isinstance(data,dict) or data.get('draft') or data.get('prerelease'):raise ValueError('GitHub не вернул стабильный выпуск')
    tag=data.get('tag_name','');version=tag[1:] if isinstance(tag,str) and tag.startswith('v') else ''
    if version_tuple(version)<=version_tuple(current):return None
    assets=data.get('assets');name='3DMonitor-Setup-v'+version+'.exe';checksum=name[:-4]+'.sha256'
    if not isinstance(assets,list):raise ValueError('В выпуске нет установщика и SHA256')
    found={}
    for asset in assets:
        if not isinstance(asset,dict) or asset.get('name') not in (name,checksum):continue
        size=asset.get('size');identifier=asset.get('id')
        if asset['name'] in found or type(identifier) is not int or identifier<1 or type(size) is not int or not 1<=size<=(MAX_INSTALLER if asset['name']==name else 8192) or asset.get('state')!='uploaded':raise ValueError('Некорректные файлы обновления')
        found[asset['name']]=asset
    if set(found)!={name,checksum}:raise ValueError('В выпуске нет установщика и SHA256')
    return {'version':version,'name':name,'size':found[name]['size'],'asset_id':found[name]['id'],'checksum_id':found[checksum]['id'],'digest':found[name].get('digest'),'release_url':'https://github.com/'+REPOSITORY+'/releases/tag/v'+version}

def checksum_value(content,name):
    try:text=content.decode('ascii').strip()
    except UnicodeError:raise ValueError('Некорректный файл SHA256') from None
    match=re.fullmatch(r'([0-9a-fA-F]{64})[ \t]+\*?'+re.escape(name),text)
    if not match:raise ValueError('SHA256 не соответствует имени установщика')
    return match[1].lower()

class Updates:
    def __init__(self,root,*,control=None,port=8765,install_supported=False,client_factory=None,runner=subprocess.Popen,current=__version__,clock=time.time):
        self.root=Path(root).resolve();self.settings=self.root/'data/updates.json';self.cache=self.root/'cache/updates'
        self.control,self.port,self.install_supported=control,port,install_supported
        self.runner,self.current,self.clock=runner,current,clock
        self.client_factory=client_factory or (lambda:httpx.AsyncClient(verify=telegram_ssl_context(),timeout=httpx.Timeout(30,connect=12),follow_redirects=False))
        self.auto_check=True;self.saved_settings={}
        if self.settings.is_file():
            self.saved_settings=json.loads(self.settings.read_text(encoding='utf-8-sig'));self.auto_check=self.saved_settings.get('auto_check',True) is True
        # Preserve legacy encrypted preferences without decrypting or sending them.
        self.lock=asyncio.Lock();self.task=None;self.download_task=None;self.plan=None;self.downloaded=None;self.phase='idle';self.error='';self.checked_at=None;self.received=0
        self.last_install=None
        result=self.cache/'install-result.json'
        if result.is_file() and result.stat().st_size<=4096:
            try:
                data=json.loads(result.read_text(encoding='utf-8-sig'))
                if type(data.get('success')) is bool:self.last_install={'success':data['success']}
            except (OSError,ValueError,AttributeError):pass

    def status(self):
        return {'repository':REPOSITORY,'current_version':self.current,'has_token':False,'auto_check':self.auto_check,'phase':self.phase,'error':self.error,'credential_error':False,'checked_at':self.checked_at,'available':({key:self.plan[key] for key in ('version','name','size','release_url')} if self.plan else None),'downloaded':bool(self.downloaded),'received':self.received,'installation_supported':self.install_supported,'last_install':self.last_install}

    async def configure(self,auto_check,token=None,forget=False):
        if type(auto_check) is not bool:raise ValueError('Некорректные настройки обновления')
        if self.lock.locked() or self.download_task and not self.download_task.done():raise ValueError('Проверка или загрузка обновления уже выполняется')
        async with self.lock:
            if token is not None:raise ValueError('Публичные обновления не требуют токена GitHub')
            value={**self.saved_settings,'schema_version':1,'repository':REPOSITORY,'auto_check':auto_check}
            if forget:value.pop('token',None)
            atomic_json(self.settings,value);self.saved_settings=value;self.auto_check=auto_check
            self.error='';self.plan=None;self.downloaded=None;self.phase='idle'
        return self.status()

    def headers(self,asset=False):
        return {'Accept':'application/octet-stream' if asset else 'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'3DMonitor/'+self.current,'Accept-Encoding':'identity'}

    @staticmethod
    def check_http(response):
        if response.status_code==401:raise ValueError('GitHub временно недоступен. Повторите позже.')
        if response.status_code in (403,404):raise ValueError('Публичный выпуск недоступен или GitHub ограничил запросы. Повторите позже.')
        if response.status_code!=200:raise ValueError('GitHub временно недоступен. Повторите позже.')

    async def read_response(self,response,limit):
        parts=[];length=0
        async for part in response.aiter_bytes():
            length+=len(part)
            if length>limit:raise ValueError('Ответ GitHub превышает допустимый размер')
            parts.append(part)
        return b''.join(parts)

    async def asset_response(self,client,identifier,consumer):
        # Anonymous requests only to the fixed public repository and approved HTTPS asset hosts.
        async with client.stream('GET',API+'/releases/assets/'+str(identifier),headers=self.headers(True)) as response:
            if response.status_code in (301,302,303,307,308):
                location=response.headers.get('location','');url=urlsplit(location)
                if url.scheme!='https' or url.hostname not in REDIRECT_HOSTS or url.username or url.password or url.port not in (None,443) or url.fragment:raise ValueError('GitHub вернул недопустимый адрес загрузки')
            else:self.check_http(response);return await consumer(response)
        async with client.stream('GET',location,headers={'Accept':'application/octet-stream','Accept-Encoding':'identity','User-Agent':'3DMonitor/'+self.current}) as response:
            self.check_http(response);return await consumer(response)

    async def check(self):
        if self.lock.locked():raise ValueError('Проверка или загрузка обновления уже выполняется')
        async with self.lock:
            self.error='';self.phase='checking'
            try:
                async with self.client_factory() as client:
                    async with client.stream('GET',API+'/releases/latest',headers=self.headers()) as response:
                        self.check_http(response);data=json.loads(await self.read_response(response,2*1024*1024))
                plan=release_plan(data,self.current)
                if plan!=self.plan:self.downloaded=None
                self.plan=plan;self.checked_at=self.clock();self.phase='available' if plan else 'current'
            except (httpx.HTTPError,OSError,json.JSONDecodeError):self.phase='error';self.error='Не удалось связаться с GitHub. Проверьте интернет и VPN.'
            except ValueError as error:self.phase='error';self.error=str(error)
        return self.status()

    async def download(self):
        if self.lock.locked():raise ValueError('Проверка или загрузка обновления уже выполняется')
        async with self.lock:
            if not self.plan:raise ValueError('Сначала проверьте наличие обновления')
            self.phase='downloading';self.error='';self.received=0;self.downloaded=None
            plan=dict(self.plan);partial=None
            try:
                self.cache.mkdir(parents=True,exist_ok=True)
                if self.cache.resolve()!=self.root/'cache/updates':raise ValueError('Некорректная папка обновления')
                path=self.cache/plan['name'];partial=path.with_suffix('.part')
                async with self.client_factory() as client:
                    checksum=await self.asset_response(client,plan['checksum_id'],lambda response:self.read_response(response,8192));digest=checksum_value(checksum,plan['name'])
                    if plan['digest'] is not None and plan['digest']!='sha256:'+digest:raise ValueError('Контрольные суммы GitHub и выпуска различаются')
                    async def write(response):
                        result=hashlib.sha256()
                        with partial.open('wb') as output:
                            async for block in response.aiter_bytes(256*1024):
                                self.received+=len(block)
                                if self.received>plan['size'] or self.received>MAX_INSTALLER:raise ValueError('Размер установщика не соответствует выпуску')
                                output.write(block);result.update(block)
                        return result.hexdigest()
                    actual=await self.asset_response(client,plan['asset_id'],write)
                if self.received!=plan['size'] or actual!=digest:raise ValueError('Проверка SHA256 установщика не пройдена. Файл не будет запущен.')
                with partial.open('rb') as file:
                    if file.read(2)!=b'MZ':raise ValueError('Загруженный файл не является установщиком Windows')
                partial.replace(path);self.downloaded={'path':path,'sha256':digest};self.phase='ready'
            except asyncio.CancelledError:
                self.phase='available';raise
            except (httpx.HTTPError,OSError):self.phase='error';self.error='Загрузка обновления прервана. Повторите позже.'
            except ValueError as error:self.phase='error';self.error=str(error)
            finally:
                if partial and partial.exists():partial.unlink()
        return self.status()

    def begin_download(self):
        if self.lock.locked() or self.download_task and not self.download_task.done():raise ValueError('Проверка или загрузка обновления уже выполняется')
        if not self.plan:raise ValueError('Сначала проверьте наличие обновления')
        self.phase='downloading';self.download_task=asyncio.create_task(self.download())
        return self.status()

    def launch_install(self):
        if not self.install_supported or not self.control:raise ValueError('Обновление нужно установить вручную на основном ПК')
        if self.lock.locked() or not self.downloaded or not self.plan:raise ValueError('Сначала загрузите и проверьте установщик')
        path=self.downloaded['path'];digest=self.downloaded['sha256']
        if path.resolve()!=self.cache/self.plan['name'] or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Установщик изменился после проверки. Загрузите его заново.')
        script=self.cache/'install-update.ps1';script.write_bytes((Path(__file__).parent/'update_install.ps1').read_bytes())
        job=self.cache/'install-job.json';atomic_json(job,{'app_dir':str(self.root),'installer':str(path),'sha256':digest,'version':self.plan['version'],'port':self.port})
        command=[str(Path(os.environ.get('WINDIR',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-Job',str(job)]
        # The helper outlives this one-file process. Its restarted EXE must
        # unpack fresh resources after the old bootloader removes its temp dir.
        self.runner(command,cwd=str(self.root),creationflags=subprocess.CREATE_NO_WINDOW,env={**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'})
        self.last_install=None;self.phase='installing';self.control.request('stop')

    async def start(self):
        async def loop():
            await asyncio.sleep(60)
            while True:
                if self.auto_check and not self.lock.locked():await self.check()
                await asyncio.sleep(6*60*60)
        self.task=asyncio.create_task(loop())

    async def stop(self):
        pending=[task for task in (self.task,self.download_task) if task]
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)
        self.task=self.download_task=None
