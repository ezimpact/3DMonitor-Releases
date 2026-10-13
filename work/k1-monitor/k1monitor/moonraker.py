"""Moonraker adapter. Startup performs only queries/subscriptions; never SETs."""
import asyncio
import hashlib
import json
import logging
import re
import time
from urllib.parse import quote

import httpx
from websockets.asyncio.client import connect

from .client import PrinterMonitor
from .files import FileCatalog
from .telemetry import number

LOG=logging.getLogger(__name__)

def relative_path(value):
    return (isinstance(value,str) and 0<len(value)<=1024 and not value.startswith('/')
            and not any(p in ('','..','.') for p in value.split('/'))
            and not any(ord(c)<32 or c in '\\:"\'\n\r' for c in value))

def object_name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_ -]{1,128}',value):
        raise ValueError('Некорректное имя объекта Klipper')
    return value

def macro_name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',value):
        raise ValueError('Некорректное имя макроса')
    return value

def moon_command(params,objects,settings):
    """Pure translation for validation/tests, including launch without executing it."""
    if 'opGcodeFile' in params:
        action,path=params['opGcodeFile'].split(':',1)
        if not relative_path(path) or not path.lower().endswith('.gcode'):raise ValueError('Некорректный путь G-code')
        if action=='deleteprt':return ('DELETE','/server/files/gcodes/'+quote(path,safe='/'),None)
        if action!='printprt':raise ValueError('Неизвестное действие файла')
        if params.get('enableSelfTest'):
            if 'bed_mesh' not in objects:raise ValueError('Проверка поверхности стола недоступна')
            return ('RPC','printer.gcode.script',{'script':'G28\nBED_MESH_CALIBRATE\nSDCARD_PRINT_FILE FILENAME="'+path+'"'})
        return ('POST','/printer/print/start',{'filename':path})
    if set(params)=={'pause'} and params['pause'] in (0,1):return ('POST','/printer/print/'+('pause' if params['pause'] else 'resume'),None)
    if params=={'stop':1}:return ('POST','/printer/print/cancel',None)
    script=None
    if set(params)=={'nozzleTempControl'}:
        value=params['nozzleTempControl'];limit=min(300,float(settings.get('extruder',{}).get('max_temp',301))-1)
        if type(value) is not int or not 0<=value<=limit:raise ValueError('Температура выше предела конфигурации сопла')
        script=f'SET_HEATER_TEMPERATURE HEATER=extruder TARGET={value}'
    elif set(params)=={'bedTempControl'}:
        value=params['bedTempControl'].get('val');limit=min(100,float(settings.get('heater_bed',{}).get('max_temp',101))-1)
        if type(value) is not int or not 0<=value<=limit:raise ValueError('Температура выше предела конфигурации стола')
        script=f'SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={value}'
    elif set(params)=={'setFeedratePct'}:
        value=params['setFeedratePct']
        if type(value) is not int or not 10<=value<=200:raise ValueError('Скорость: 10–200%')
        script=f'M220 S{value}'
    elif set(params)=={'lightSw'}:
        pin=next((p for p in ('output_pin LED_pin','output_pin light','output_pin caselight','output_pin led') if p in objects),None)
        if not pin or params['lightSw'] not in (0,1):raise ValueError('Управляемая подсветка не обнаружена')
        script='SET_PIN PIN='+object_name(pin.split(' ',1)[1])+' VALUE='+str(params['lightSw'])
    elif set(params)=={'excludeObjects'}:
        names=params['excludeObjects']
        if 'exclude_object' not in objects or not isinstance(names,list) or len(names)!=1:raise ValueError('Исключение объектов недоступно')
        name=names[0]
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,256}',name):raise ValueError('Имя модели не подходит для команды Klipper')
        script='EXCLUDE_OBJECT NAME='+name
    elif set(params)=={'gcodeCmd'}:script=params['gcodeCmd']
    if script is None:raise ValueError('Команда Moonraker не поддерживается')
    return ('POST','/printer/gcode/script',{'script':script})

class MoonrakerMonitor(PrinterMonitor):
    persistent_status = False
    protocol='moonraker'
    def __init__(self,host,firmware,*,api_port=7125,camera_path='',unload_macro='UNLOAD_FILAMENT',**kwargs):
        super().__init__(host,firmware,**kwargs)
        self.base_url=f'http://{host}:{api_port}';self.url=f'ws://{host}:{api_port}/websocket'
        self.camera_path=camera_path;self.unload_macro=unload_macro
        self.objects=set();self.settings={};self.raw_status={};self._client=None
        self._rpc_id=10;self._rpc_pending={};self._job_sequence=0;self._last_job=('','standby')
        self.api_port=api_port;self._last_refresh=0;self._klippy_state='startup'

    async def _get(self,path):
        response=await self._client.get(self.base_url+path);response.raise_for_status()
        if len(response.content)>4*1024*1024:raise ValueError('Слишком большой ответ Moonraker')
        return response.json()['result']

    def _ingest(self,updates):
        for key,value in updates.items():
            self.raw_status[key]={**self.raw_status.get(key,{}),**value} if isinstance(value,dict) else value
        s=self.raw_status;old_times=dict(self.state.field_times)
        stats=s.get('print_stats',{});web=s.get('webhooks',{});pause=s.get('pause_resume',{}).get('is_paused',False)
        name=stats.get('filename') or '';job=(name,stats.get('state','standby'))
        if name and (name!=self._last_job[0] or job[1]=='printing' and self._last_job[1] in ('complete','cancelled','error','standby')):self._job_sequence+=1
        self._last_job=job
        state=5 if pause or job[1]=='paused' else {'printing':1,'complete':2,'error':3,'cancelled':4}.get(job[1],0)
        sensors=[s.get(o,{}) for o in self.objects if o.startswith(('filament_switch_sensor ','filament_motion_sensor '))]
        runout=bool(sensors) and any(x.get('enabled',True) and x.get('filament_detected') is False for x in sensors)
        message=stats.get('message','') if job[1]=='error' else web.get('state_message','') if web.get('state') not in (None,'ready') else ''
        led=next((s.get(p,{}).get('value') for p in ('output_pin LED_pin','output_pin light','output_pin caselight','output_pin led') if p in self.objects),None)
        progress=s.get('display_status',{}).get('progress',s.get('virtual_sdcard',{}).get('progress'))
        elapsed=number(stats.get('print_duration'));remaining=None
        active=state in (1,5)
        if active and elapsed and progress is not None and .001<float(progress)<1:remaining=int(elapsed*(1-float(progress))/float(progress))
        chamber=next((s.get(o,{}).get('temperature') for o in self.objects if o.startswith(('temperature_sensor chamber','heater_generic chamber','temperature_sensor enclosure'))),None)
        payload={'model':'Klipper','modelVersion':'DWIN sw ver:'+self.state.configured_firmware+';',
            'state':state,'printFileName':name if active or state in (2,3,4) else '',
            'printId':str(self._job_sequence) if name else '', 'printProgress':float(progress)*100 if progress is not None else None,
            'printJobTime':int(elapsed) if elapsed is not None else None,'printLeftTime':remaining,
            'layer':stats.get('info',{}).get('current_layer'),'TotalLayer':stats.get('info',{}).get('total_layer'),
            'nozzleTemp':s.get('extruder',{}).get('temperature'),'targetNozzleTemp':s.get('extruder',{}).get('target'),
            'bedTemp0':s.get('heater_bed',{}).get('temperature'),'targetBedTemp0':s.get('heater_bed',{}).get('target'),
            'boxTemp':chamber,'curFeedratePct':100*float(s.get('gcode_move',{}).get('speed_factor',1)),
            'materialStatus':int(runout),'pause':int(pause),
            'err':{'errcode':-1 if message else 0,'value':message},
            'video':int(bool(self.camera_path)),'webrtcSupport':0,
            **({'lightSw':int(float(led)>0)} if led is not None else {}),
            'objects':s.get('exclude_object',{}).get('objects',[]),'excluded_objects':s.get('exclude_object',{}).get('excluded_objects',[]),'current_object':s.get('exclude_object',{}).get('current_object')}
        self.state.ingest(payload)
        # Only a real full query or a changed heater pair refreshes temperature freshness.
        for obj,attr,field in [('extruder','temperature','nozzleTemp'),('extruder','target','targetNozzleTemp'),('heater_bed','temperature','bedTemp0'),('heater_bed','target','targetBedTemp0')]:
            if attr not in updates.get(obj,{}):self.state.field_times[field]=old_times.get(field,-1e9)
        if not any(o in updates for o in self.objects if o.startswith(('filament_switch_sensor ','filament_motion_sensor '))):self.state.field_times['materialStatus']=old_times.get('materialStatus',-1e9)
        if 'print_stats' not in updates and 'pause_resume' not in updates:self.state.field_times['state']=old_times.get('state',-1e9)
        if 'pause_resume' not in updates:self.state.field_times['pause']=old_times.get('pause',-1e9)
        if self._command and ('print_stats' in updates or 'pause_resume' in updates):
            self._command['_ack_revision']=self.state.message_count;self._update_confirmation()

    async def _refresh(self):
        names=['webhooks','print_stats','virtual_sdcard','display_status','pause_resume','extruder','heater_bed','gcode_move','exclude_object']
        names += [o for o in self.objects if o.startswith(('filament_switch_sensor ','filament_motion_sensor ','output_pin ','temperature_sensor chamber','heater_generic chamber','temperature_sensor enclosure'))]
        query='&'.join(quote(o,safe='') for o in names if o in self.objects)
        result=await self._get('/printer/objects/query?'+query)
        self._ingest(result['status']);self._last_refresh=time.monotonic()

    def snapshot(self):
        s=super().snapshot();fresh=s['connection']['online'] and not s['connection']['stale']
        state=s['state_code'];ready=self.raw_status.get('webhooks',{}).get('state')=='ready'
        if ready and s['filament_runout'] and state in (1,5):s['status']='paused' if state==5 else 'printing'
        if ready and s['filament_runout'] and state in (0,2,4) and not self.raw_status.get('print_stats',{}).get('message'):
            s['status']={0:'idle',2:'completed',4:'stopped'}[state];s['error']={'code':0,'key':None,'message':'','active':False};s['error_code']=0
        s['printer']['protocol']='moonraker';s['printer']['api_port']=self.api_port
        s['camera']={'present':bool(self.camera_path),'webrtc':False,'path':self.camera_path}
        s['capabilities']={'objects':'exclude_object' in self.objects,'calibration':'bed_mesh' in self.objects,
            'auto_unload':('gcode_macro '+self.unload_macro in self.objects and any(o.startswith(('filament_switch_sensor ','filament_motion_sensor ')) for o in self.objects)),
            'calibration_label':'Проверка поверхности стола перед печатью',
            'camera':bool(self.camera_path),'protocol':'Moonraker / Klipper'}
        if not ready:
            for key in s['controls']:s['controls'][key]=False
        if ready and fresh and not (self._command and self._command['phase']=='pending'):
            s['controls'].update(pause=state==1 and 'pause_resume' in self.objects,resume=state==5 and 'pause_resume' in self.objects,stop=state in (1,5))
            s['controls']['start']=state in (0,2,3,4) and 'virtual_sdcard' in self.objects and self.raw_status.get('print_stats',{}).get('state') in ('standby','complete','cancelled')
        s['controls']['exclude']=s['controls']['exclude'] and s['capabilities']['objects']
        s['unload_eligible']=s['unload_eligible'] and s['capabilities']['auto_unload'] and ready
        s['auto_unload']['supported']=s['capabilities']['auto_unload']
        if not s['capabilities']['auto_unload']:s['auto_unload']['message']='Нужны датчик филамента и макрос '+self.unload_macro
        for kind,obj,limit in [('nozzle','extruder',300),('bed','heater_bed',100)]:
            max_value=number(self.settings.get(obj,{}).get('max_temp'))
            s['controls'][kind]=s['controls'][kind] and obj in self.objects and s['temperatures'].get(kind+'_target') is not None
            s.setdefault('temperature_limits',{})[kind]=min(limit,int(max_value)-1) if max_value is not None else limit
        return s

    def _update_confirmation(self):
        cmd=self._command
        if not cmd or cmd.get('phase')!='pending':return
        stats=self.raw_status.get('print_stats',{});paused=self.raw_status.get('pause_resume',{}).get('is_paused')
        matches={'pause':paused or stats.get('state')=='paused','resume':not paused and stats.get('state')=='printing',
            'stop':stats.get('state') in ('cancelled','complete','standby'),
            'start':stats.get('state')=='printing' and stats.get('filename')==cmd.get('_file')}
        if cmd.get('_ack_revision',-1)>cmd['_revision'] and matches.get(cmd['action']):cmd.update(phase='confirmed',message='Принтер подтвердил изменение состояния')
        if cmd['phase']=='pending' and time.monotonic()-cmd['_sent']>12:cmd.update(phase='unconfirmed',message='Команда отправлена; подтверждение ещё не получено. Проверьте состояние принтера.')

    def _unload_gcode(self):
        if 'gcode_macro '+self.unload_macro not in self.objects:raise ValueError('Макрос выгрузки недоступен')
        return 'SAVE_GCODE_STATE NAME=K1MONITOR_UNLOAD\nM83\n'+macro_name(self.unload_macro)+'\nM400\nRESTORE_GCODE_STATE NAME=K1MONITOR_UNLOAD'

    def _can_restore_unload_temperature(self,snapshot):
        # The configured printer macro owns its standby/cooling policy.
        return True

    def _unload_sent_message(self):
        return 'Команда выгрузки отправлена · печать остаётся на паузе'

    def file_path_valid(self,path):return relative_path(path) and path.lower().endswith('.gcode')
    def download_url(self,path):
        if not self.file_path_valid(path):raise ValueError('Некорректный путь файла')
        return self.base_url+'/server/files/gcodes/'+quote(path,safe='/')
    def upload_url(self,name):return self.base_url+'/server/files/upload'
    def upload_fields(self):return {'root':'gcodes','print':'false'}

    async def list_directory(self,directory=''):
        if directory and not relative_path(directory):raise ValueError('Некорректный путь папки')
        session,host=self.state.connection_count,self.state.host
        self.check_selection(session,host)
        info=await self._get('/server/files/directory?path='+quote('gcodes'+('/'+directory if directory else ''),safe='')+'&extended=false')
        self.check_selection(session,host)
        names={item.get('dirname') for item in info.get('dirs',[])[:2000] if isinstance(item,dict)
               and isinstance(item.get('dirname'),str) and '/' not in item['dirname'] and relative_path(item['dirname'])}
        return [{'name':name,'path':(directory+'/' if directory else '')+name} for name in sorted(names,key=str.casefold)]

    async def list_files(self,*,refresh=False):
        async with self._files_lock:
            self.check_selection(self.state.connection_count,self.state.host)
            if refresh or not self.files.updated or time.monotonic()-self.files.updated>10:
                entries=await self._get('/server/files/list?root=gcodes');files=[]
                for item in entries[:2000]:
                    path=item.get('path')
                    if not self.file_path_valid(path):continue
                    files.append({'id':hashlib.sha256(path.encode()).hexdigest()[:32],'path':path,'name':path.rsplit('/',1)[-1],
                        'size_bytes':item.get('size'),'modified':item.get('modified'),'thumbnail':None})
                # Metadata is lazy: only the requested thumbnail needs another GET.
                self.files.files=sorted(files,key=lambda x:-(x['modified'] or 0));self.files.updated=time.monotonic()
                self.files.revision=hashlib.sha256(json.dumps(self.files.files,sort_keys=True).encode()).hexdigest();self.files.event.set()
            return self.files

    async def thumbnail(self,path):
        if not self.file_path_valid(path):raise ValueError('Миниатюра отсутствует')
        meta=await self._get('/server/files/metadata?filename='+quote(path,safe=''))
        thumbs=meta.get('thumbnails',[])
        if not thumbs:raise ValueError('Миниатюра отсутствует')
        chosen=max(thumbs,key=lambda x:x.get('width',0));relative=chosen.get('relative_path')
        prefix=path.rsplit('/',1)[0]+'/' if '/' in path else ''
        if not relative_path(relative):raise ValueError('Некорректный путь миниатюры')
        reply=await self._client.get(self.base_url+'/server/files/gcodes/'+quote(prefix+relative,safe='/'));reply.raise_for_status()
        content=reply.content
        if len(content)>2*1024*1024:raise ValueError('Слишком большая миниатюра')
        if content.startswith(b'\x89PNG\r\n\x1a\n'):return content,'image/png'
        if content.startswith(b'\xff\xd8\xff'):return content,'image/jpeg'
        raise ValueError('Некорректная миниатюра')

    async def list_objects(self):
        self.check_selection(self.state.connection_count,self.state.host)
        if 'exclude_object' not in self.objects:raise ValueError('В Klipper не включён exclude_object')
        await self._refresh();return self.snapshot()

    async def _send(self,ws,message):
        data=json.loads(message);params=data.get('params',{})
        if data['method']=='get':
            if 'reqGcodeFile' in params:await self.list_files(refresh=True)
            else:await self._refresh()
            return
        method,path,body=moon_command(params,self.objects,self.settings)
        if method=='RPC':
            async with self._send_lock:
                self._rpc_id+=1;self._rpc_pending[self._rpc_id]=self._command
                await ws.send(json.dumps({'jsonrpc':'2.0','method':path,'params':body,'id':self._rpc_id}))
        else:
            reply=await self._client.request(method,self.base_url+path,json=body);reply.raise_for_status()

    async def _discover_camera(self):
        if self.camera_path:return
        # New Moonraker, then old Mainsail database layout. No configuration writes.
        try:
            cams=(await self._get('/server/webcams/list')).get('webcams',[])
        except (httpx.HTTPError,KeyError):
            try:
                value=(await self._get('/server/database/item?namespace=mainsail')).get('value',{})
                cams=value.get('webcams',value.get('webcam',[]))
                if isinstance(cams,dict):cams=list(cams.values())
            except (httpx.HTTPError,KeyError):cams=[]
        from .protocols import camera_path
        for cam in cams if isinstance(cams,list) else []:
            if not isinstance(cam,dict) or cam.get('enabled') is False:continue
            raw=cam.get('stream_url',cam.get('urlStream',''))
            if not raw:continue
            try:self.camera_path=camera_path(raw,self.state.host);break
            except ValueError:continue

    async def _receive(self,ws):
        async for raw in ws:
            data=json.loads(raw)
            method=data.get('method')
            if method=='notify_status_update':self._ingest(data['params'][0])
            elif method in ('notify_klippy_shutdown','notify_klippy_disconnected','notify_klippy_ready'):
                raise ConnectionError('Состояние Klipper изменилось; переподключаемся')
            elif 'id' in data:
                command=self._rpc_pending.pop(data['id'],None)
                if 'error' in data and command:
                    command.update(phase='unconfirmed',message='Moonraker отклонил команду: '+str(data['error'].get('message','Ошибка'))[:300])

    async def _run(self):
        delay=self.retry_min
        while True:
            receiver=None;started=time.monotonic()
            try:
                async with httpx.AsyncClient(trust_env=False,timeout=10,follow_redirects=False) as client:
                    self._client=client
                    info=await self._get('/printer/info');self.state.configured_firmware=info.get('software_version','unknown')
                    self.objects=set((await self._get('/printer/objects/list'))['objects'])
                    cfg=await self._get('/printer/objects/query?configfile');self.settings=cfg['status'].get('configfile',{}).get('config',{})
                    await self._discover_camera()
                    async with connect(self.url,proxy=None,ping_interval=20,ping_timeout=10,open_timeout=5,close_timeout=2,max_size=4*1024*1024) as ws:
                        self.state.begin_session();self.files.reset();self.raw_status={};self._ws=ws
                        receiver=asyncio.create_task(self._receive(ws))
                        names=[o for o in self.objects if o in ('webhooks','print_stats','virtual_sdcard','display_status','pause_resume','extruder','heater_bed','gcode_move','exclude_object') or o.startswith(('filament_switch_sensor ','filament_motion_sensor ','output_pin ','temperature_sensor chamber','heater_generic chamber'))]
                        await ws.send(json.dumps({'jsonrpc':'2.0','method':'printer.objects.subscribe','params':{'objects':dict.fromkeys(names)},'id':1}))
                        await self._refresh()
                        while True:
                            done,_=await asyncio.wait([receiver],timeout=self.poll_seconds)
                            if done:await receiver;raise ConnectionError('Moonraker закрыл соединение')
                            await self._refresh()
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.state.disconnected(str(exc));LOG.warning('Moonraker %s: %s',self.state.host,exc)
            finally:
                self._ws=None;self._client=None;self.state.transport_open=False
                self._rpc_pending.clear()
                if receiver:
                    receiver.cancel()
                    await asyncio.gather(receiver,return_exceptions=True)
                if self._command and self._command['phase']=='pending':self._command.update(phase='unconfirmed',message='Связь прервалась до подтверждения команды')
            if time.monotonic()-started>=10:delay=self.retry_min
            await asyncio.sleep(delay);delay=min(delay*2,20)
