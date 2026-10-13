"""Durable per-printer print history and conservative telemetry transitions."""
import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import time
import uuid
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

from .notifications import display_filename

LOG=logging.getLogger(__name__)
ACTIVE={'printing','paused','pausing','stopping','restoring'}
TERMINAL={'completed','stopped','failed'}

def number(value):
    return float(value) if isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None

def remaining_time(value):
    value=number(value)
    return value if value is not None and value>0 else None

def endpoint(printer):
    return '|'.join(str(printer.get(k,'')) for k in ('protocol','ip','api_port'))

def same_endpoint(left,right):
    # Old Creality registry entries omit the default protocol/port. Telemetry
    # supplies them. Compare their meaning without rewriting history keys/IDs.
    def canonical(value):
        parts=value.split('|')
        if len(parts)!=3:return value
        protocol,ip,port=parts;protocol=protocol or 'creality'
        defaults={'creality':'9999','moonraker':'7125','octoprint':'80','prusalink':'80'}
        return '|'.join((protocol,ip,port or defaults.get(protocol,'')))
    return canonical(left)==canonical(right)

class ActivityStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, printer TEXT NOT NULL, endpoint TEXT NOT NULL, native TEXT, started REAL, updated REAL NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS native_job ON jobs(printer,endpoint,native) WHERE native IS NOT NULL')
            db.execute('CREATE INDEX IF NOT EXISTS jobs_printer ON jobs(printer,started DESC)')
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, created REAL, payload TEXT, windows INTEGER, telegram INTEGER, attempts INTEGER DEFAULT 0, retry REAL DEFAULT 0)')
    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.path,timeout=5)
        try:
            with connection:yield connection
        finally:connection.close()
    def save(self,record):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?)',(record['id'],record['printer_id'],record['endpoint'],record.get('native_id'),record.get('started'),record['updated'],record['status'],json.dumps(record,ensure_ascii=False)))
    def active(self):
        with self.db() as db:return [json.loads(row[0]) for row in db.execute("SELECT payload FROM jobs WHERE status IN ('printing','paused','pausing','stopping','restoring')")]
    def record(self,printer,record_id):
        with self.db() as db:
            row=db.execute('SELECT payload FROM jobs WHERE printer=? AND id=?',(printer,record_id)).fetchone()
        return json.loads(row[0]) if row else None
    def list(self,printer,offset=0,limit=50):
        with self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM jobs WHERE printer=?',(printer,)).fetchone()[0]
            rows=db.execute('SELECT payload FROM jobs WHERE printer=? ORDER BY COALESCE(started,updated) DESC,id DESC LIMIT ? OFFSET ?',(printer,limit,offset)).fetchall()
        return {'total':total,'offset':offset,'items':[json.loads(row[0]) for row in rows]}
    def imported(self,record):
        with self.db() as db:
            existing=db.execute('SELECT id FROM jobs WHERE printer=? AND endpoint=? AND native=?',(record['printer_id'],record['endpoint'],record['native_id'])).fetchone()
            if existing:record['id']=existing[0]
            else:
                # Merge only a matching file with a reliable start estimate, never by basename.
                match=db.execute('SELECT id,payload FROM jobs WHERE printer=? AND endpoint=? AND native IS NULL AND ABS(started-?)<=15',(record['printer_id'],record['endpoint'],record['started'])).fetchall()
                match=[row for row in match if json.loads(row[1]).get('file')==record['file']]
                if len(match)==1:record['id']=match[0][0]
                elif record.get('last_printed'):
                    stamp=record['last_printed']
                    matches=db.execute("SELECT id,payload FROM jobs WHERE printer=? AND endpoint=? AND native IS NULL AND status IN ('completed','stopped','failed','unknown') AND started<=? AND updated>=?",(record['printer_id'],record['endpoint'],stamp+15,stamp-15)).fetchall()
                    matches=[row for row in matches if json.loads(row[1]).get('file')==record['file']]
                    if len(matches)==1:
                        record['id']=matches[0][0];local=json.loads(matches[0][1]);record.update(started=local.get('started'),ended=local.get('ended'))
        self.save(record)
    def enqueue(self,event,windows,telegram):
        with self.db() as db:db.execute('INSERT OR IGNORE INTO events(id,created,payload,windows,telegram) VALUES (?,?,?,?,?)',(event['id'],event['created'],json.dumps(event,ensure_ascii=False),int(windows),int(telegram)))
    def pending(self,now):
        with self.db() as db:
            db.execute('DELETE FROM events WHERE created<?',(now-86400,))
            db.execute('UPDATE events SET windows=0,telegram=0 WHERE created<?',(now-900,))
            return [dict(id=row[0],event=json.loads(row[1]),windows=row[2],telegram=row[3],attempts=row[4]) for row in db.execute('SELECT id,payload,windows,telegram,attempts FROM events WHERE (windows=1 OR telegram=1) AND retry<=? ORDER BY created LIMIT 10',(now,))]
    def delivery(self,event_id,windows=None,telegram=None,retry=None):
        with self.db() as db:
            if windows is not None:db.execute('UPDATE events SET windows=? WHERE id=?',(int(windows),event_id))
            if telegram is not None:db.execute('UPDATE events SET telegram=? WHERE id=?',(int(telegram),event_id))
            if retry is not None:db.execute('UPDATE events SET retry=?,attempts=attempts+1 WHERE id=?',(retry,event_id))

class ActivityTracker:
    def __init__(self,store,clock=time.time):
        self.store=store;self.clock=clock;self.previous={};self.running={r['printer_id']:r for r in store.active()};self.last_saved={};self.observed=set()
    def update(self,items):
        now=self.clock();events=[]
        for item in items:
            p=item['printer'];pid=p['id'];address=endpoint(p);previous=self.previous.get(pid);job=self.running.get(pid)
            if previous and not same_endpoint(previous['endpoint'],address):
                if job:self.finish(job,'unknown',now);self.running.pop(pid,None)
                previous=None;self.previous.pop(pid,None);self.observed.discard(pid);job=None
            if job and not same_endpoint(job['endpoint'],address):self.finish(job,'unknown',now);self.running.pop(pid,None);job=None
            online=item.get('connection',{}).get('online') and not item.get('connection',{}).get('stale')
            def emit(kind):
                events.append({'id':uuid.uuid4().hex,'kind':kind,'created':now,'started':(job or {}).get('started'),'printer_id':pid,'name':p['name'],'file':item.get('print',{}).get('file') or (job or {}).get('file'),'remaining':remaining_time(item.get('print',{}).get('remaining_seconds')),'paused':item['status']=='paused','error':item.get('error',{}),'runout':item.get('filament_runout',False)})
            # Disconnects neither notify nor finish an active print history entry.
            if not online:continue
            status=item['status'];details=item.get('print',{});file=details.get('file');elapsed=number(details.get('elapsed_seconds'))
            if status=='error':status={5:'paused',2:'completed',3:'failed',4:'stopped'}.get(item.get('state_code'),status)
            active=status in ACTIVE and bool(file)
            # A new filename or a large elapsed reset is a different job. Job IDs
            # on some adapters are session-local, so cannot identify restarts.
            changed=job and active and (file!=job['file'] or elapsed is not None and job.get('elapsed') is not None and elapsed+60<job['elapsed'])
            if changed:self.finish(job,'unknown',now);self.running.pop(pid,None);job=None
            if active:
                self.observed.add(pid)
                if not job:
                    job={'id':uuid.uuid4().hex,'printer_id':pid,'endpoint':address,'file':file,'file_origin':details.get('origin'),'started':now-(elapsed or 0),'ended':None,'elapsed':elapsed,'status':status,'source':'monitor','partial':previous is None,'updated':now,'progress':details.get('progress_percent')}
                    self.running[pid]=job
                    if previous is not None:emit('started')
                elif previous and status=='paused' and previous['status']!='paused':emit('paused')
                elif previous and status=='printing' and previous['status']=='paused':emit('resumed')
                job.update(status=status,elapsed=elapsed,updated=now,progress=details.get('progress_percent'))
                if now-self.last_saved.get(pid,0)>=15 or previous is None or previous['status']!=status:self.store.save(job);self.last_saved[pid]=now
            elif job and status in TERMINAL:
                # Error UI can mask terminal state. Confirm the explicit terminal
                # status twice before recording it; disconnect never means success.
                if previous and previous['status']==status:
                    job.update(elapsed=elapsed if elapsed is not None else job.get('elapsed'),progress=details.get('progress_percent'))
                    self.finish(job,status,now);self.running.pop(pid,None)
                    if pid in self.observed:emit({'completed':'completed','stopped':'cancelled','failed':'failed'}[status])
                    self.observed.discard(pid)
            elif job and status=='idle' and previous and previous['status']=='idle':self.finish(job,'unknown',now);self.running.pop(pid,None)
            error=item.get('error',{});signature=json.dumps([error.get('code'),error.get('key'),error.get('message')],ensure_ascii=False) if error.get('active') else ''
            runout=bool(item.get('filament_runout'))
            if previous:
                if runout and not previous['runout'] and (job or active):emit('runout')
                elif signature and signature!=previous['error'] and not runout:emit('error')
            self.previous[pid]={'endpoint':address,'status':status,'error':signature,'runout':runout}
        return events
    def finish(self,job,status,now):job.update(status=status,ended=now,updated=now);self.store.save(job)
    def flush(self):
        for job in self.running.values():self.store.save(job)

def native_record(printer,row,source):
    if source=='octoprint':
        last=row.get('prints',{}).get('last',{});stamp=number(last.get('date'));file=row.get('path') or row.get('name')
        if stamp is None or stamp<946684800 or not isinstance(file,str) or not file or type(last.get('success')) is not bool:return None
        key='octoprint:'+file+':'+str(stamp)
        return {'id':hashlib.sha256((printer['id']+endpoint(printer)+key).encode()).hexdigest(),'printer_id':printer['id'],'endpoint':endpoint(printer),'native_id':key,'file':file[:1024],'file_origin':row.get('origin'),'started':None,'ended':None,'last_printed':stamp,'elapsed':number(last.get('printTime')),'status':'completed' if last['success'] else 'failed','source':'printer','partial':True,'updated':stamp,'progress':100 if last['success'] else None}
    started=number(row.get('start_time' if source=='moonraker' else 'starttime'))
    native=row.get('job_id' if source=='moonraker' else 'id');file=row.get('filename');duration=number(row.get('total_duration' if source=='moonraker' else 'usagetime'))
    if started is None or started<946684800 or not isinstance(file,str) or not file or native is None:return None
    if source=='moonraker':
        state=row.get('status');status={'completed':'completed','cancelled':'stopped','error':'failed','klippy_shutdown':'failed','klippy_disconnect':'unknown','interrupted':'unknown'}.get(state)
        if status is None:return None
        ended=number(row.get('end_time'))
    else:status='completed' if row.get('printfinish')==1 else 'unknown';ended=started+duration if duration is not None else None
    if file.startswith('/usr/data/printer_data/gcodes/'):file=file[len('/usr/data/printer_data/gcodes/'):]
    key=str(native)[:128]
    return {'id':hashlib.sha256((printer['id']+endpoint(printer)+source+key).encode()).hexdigest(),'printer_id':printer['id'],'endpoint':endpoint(printer),'native_id':source+':'+key,'file':file[:1024],'started':started,'ended':ended,'elapsed':number(row.get('print_duration')) if source=='moonraker' else duration,'status':status,'source':'printer','partial':False,'updated':time.time(),'progress':100 if status=='completed' else None}

async def fetch_native(monitor,protocol):
    if protocol=='octoprint':
        data=await monitor._get('/api/files?recursive=true');records=[]
        def visit(items,depth=0):
            if depth>16:return
            for item in items:
                if len(records)>=5000:return
                if not isinstance(item,dict):continue
                if item.get('type')=='folder':visit(item.get('children',[]),depth+1)
                elif item.get('prints',{}).get('last'):records.append(item)
        visit(data.get('files',[]));return records
    if protocol=='moonraker':
        jobs=[]
        async with httpx.AsyncClient(trust_env=False,timeout=10,follow_redirects=False) as client:
            for start in range(0,5000,200):
                reply=await client.get(monitor.base_url+'/server/history/list',params={'limit':200,'start':start,'order':'desc'});reply.raise_for_status();batch=reply.json()['result']['jobs']
                if not isinstance(batch,list):raise ValueError('Некорректная история принтера')
                jobs.extend(batch)
                if len(batch)<200:break
        return jobs
    if protocol=='creality':
        # Official Creality Print LAN frontend: method=get, reqHistory=1.
        async with asyncio.timeout(10):
            async with connect(monitor.url,subprotocols=['wsslicer'],max_size=16*1024*1024,open_timeout=5,close_timeout=1) as socket:
                await socket.send(json.dumps({'method':'get','params':{'reqHistory':1}}))
                async for message in socket:
                    data=json.loads(message)
                    if isinstance(data,dict):
                        for candidate in [data,data.get('result'),data.get('params'),data.get('data')]:
                            if isinstance(candidate,dict) and isinstance(candidate.get('historyList'),list):return candidate['historyList'][:5000]
    return []

class ActivityService:
    def __init__(self,manager,notifications,relay=None):
        self.manager=manager;self.notifications=notifications
        self.store=notifications.store;self.tracker=ActivityTracker(self.store);self.tasks=[];self.import_status={};self.locks={};self.relay=relay
        notifications.status_provider=self.status_reports;notifications.photo_provider=self.status_photo
        notifications.light_provider=self.set_lights
    async def start(self):
        await self.notifications.start()
        self.tasks=[asyncio.create_task(self.observe()),asyncio.create_task(self.deliver()),asyncio.create_task(self.sync())]
    def status_reports(self):
        t=getattr(self.notifications,'translate',lambda value:value)
        now=self.notifications.clock();reports=[]
        labels={'printing':t('Печатает'),'paused':t('На паузе'),'pausing':t('Ставит на паузу'),'preparing':t('Подготовка'),'stopping':t('Остановка'),'restoring':t('Восстановление'),'idle':t('Ожидает'),'completed':t('Печать завершена'),'stopped':t('Печать отменена'),'failed':t('Печать не завершена'),'error':t('Ошибка')}
        def stamp(value):
            try:return datetime.fromtimestamp(value).astimezone().strftime('%d.%m.%Y %H:%M') if value is not None else t('неизвестно')
            except (ValueError,OverflowError,OSError):return t('неизвестно')
        def brief(value,limit):
            value=str(value);return value if len(value)<=limit else value[:limit-1]+'…'
        snapshot = self.manager.snapshot()
        items = snapshot['printers']
        order = snapshot.get('printer_order')
        if order is not None:
            by_id = {item['printer']['id']: item for item in items}
            items = [by_id[pid] for pid in order if pid in by_id] + [item for item in items if item['printer']['id'] not in order]
        for item in items:
            printer=item['printer'];pid=printer['id'];address=endpoint(printer);details=item.get('print',{});state=item['status'];job=self.tracker.running.get(pid)
            online=bool(item['connection']['online'] and not item['connection']['stale'])
            if state=='error' and item.get('state_code')==5:state='paused'
            active=state in ACTIVE or state=='preparing';file=details.get('file') if active or not online else None
            started=None
            if file and job and same_endpoint(job['endpoint'],address) and job['file']==file and (not job.get('partial') or job.get('elapsed') is not None):started=job.get('started')
            elif online and active and file and number(details.get('elapsed_seconds')) is not None:started=now-number(details['elapsed_seconds'])
            remaining=remaining_time(details.get('remaining_seconds')) if online and active else None
            name=printer['name'];lines=[brief(name,64)+' — '+(labels.get(state,t('Неизвестное состояние')) if online else t('Нет связи · последние полученные данные')), brief(display_filename(file) if file else '—',180), t('Начало: ')+(stamp(started) if file else '—'), t('Примерное окончание: ')+(stamp(now+remaining) if remaining is not None else t('неизвестно') if active or not online else '—')]
            if state=='paused':lines.append(t('Печать на паузе: время окончания изменится.'))
            if online and item.get('error',{}).get('active'):lines.append(brief(item['error'].get('message') or t('Ошибка принтера'),120))
            camera=bool(online and item.get('camera',{}).get('present'))
            if not camera:lines.append(t('Камера: ')+(t('нет или не настроена') if online else t('недоступна')))
            reports.append({'printer_id':pid,'endpoint':address,'name':name,'text':'\n'.join(lines),'camera':camera,'online':online})
        return reports
    async def status_photo(self,pid,address):
        if not self.relay:return None
        monitor=self.manager.get(pid)
        if not same_endpoint(endpoint(self.manager.entry(pid)),address):return None
        photo=await self.relay.snapshot(pid)
        if self.manager.get(pid) is not monitor or not same_endpoint(endpoint(self.manager.entry(pid)),address):return None
        return photo
    async def set_lights(self,value,request_id,authorized):
        t=getattr(self.notifications,'translate',lambda value:value)
        if value is not None and type(value) is not bool:raise ValueError(t('Некорректный режим подсветки'))
        eligible=lambda item:bool(item['connection']['online'] and not item['connection']['stale'] and item.get('capabilities',{}).get('light') is not False and type(item.get('light_on')) is bool)
        items=self.manager.snapshot()['printers'];targets=[item for item in items if eligible(item)]
        desired=any(not item['light_on'] for item in targets) if value is None else value
        plans=[]
        for item in items:
            try:monitor=self.manager.get(item['printer']['id'])
            except KeyError:monitor=None
            plans.append((item,monitor))
        results=[]
        for saved,monitor in plans:
            name=saved['printer']['name'];pid=saved['printer']['id']
            if not authorized():results.append(t('Выполнение прекращено: чат отключён или команда устарела.'));break
            if not eligible(saved):
                results.append(name+': '+(t('нет связи, пропущен') if not saved['connection']['online'] or saved['connection']['stale'] else t('подсветка не поддерживается или её состояние неизвестно, пропущен')));continue
            # Use the same guarded single-attempt command as the UI. Only the
            # light operation is permitted here; job transitions do not cancel it.
            async with self.notifications.lock:
                if not authorized():results.append(t('Выполнение прекращено: чат отключён или команда устарела.'));break
                try:
                    if monitor is None or self.manager.get(pid) is not monitor or not same_endpoint(endpoint(self.manager.entry(pid)),endpoint(saved['printer'])):raise ValueError(t('Подключение изменилось'))
                    current=monitor.snapshot()
                    if not eligible(current) or current['connection']['sessions']!=saved['connection']['sessions']:raise ValueError(t('Нет свежих данных или подключение изменилось'))
                    if current['light_on']==desired:results.append(name+t(': свет уже ')+(t('включён') if desired else t('выключен')));continue
                    result=await monitor.stock_command(action='light',value=desired,request_id=request_id+'-'+pid,expected_session=saved['connection']['sessions'],expected_ip=saved['printer']['ip'])
                    results.append(name+': '+(t('команда ')+(t('включения') if desired else t('выключения'))+t(' отправлена') if result['phase']=='sent' else t('результат не подтверждён; автоматического повтора нет')))
                except asyncio.CancelledError:raise
                except Exception:results.append(name+t(': не удалось отправить команду; автоматического повтора нет'))
        return results
    async def stop(self):
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True);self.tracker.flush();await self.notifications.stop()
    async def observe(self):
        while True:
            try:
                for event in self.tracker.update(self.manager.snapshot()['printers']):self.notifications.enqueue(event)
            except asyncio.CancelledError:raise
            except Exception:LOG.exception('Activity processing failed')
            await asyncio.sleep(1)
    async def deliver(self):
        while True:
            try:await self.notifications.dispatch()
            except asyncio.CancelledError:raise
            except Exception:LOG.exception('Notification delivery failed')
            await asyncio.sleep(1)
    async def sync(self):
        await asyncio.sleep(10)
        while True:
            for printer in list(self.manager.config.printers):
                try:
                    if self.manager.get(printer['id']).snapshot()['connection']['online']:await self.refresh(printer['id'])
                except (KeyError,ValueError):pass
            await asyncio.sleep(300)
    async def refresh(self,pid):
        async with self.locks.setdefault(pid,asyncio.Lock()):
            printer=dict(self.manager.entry(pid));protocol=printer.get('protocol','creality');monitor=self.manager.get(pid)
            if protocol not in ('moonraker','creality','octoprint'):
                self.import_status[pid]='Протокол не предоставляет полную историю. Записываем задания, которые наблюдает 3DMonitor.';return
            try:
                rows=await fetch_native(monitor,protocol)
                if self.manager.get(pid) is not monitor:raise ValueError('Подключение принтера изменилось')
                count=0
                for row in rows:
                    record=native_record(printer,row,protocol) if isinstance(row,dict) else None
                    if record:self.store.imported(record);count+=1
                    if count and count%20==0:
                        await asyncio.sleep(0)
                        if self.manager.get(pid) is not monitor:raise ValueError('Подключение принтера изменилось')
                self.import_status[pid]=(f'OctoPrint сообщает только последнюю печать каждого файла: {count} записей. Полный журнал ведёт 3DMonitor.' if protocol=='octoprint' else f'История с принтера обновлена: {count} записей. Сохранённые записи остаются в 3DMonitor.')
            except asyncio.CancelledError:raise
            except Exception:self.import_status[pid]='История с принтера сейчас недоступна. Локальные записи сохранены; попробуйте обновить позже.'
    def history(self,pid,offset=0):
        self.manager.get(pid)
        return {**self.store.list(pid,offset),'message':self.import_status.get(pid,'3DMonitor сохраняет наблюдаемые задания. Можно обновить историю с принтера.')}
