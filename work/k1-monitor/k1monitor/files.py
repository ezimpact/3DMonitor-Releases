"""Read stock K1 file listings and proxy bounded raster thumbnails."""
import asyncio
from collections import OrderedDict
import hashlib
import json
from pathlib import PurePosixPath
import time
from urllib.parse import quote

import httpx
from .telemetry import integer

FILE_FIELDS = ('retGcodeFileInfo', 'retGcodeFileInfo2')
GCODE_ROOT = '/usr/data/printer_data/gcodes/'

def safe_directory(value):
    return (isinstance(value,str) and len(value)<=1024 and (value=='' or
            not value.startswith('/') and not any(part in ('','..','.') for part in value.split('/')))
            and not any(ord(c)<32 or c in '\\:' for c in value))

def catalog_path(item):
    path=item['path']
    return path[len(GCODE_ROOT):] if path.startswith(GCODE_ROOT) else path.lstrip('/')

def safe_file_path(path):
    if not isinstance(path,str) or len(path)>1024 or not path.startswith(GCODE_ROOT): return False
    tail=path[len(GCODE_ROOT):]
    return bool(tail) and not any(part in ('','..','.') for part in tail.split('/')) and not any(ord(c)<32 for c in path) and '\\' not in path and ':' not in path and path.lower().endswith('.gcode')

def thumbnail_path(value):
    if not isinstance(value,str) or 'humbnail/' not in value:return None
    tail=value.split('humbnail/',1)[1]
    if len(tail)>1024 or any(part in ('','..','.') for part in tail.split('/')) or any(ord(c)<32 for c in tail) or any(c in tail for c in ('\\',':','?','#')):return None
    if not tail.lower().endswith(('.png','.jpg','.jpeg')):return None
    return '/downloads/humbnail/'+quote(tail,safe='/')

def parse_listing(payload):
    records=[]
    if 'retGcodeFileInfo2' in payload:
        entries=payload['retGcodeFileInfo2']
        if not isinstance(entries,list):raise ValueError('Некорректный список файлов')
        for item in entries[:2000]:
            if isinstance(item,dict):
                records.append((item.get('path'),item.get('file_size'),item.get('create_time'),item.get('thumbnail')))
    elif 'retGcodeFileInfo' in payload:
        listing=payload['retGcodeFileInfo']
        if not isinstance(listing,dict) or not isinstance(listing.get('fileInfo'),str):raise ValueError('Некорректный список файлов')
        for entry in listing['fileInfo'].split(';')[:2000]:
            parts=entry.split(':')
            if len(parts)>=7:
                records.append((parts[0].rstrip('/')+'/'+parts[1],parts[2],parts[4],parts[6]))
    else:return None
    files={}
    for path,size,modified,thumbnail in records:
        if not safe_file_path(path):continue
        files[path]={'id':hashlib.sha256(path.encode()).hexdigest()[:32],'path':path,'name':PurePosixPath(path).name,
                     'size_bytes':integer(size),'modified':integer(modified),'thumbnail':thumbnail_path(thumbnail)}
    return sorted(files.values(),key=lambda item:-(item['modified'] or 0))

class FileCatalog:
    def __init__(self):
        self.files=[];self.directories=[];self.updated=0;self.revision='';self.event=asyncio.Event()
    def reset(self):
        self.files=[];self.directories=[];self.updated=0;self.revision='';self.event.clear()
    def ingest(self,payload):
        if not isinstance(payload,dict):return False
        files=parse_listing(payload)
        if files is None:return False
        self.files=files;self.directories=[];self.updated=time.monotonic()
        self.revision=hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.event.set();return True
    def get(self,key):
        return next((item for item in self.files if item['id']==key),None)
    def public(self):
        return [{**{key:item[key] for key in ('id','name','size_bytes','modified')},'relative_path':catalog_path(item),**({'read_only':item['_read_only']} if '_read_only' in item else {})} for item in self.files]
    def directory_entries(self,directory):
        prefix=directory+'/' if directory else ''
        names=set()
        for path in self.directories:
            if safe_directory(path) and path.startswith(prefix) and path!=directory:
                names.add(path[len(prefix):].split('/',1)[0])
        for item in self.files:
            path=catalog_path(item)
            if not path.startswith(prefix):continue
            tail=path[len(prefix):]
            if '/' in tail:names.add(tail.split('/',1)[0])
        return [{'name':name,'path':prefix+name} for name in sorted(names,key=str.casefold)]

class Thumbnails:
    def __init__(self):
        self.cache=OrderedDict()
        self.limit=asyncio.Semaphore(4)
    async def get(self,ip,path):
        async with self.limit:
            return await asyncio.wait_for(self._get(ip,path),10)
    async def _get(self,ip,path):
        if path!='/downloads/original/current_print_image.png' and not path.startswith('/downloads/humbnail/'):
            raise ValueError('Неизвестная миниатюра')
        key=(ip,path);cached=self.cache.get(key)
        if cached and time.monotonic()-cached[0]<30:
            self.cache.move_to_end(key);return cached[1:]
        async with httpx.AsyncClient(trust_env=False,timeout=5,follow_redirects=False) as client:
            async with client.stream('GET',f'http://{ip}{path}') as reply:
                reply.raise_for_status();body=bytearray()
                async for chunk in reply.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>2*1024*1024:raise ValueError('Миниатюра слишком большая')
        content=bytes(body)
        if content.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
        elif content.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
        else:raise ValueError('Принтер не вернул изображение')
        self.cache[key]=(time.monotonic(),content,mime);self.cache.move_to_end(key)
        while len(self.cache)>64:self.cache.popitem(last=False)
        return content,mime
