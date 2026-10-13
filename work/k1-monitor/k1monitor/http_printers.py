"""Beta HTTP adapters. Monitoring uses GET only; controls are single-attempt."""
import asyncio
import hashlib
import json
import logging
import time
from urllib.parse import quote,urlsplit,unquote

import httpx
from .client import PrinterMonitor
from .credentials import request_options
from .moonraker import relative_path
from .telemetry import number

LOG=logging.getLogger(__name__)
TEXT_EXTENSIONS=('.gcode','.gco')


class UploadStream(httpx.AsyncByteStream):
    """Rewind the spool when Digest authentication repeats a rejected request."""
    def __init__(self,file):self.file=file
    async def __aiter__(self):
        self.file.seek(0)
        while block:=self.file.read(65536):yield block


def octo_command(params):
    """Pure translation. Never execute print starts in automated checks."""
    if 'opGcodeFile' in params:
        action,path=params['opGcodeFile'].split(':',1)
        if not relative_path(path) or not path.lower().endswith(TEXT_EXTENSIONS):raise ValueError('Некорректный путь G-code')
        url='/api/files/local/'+quote(path,safe='/')
        if action=='deleteprt':return 'DELETE',url,None
        if action!='printprt' or params.get('enableSelfTest'):raise ValueError('Отдельная калибровка OctoPrint не поддерживается')
        return 'POST',url,{'command':'select','print':True}
    if set(params)=={'pause'} and params['pause'] in (0,1):
        return 'POST','/api/job',{'command':'pause','action':'pause' if params['pause'] else 'resume'}
    if params=={'stop':1}:return 'POST','/api/job',{'command':'cancel'}
    if set(params)=={'nozzleTempControl'}:
        value=params['nozzleTempControl']
        if type(value) is int and 0<=value<=300:return 'POST','/api/printer/tool',{'command':'target','targets':{'tool0':value}}
    if set(params)=={'bedTempControl'}:
        value=params['bedTempControl'].get('val')
        if type(value) is int and 0<=value<=100:return 'POST','/api/printer/bed',{'command':'target','target':value}
    if set(params)=={'setFeedratePct'}:
        value=params['setFeedratePct']
        if type(value) is int and 50<=value<=200:return 'POST','/api/printer/printhead',{'command':'feedrate','factor':value}
    raise ValueError('Команда не поддерживается OctoPrint')


class HttpPrinterMonitor(PrinterMonitor):
    persistent_status = False
    protocol='http';label='HTTP'
    def __init__(self,host,firmware,*,api_port=80,camera_path='',credentials=None,**kwargs):
        super().__init__(host,firmware,**kwargs)
        self.api_port=api_port;self.base_url=f'http://{host}:{api_port}'
        self.camera_path=camera_path;self.credentials=credentials or {'mode':'none'}
        self._client=None;self.raw_state='';self.job={};self.info={}
        self._job_sequence=0;self._last_job=('',False);self.error_message=''
        self._refresh_lock=asyncio.Lock();self._last_elapsed=None
    def http_options(self):return request_options(self.credentials)
    async def _request(self,method,path,**kwargs):
        response=await self._client.request(method,self.base_url+path,**kwargs)
        response.raise_for_status();return response
    async def _get(self,path,*,optional=False):
        response=await self._request('GET',path,headers={'Accept':'application/json'})
        if response.status_code==204 and optional:return {}
        if len(response.content)>4*1024*1024:raise ValueError('Слишком большой ответ принтера')
        return response.json()
    def safe_url(self,value):
        if not isinstance(value,str) or any(ord(c)<32 for c in value) or '\\' in value:raise ValueError('Некорректный адрес файла')
        url=self.base_url+value if value.startswith('/') and not value.startswith('//') else value
        parts=urlsplit(url);base=urlsplit(self.base_url)
        if parts.scheme!=base.scheme or parts.hostname!=base.hostname or (parts.port or 80)!=(base.port or 80) or parts.username or parts.password or parts.fragment:
            raise ValueError('Адрес файла должен находиться на API этого принтера')
        decoded=unquote(parts.path)
        if '\\' in decoded or any(ord(c)<32 for c in decoded) or any(part in ('.','..') for part in decoded.split('/')):raise ValueError('Некорректный адрес файла')
        return url
    def file_path_valid(self,path):return relative_path(path) and path.lower().endswith(TEXT_EXTENSIONS)
    def _catalog(self,records):
        self.files.files=sorted(records,key=lambda x:-(x.get('modified') or 0))
        self.files.updated=time.monotonic()
        self.files.revision=hashlib.sha256(json.dumps(self.files.files,sort_keys=True).encode()).hexdigest()
        self.files.event.set();return self.files
    def _payload(self,code,name,*,progress=None,elapsed=None,remaining=None,temps=None,speed=None,job_id=None):
        active=code in (1,5,6,7,8)
        current=(name,active)
        if active and (not self._last_job[1] or self._last_job[0]!=name or elapsed is not None and self._last_elapsed is not None and elapsed+2<self._last_elapsed):self._job_sequence+=1
        self._last_job=current
        self._last_elapsed=elapsed
        payload={'model':self.label,'state':code,'printFileName':name if code!=0 else '',
            'printId':str(job_id) if job_id is not None else str(self._job_sequence) if active else '',
            'printProgress':progress if code!=0 else None,'printJobTime':elapsed,'printLeftTime':remaining,
            'pause':int(code==5),'err':{'errcode':-1 if self.error_message else 0,'value':self.error_message},
            'video':int(bool(self.camera_path)),'webrtcSupport':0,**(temps or {})}
        payload['curFeedratePct']=speed
        # Missing values must clear previous readings, never refresh stale values.
        for field in ('nozzleTemp','targetNozzleTemp','bedTemp0','targetBedTemp0','boxTemp'):payload.setdefault(field,None)
        self.state.ingest(payload)
        if self._command and self._command.get('_http_submitted'):
            self._command['_ack_revision']=self.state.message_count;self._update_confirmation()
    def snapshot(self):
        snap=super().snapshot()
        snap['printer'].update(protocol=self.protocol,api_port=self.api_port)
        snap['authentication']={'configured':self.credentials.get('mode')!='none','mode':self.credentials.get('mode','none'),'username':self.credentials.get('username','')}
        snap['camera']={'present':bool(self.camera_path),'webrtc':False,'path':self.camera_path}
        snap['capabilities']={'protocol':self.label+' · beta','objects':False,'calibration':False,'auto_unload':False,
            'calibration_label':'Калибровка выполняется стартовым G-code принтера','camera':bool(self.camera_path)}
        snap['controls'].update(exclude=False,speed=False,nozzle=False,bed=False)
        snap['auto_unload'].update(supported=False,enabled=False,message='Автовыгрузка недоступна через этот API в тестовой версии')
        snap['unload_eligible']=False
        return snap
    async def start_print(self,**kwargs):
        if kwargs.get('calibrate',True):raise ValueError('Используйте запуск без отдельной калибровки; её выполняет стартовый G-code')
        return await super().start_print(**kwargs)
    async def control(self,*args,expected_job=None,**kwargs):
        if expected_job is None:raise ValueError('Нужен актуальный идентификатор задания. Обновите страницу.')
        return await super().control(*args,expected_job=expected_job,**kwargs)
    async def _refresh(self):
        async with self._refresh_lock:await self._read_status()
    async def list_objects(self):raise ValueError('Исключение моделей не поддерживается этим адаптером')
    async def _send(self,ws,message):
        data=json.loads(message)
        if data['method']=='get':
            if 'reqGcodeFile' in data.get('params',{}):await self.list_files(refresh=True)
            else:await self._refresh()
            return
        # Re-query under the caller's command lock before any mutation. Keep
        # the selected printer job identity pinned, especially for Prusa job IDs.
        before=self.snapshot();await self._refresh();after=self.snapshot()
        if before['connection']['sessions']!=after['connection']['sessions'] or before['print']['job_id']!=after['print']['job_id'] or before['print']['file']!=after['print']['file']:
            raise ValueError('Задание изменилось до отправки команды')
        params=data['params']
        allowed=self.raw_state in (('Operational','Printing','Paused') if self.protocol=='octoprint' else ('IDLE','READY','PRINTING','PAUSED','FINISHED','STOPPED'))
        if not allowed or after['error']['active']:raise ValueError('Команда недоступна в текущем состоянии')
        if 'pause' in params:
            wanted=('Printing','PRINTING') if params['pause'] else ('Paused','PAUSED')
            if self.raw_state not in wanted:raise ValueError('Состояние задания изменилось')
        if 'opGcodeFile' in params:
            action,file=params['opGcodeFile'].split(':',1)
            entry=next((item for item in self.files.files if item['path']==file),{})
            if entry.get('_read_only'):raise ValueError('Файл доступен только для чтения')
            if action=='printprt' and self.raw_state not in ('Operational','IDLE','READY','FINISHED','STOPPED'):raise ValueError('Принтер занят')
            if action=='deleteprt' and after['status'] not in ('idle','completed','stopped') and after['print']['file']==file:raise ValueError('Нельзя удалить текущее задание')
        method,path,body=self.translate(params)
        async with self._send_lock:
            await self._request(method,path,**({'json':body} if body is not None else {}))
            if self._command and self._command['phase']=='pending':self._command['_http_submitted']=True
    async def _run(self):
        delay=self.retry_min
        while True:
            try:
                async with httpx.AsyncClient(trust_env=False,timeout=10,follow_redirects=False,**self.http_options()) as client:
                    self._client=client;await self._initialize()
                    self.state.begin_session();self.files.reset();self._ws=self
                    await self._refresh()
                    while True:await asyncio.sleep(self.poll_seconds);await self._refresh()
            except asyncio.CancelledError:raise
            except Exception as exc:
                if isinstance(exc,httpx.HTTPStatusError):
                    message='Ошибка авторизации: проверьте API-ключ или логин/пароль' if exc.response.status_code in (401,403) else f'API вернул HTTP {exc.response.status_code}'
                elif isinstance(exc,(ValueError,KeyError,TypeError)):message='Ответ API не соответствует выбранному протоколу'
                else:message='Связь с HTTP API принтера прервана'
                self.state.disconnected(message);LOG.warning('%s %s: %s',self.label,self.state.host,message)
            finally:
                self._client=None;self._ws=None;self.state.transport_open=False
                if self._command and self._command['phase']=='pending':self._command.update(phase='unconfirmed',message='Связь прервалась до подтверждения; автоматического повтора нет')
            await asyncio.sleep(delay);delay=min(delay*2,20)


class OctoPrintMonitor(HttpPrinterMonitor):
    protocol='octoprint';label='OctoPrint'
    async def _initialize(self):
        version=await self._get('/api/version')
        if not str(version.get('text','')).startswith('OctoPrint'):raise ValueError('Выберите соответствующий протокол сервера')
        self.state.configured_firmware=str(version.get('server','unknown'))[:128]
    async def _read_status(self):
        previous=self.raw_state
        job=await self._get('/api/job');self.job=job
        self.raw_state=job.get('state','Unknown')
        temperatures={}
        if self.raw_state not in ('Offline','Offline after error','Opening serial connection','Closed','Error'):
            printer=await self._get('/api/printer')
            temp=printer.get('temperature',{})
            temperatures={'nozzleTemp':temp.get('tool0',{}).get('actual'),'targetNozzleTemp':temp.get('tool0',{}).get('target'),
                          'bedTemp0':temp.get('bed',{}).get('actual'),'targetBedTemp0':temp.get('bed',{}).get('target'),
                          'boxTemp':temp.get('chamber',{}).get('actual')}
        codes={'Operational':0,'Printing':1,'Printing from SD':1,'Paused':5,'Pausing':6,'Cancelling':7,'Resuming':8,'Finishing':7}
        self.error_message=str(job.get('error') or '')[:500]
        if self.raw_state not in codes and not self.error_message:self.error_message='OctoPrint: '+self.raw_state
        progress=job.get('progress',{});file=job.get('job',{}).get('file',{})
        name=file.get('path') or file.get('name') or '';code=codes.get(self.raw_state,3)
        active=self.raw_state in ('Printing','Printing from SD','Paused','Pausing','Resuming','Finishing','Cancelling')
        if active and name and (not self._last_job[1] or self._last_job[0]!=name or number(progress.get('printTime')) is not None and self._last_elapsed is not None and progress['printTime']+2<self._last_elapsed):
            # Capture the previous result before this job ends. Comparing native
            # dates avoids assuming that the printer and Windows clocks agree.
            self._octo_history=None
            origin=file.get('origin','local')
            if origin in ('local','sdcard') and self.file_path_valid(name):
                path='/api/files/'+origin+'/'+quote(name,safe='/')
                try:
                    metadata=await self._get(path)
                    last=metadata.get('prints',{}).get('last',{}) or {}
                    self._octo_history=(name,path,last.get('date'))
                except (httpx.HTTPError,ValueError,KeyError,TypeError):pass
        if self.raw_state=='Operational':
            if previous in ('Cancelling','Finishing') or number(progress.get('completion'))==100:
                self._history_result=(4 if previous=='Cancelling' else 2,name or self._last_job[0])
            elif self._last_job[1] and getattr(self,'_octo_history',None):
                old_name,path,old_date=self._octo_history
                try:
                    metadata=await self._get(path);last=metadata.get('prints',{}).get('last',{}) or {}
                    date=number(last.get('date'))
                    if date is not None and date>0 and date!=old_date and last.get('success') is True:
                        self._history_result=(2,old_name)
                except (httpx.HTTPError,ValueError,KeyError,TypeError):pass
            result=getattr(self,'_history_result',None)
            if result and (not name or name==result[1]):code,name=result
            elif result:self._history_result=None
        elif self.raw_state in ('Printing','Printing from SD','Paused'):self._history_result=None
        self._payload(code,name,progress=progress.get('completion'),
            elapsed=progress.get('printTime'),remaining=progress.get('printTimeLeft'),temps=temperatures)
    def snapshot(self):
        snap=super().snapshot();ready=snap['connection']['online'] and not snap['connection']['stale'] and not snap['error']['active'] and self.raw_state in ('Operational','Printing','Paused')
        snap['print']['origin']=self.job.get('job',{}).get('file',{}).get('origin')
        if self.raw_state not in ('Operational','Printing','Paused'):
            snap['controls']={key:False for key in snap['controls']}
        snap['controls']['start']=snap['controls']['start'] and self.raw_state=='Operational'
        snap['controls']['nozzle']=ready and snap['temperatures']['nozzle_target'] is not None and not (self._command and self._command['phase']=='pending')
        snap['controls']['bed']=ready and snap['temperatures']['bed_target'] is not None and not (self._command and self._command['phase']=='pending')
        snap['controls']['speed']=ready and self.raw_state in ('Printing','Paused') and not (self._command and self._command['phase']=='pending')
        snap['speed_limits']={'min':50,'max':200}
        return snap
    def translate(self,params):return octo_command(params)
    def download_url(self,path):
        entry=next((item for item in self.files.files if item['path']==path),None)
        return self.safe_url(entry.get('_download') if entry else '/downloads/files/local/'+quote(path,safe='/'))
    def upload_url(self,name):return self.base_url+'/api/files/local'
    def upload_fields(self):return {'select':'false','print':'false'}
    async def list_files(self,*,refresh=False):
        async with self._files_lock:
            self.check_selection(self.state.connection_count,self.state.host)
            if refresh or not self.files.updated or time.monotonic()-self.files.updated>10:
                data=await self._get('/api/files/local?recursive=true');records=[];directories=[]
                def visit(items,prefix='',depth=0):
                    if depth>16:raise ValueError('Слишком глубокий каталог')
                    for item in items:
                        if len(records)>=2000 or len(directories)>=2000:return
                        path=item.get('path') or prefix+item.get('name','')
                        if item.get('type')=='folder' and relative_path(path):
                            directories.append(path);visit(item.get('children',[]),path+'/',depth+1)
                        elif self.file_path_valid(path):
                            records.append({'id':hashlib.sha256(path.encode()).hexdigest()[:32],'path':path,'name':item.get('display') or path.rsplit('/',1)[-1],
                                'size_bytes':item.get('size'),'modified':item.get('date'),'thumbnail':None,
                                '_download':item.get('refs',{}).get('download') or '/downloads/files/local/'+quote(path,safe='/')})
                visit(data.get('files',[]));self._catalog(records);self.files.directories=directories
            return self.files
    async def thumbnail(self,path):raise ValueError('Миниатюры OctoPrint требуют отдельного плагина; в beta недоступны')


class PrusaLinkMonitor(HttpPrinterMonitor):
    protocol='prusalink';label='PrusaLink'
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.storage=None;self.storages=[];self._job_id=None;self.put_upload=False
    async def _initialize(self):
        version=await self._get('/api/version')
        if 'Prusa' not in str(version.get('text','')):raise ValueError('Выбранный сервер не является PrusaLink')
        self.info=version;self.put_upload=version.get('capabilities',{}).get('upload-by-put') is True
        self.state.configured_firmware=str(version.get('firmware',version.get('server','unknown')))[:128]
        storage=await self._get('/api/v1/storage');self.storages=storage.get('storage_list',[])
        self.storages=[s for s in self.storages if s.get('available',True) and isinstance(s.get('path'),str) and relative_path(s['path'].strip('/'))]
        writable=next((s for s in self.storages if not s.get('read_only',False)),None)
        self.storage=(writable or next(iter(self.storages),{})).get('path')
    async def _read_status(self):
        data=await self._get('/api/v1/status');printer=data['printer'];self.job=await self._get('/api/v1/job',optional=True)
        self.raw_state=printer['state'];self._job_id=self.job.get('id')
        codes={'IDLE':0,'READY':0,'BUSY':8,'PRINTING':1,'PAUSED':5,'FINISHED':2,'STOPPED':4,'ERROR':3,'ATTENTION':3}
        status=printer.get('status_printer',{})
        self.error_message=str(status.get('message') or self.raw_state)[:500] if self.raw_state in ('ERROR','ATTENTION') or self.raw_state not in codes else ''
        file=self.job.get('file',{});name=file.get('name') or '';folder=file.get('path','')
        path=(folder if folder.endswith('/'+name) else folder.rstrip('/')+'/'+name).lstrip('/') if name else ''
        self._payload(codes.get(self.raw_state,3),path,progress=self.job.get('progress',data.get('job',{}).get('progress')),
            elapsed=self.job.get('time_printing'),remaining=self.job.get('time_remaining'),job_id=self._job_id,
            temps={'nozzleTemp':printer.get('temp_nozzle'),'targetNozzleTemp':printer.get('target_nozzle'),
                   'bedTemp0':printer.get('temp_bed'),'targetBedTemp0':printer.get('target_bed')},speed=printer.get('speed'))
    def snapshot(self):
        snap=super().snapshot()
        snap['controls']['start']=snap['controls']['start'] and self.raw_state in ('IDLE','READY','FINISHED','STOPPED') and self.job.get('state') not in ('PRINTING','PAUSED')
        for key in ('pause','resume','stop'):snap['controls'][key]=snap['controls'][key] and type(self._job_id) is int
        if self.raw_state in ('BUSY','ATTENTION','ERROR'):
            snap['controls']={key:False for key in snap['controls']}
        return snap
    def translate(self,params):
        if 'opGcodeFile' in params:
            action,path=params['opGcodeFile'].split(':',1)
            if not self.file_path_valid(path):raise ValueError('Некорректный путь G-code')
            url='/api/v1/files/'+quote(path,safe='/')
            if action=='deleteprt':return 'DELETE',url,None
            if action=='printprt' and not params.get('enableSelfTest'):return 'POST',url,None
            raise ValueError('Отдельная калибровка PrusaLink недоступна')
        if type(self._job_id) is not int or self._job_id<0:raise ValueError('Нет актуального идентификатора задания PrusaLink')
        url='/api/v1/job/'+str(self._job_id)
        if set(params)=={'pause'} and params['pause'] in (0,1):return 'PUT',url+('/pause' if params['pause'] else '/resume'),None
        if params=={'stop':1}:return 'DELETE',url,None
        raise ValueError('Управление температурой/скоростью через PrusaLink v1 не поддерживается')
    def download_url(self,path):
        entry=next((item for item in self.files.files if item['path']==path),None)
        if not entry or not entry.get('_download'):raise ValueError('API не предоставил ссылку скачивания')
        return self.safe_url(entry['_download'])
    async def list_files(self,*,refresh=False):
        async with self._files_lock:
            self.check_selection(self.state.connection_count,self.state.host)
            if refresh or not self.files.updated or time.monotonic()-self.files.updated>10:
                records=[];folders=0;directories=[]
                async def visit(path,depth=0,read_only=False):
                    nonlocal folders
                    folders+=1
                    if depth>16 or folders>128:raise ValueError('Каталог слишком большой')
                    directories.append(path.strip('/'))
                    data=await self._get('/api/v1/files/'+quote(path.strip('/'),safe='/'))
                    for item in data.get('children',[]):
                        if len(records)>=2000:return
                        name=item.get('name','');child=path.strip('/')+'/'+name
                        if not relative_path(child):continue
                        if item.get('type')=='FOLDER':await visit(child,depth+1,read_only or item.get('read_only',False))
                        elif self.file_path_valid(child):
                            refs=item.get('refs',{})
                            records.append({'id':hashlib.sha256(child.encode()).hexdigest()[:32],'path':child,'name':item.get('display_name') or name,
                                'size_bytes':item.get('size'),'modified':item.get('m_timestamp'),'thumbnail':refs.get('thumbnail') or refs.get('icon'),
                                '_download':refs.get('download'),'_read_only':read_only or item.get('read_only',False)})
                for storage in self.storages:await visit(storage['path'],read_only=storage.get('read_only',False))
                self._catalog(records);self.files.directories=directories
            return self.files
    async def upload_file(self,client,name,file,size):
        if not self.storage or any(s.get('read_only',False) for s in self.storages if s['path']==self.storage):raise ValueError('Нет доступного хранилища для загрузки')
        if self.put_upload:
            if self.credentials.get('mode')=='digest':
                # Authenticate with a harmless read before transmitting the file.
                (await client.get(self.base_url+'/api/version')).raise_for_status()
            reply=await client.put(self.base_url+'/api/v1/files/'+quote(self.storage.strip('/')+'/'+name,safe='/'),content=UploadStream(file),
                headers={'Content-Type':'application/octet-stream','Content-Length':str(size),'Print-After-Upload':'?0','Overwrite':'?0'})
        else:
            reply=await client.post(self.base_url+'/api/files/'+quote(self.storage.strip('/'),safe=''),files={'file':(name,file,'application/octet-stream')},data={'print':'false','select':'false'})
        reply.raise_for_status()
    async def thumbnail(self,path):
        entry=next((item for item in self.files.files if item['path']==path),None)
        if not entry:
            await self.list_files();entry=next((item for item in self.files.files if item['path']==path),None)
        if not entry or not entry.get('thumbnail'):raise ValueError('Миниатюра отсутствует')
        content=bytearray()
        async with self._client.stream('GET',self.safe_url(entry['thumbnail'])) as response:
            response.raise_for_status()
            async for block in response.aiter_bytes():
                content.extend(block)
                if len(content)>2*1024*1024:raise ValueError('Миниатюра слишком большая')
        content=bytes(content)
        if content.startswith(b'\x89PNG\r\n\x1a\n'):return content,'image/png'
        if content.startswith(b'\xff\xd8\xff'):return content,'image/jpeg'
        raise ValueError('API не вернул изображение')
