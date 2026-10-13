"""Bounded stock HTTP file transfers. Uploading never starts a print."""
import asyncio
from collections import OrderedDict
import hashlib
from pathlib import PurePosixPath
import tempfile
import time
from urllib.parse import quote

import httpx
from .files import GCODE_ROOT, safe_file_path

MAX_GCODE = 512 * 1024 * 1024

def gcode_name(name):
    if (not isinstance(name,str) or not 1<len(name)<=180 or not name.lower().endswith(('.gcode','.gco'))
            or name.startswith('.') or any(ord(c)<32 or c in '\\/:*?"\'<>|;%#' for c in name)):
        raise ValueError('Нужно имя файла .gcode без путей и специальных символов')
    return name

class FileTransfers:
    def __init__(self,manager,client_factory=httpx.AsyncClient):
        self.manager=manager;self.client_factory=client_factory
        self.slots=asyncio.Semaphore(2);self.locks={};self.requests=OrderedDict()

    def selected(self,printer_id,session,ip,monitor=None):
        current=self.manager.get(printer_id)
        if monitor is not None and current is not monitor:raise ValueError('Адрес принтера изменился')
        current.check_selection(session,ip)
        return current

    async def catalog_file(self,printer_id,file_id,revision,session,ip):
        monitor=self.selected(printer_id,session,ip)
        catalog=await monitor.list_files(refresh=True)
        self.selected(printer_id,session,ip,monitor)
        entry=catalog.get(file_id)
        valid=getattr(monitor,'file_path_valid',safe_file_path)
        if catalog.revision!=revision or not entry or not valid(entry['path']):
            raise ValueError('Список файлов изменился. Обновите его.')
        return monitor,dict(entry)

    @staticmethod
    def spool():return tempfile.SpooledTemporaryFile(max_size=2*1024*1024,mode='w+b')

    async def receive(self,request):
        length=request.headers.get('content-length')
        if length and int(length)>MAX_GCODE:raise ValueError('Файл больше 512 МБ')
        file=self.spool();size=0;digest=hashlib.sha256()
        try:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_GCODE:raise ValueError('Файл больше 512 МБ')
                if b'\0' in chunk:raise ValueError('Нужен текстовый файл G-code')
                file.write(chunk);digest.update(chunk)
            if not size:raise ValueError('Файл пуст')
            file.seek(0);return file,size,digest.hexdigest()
        except BaseException:file.close();raise

    async def download(self,printer_id,file_id,revision,session,ip):
        monitor,entry=await self.catalog_file(printer_id,file_id,revision,session,ip)
        if (entry['size_bytes'] or 0)>MAX_GCODE:raise ValueError('Файл больше 512 МБ')
        path='/downloads/gcode/'+quote(entry['path'][len(GCODE_ROOT):],safe='/')
        url=monitor.download_url(entry['path']) if hasattr(monitor,'download_url') else f'http://{ip}{path}'
        file=self.spool();size=0;digest=hashlib.sha256()
        try:
            async with self.client_factory(trust_env=False,timeout=httpx.Timeout(30,connect=5),follow_redirects=False,**(monitor.http_options() if hasattr(monitor,'http_options') else {})) as client:
                async with client.stream('GET',url) as response:
                    response.raise_for_status()
                    if any(kind in response.headers.get('content-type','').lower() for kind in ('html','json')):
                        raise ValueError('Принтер вернул страницу вместо файла')
                    async for chunk in response.aiter_bytes():
                        size+=len(chunk)
                        if size>MAX_GCODE:raise ValueError('Файл больше 512 МБ')
                        file.write(chunk);digest.update(chunk)
            self.selected(printer_id,session,ip,monitor)
            if monitor.files.revision!=revision:raise ValueError('Список файлов изменился во время скачивания')
            if not size or (entry['size_bytes'] is not None and entry['size_bytes']!=size):
                raise ValueError('Файл скачан не полностью. Обновите список.')
            file.seek(0);return file,size,digest.hexdigest(),entry['name']
        except BaseException:file.close();raise

    async def upload(self,printer_id,name,file,size,digest,session,ip,request_id,check_source=lambda:None):
        gcode_name(name)
        async with self.locks.setdefault(printer_id,asyncio.Lock()):
            monitor=self.selected(printer_id,session,ip)
            if name.lower().endswith('.gco') and getattr(monitor,'protocol','creality') not in ('octoprint','prusalink'):raise ValueError('Этот принтер принимает файлы .gcode')
            signature=(name,digest,size,session,ip);key=(printer_id,request_id)
            saved=self.requests.get(key)
            if saved:
                if saved[1]!=signature:raise ValueError('Идентификатор отправки уже использован')
                return saved[2]
            # Never overwrite an existing file, including an active job.
            catalog=await monitor.list_files(refresh=True)
            snapshot=self.selected(printer_id,session,ip,monitor).snapshot()
            if any(name.casefold() in (entry['name'].casefold(),PurePosixPath(entry['path']).name.casefold()) for entry in catalog.files):
                raise ValueError('На принтере уже есть файл с таким именем. Переименуйте файл перед загрузкой.')
            if snapshot['status'] in ('printing','paused','preparing','error') and PurePosixPath(snapshot['print']['file'] or '').name.casefold()==name.casefold():
                raise ValueError('Нельзя заменить файл текущей печати')
            check_source()
            result={'phase':'unconfirmed','message':'Отправка не подтверждена. Файл мог загрузиться; обновите список. Автоматического повтора нет.'}
            self.requests[key]=(time.monotonic(),signature,result)
            while self.requests and (len(self.requests)>256 or time.monotonic()-next(iter(self.requests.values()))[0]>86400):self.requests.popitem(last=False)
            file.seek(0)
            try:
                async with self.client_factory(trust_env=False,timeout=httpx.Timeout(120,connect=5),follow_redirects=False,**(monitor.http_options() if hasattr(monitor,'http_options') else {})) as client:
                    url=monitor.upload_url(name) if hasattr(monitor,'upload_url') else f'http://{ip}/upload/{quote(name,safe="")}'
                    options={'data':monitor.upload_fields()} if hasattr(monitor,'upload_fields') else {}
                    if hasattr(monitor,'upload_file'):await monitor.upload_file(client,name,file,size)
                    else:
                        response=await client.post(url,files={'file':(name,file,'application/octet-stream')},**options)
                        response.raise_for_status()
                self.selected(printer_id,session,ip,monitor)
                listing=await monitor.list_files(refresh=True)
                self.selected(printer_id,session,ip,monitor)
                uploaded=next((entry for entry in listing.files if entry['name']==name and entry['size_bytes']==size),None)
                if uploaded:result.update(phase='complete',message=f'Файл «{name}» загружен на {self.manager.entry(printer_id)["name"]}.')
            except (httpx.HTTPError,ValueError,KeyError,TimeoutError):pass
            monitor.files.updated=0
            return result
