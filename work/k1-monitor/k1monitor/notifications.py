"""Background Windows and Telegram delivery. Secrets never enter public responses."""
import asyncio
import base64
import ctypes
from datetime import datetime
import json
import os
import re
import secrets
import socket
import ssl
import time
from urllib.error import HTTPError,URLError
from urllib.request import Request,build_opener,HTTPRedirectHandler,HTTPSHandler

import certifi

from .config import atomic_json

EVENTS={'started':'Печать началась','paused':'Печать на паузе','resumed':'Печать продолжена','completed':'Печать завершена','cancelled':'Печать отменена','failed':'Печать не завершена','error':'Ошибка принтера','runout':'Закончился филамент','filament_low':'Остатка катушки может не хватить','temperature_warning':'Нестабильная температура сопла'}

def secret(value,decode=False):
    if os.name!='nt':return value
    class Blob(ctypes.Structure):_fields_=[('length',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=base64.b64decode(value) if decode else value.encode('utf-8');buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    api=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=api.CryptUnprotectData if decode else api.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(Blob)]
    fn.restype=ctypes.c_int;kernel.LocalFree.argtypes=[ctypes.c_void_p]
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):raise ValueError('Сохранённый токен недоступен для этого пользователя Windows. Введите его заново.')
    try:
        result=ctypes.string_at(target.data,target.length)
        return result.decode('utf-8') if decode else base64.b64encode(result).decode('ascii')
    finally:kernel.LocalFree(target.data)

class TelegramError(ValueError):
    def __init__(self,message,retry_after=60,*,retry_safe=False):super().__init__(message);self.retry_after=retry_after;self.retry_safe=retry_safe

def telegram_ssl_context():
    # Windows can lazily download public roots when a native application first
    # needs them; OpenSSL does not perform that download. Supplement the system
    # store with Mozilla roots already supplied by our HTTP dependencies.
    context=ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context

def network_message(error):
    cause=error.reason if isinstance(error,URLError) else error
    if isinstance(cause,ssl.SSLCertVerificationError):return 'Не удалось проверить сертификат Telegram. Проверьте дату Windows и HTTPS-фильтрацию VPN/антивируса.'
    if isinstance(cause,ssl.SSLError):return 'Ошибка защищённого соединения с Telegram. Проверьте работу VPN/прокси.'
    if isinstance(cause,socket.gaierror):return 'Не удалось определить адрес api.telegram.org. Проверьте DNS или VPN.'
    if isinstance(cause,TimeoutError):return 'Telegram не ответил за 12 секунд. Проверьте доступ к api.telegram.org через VPN/прокси.'
    return 'Соединение с api.telegram.org прервано. Проверьте, проходит ли 3DMonitor через VPN/прокси.'

def telegram_request(token,method,body):
    # urllib does not log the URL containing the token. Never return raw network
    # exception text, Telegram descriptions, or redirects to a caller/log.
    if method=='sendPhoto' and isinstance(body.get('photo'),bytes):
        boundary='3DMonitor'+secrets.token_hex(16);parts=[]
        for key,value in body.items():
            if key=='photo':parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="camera.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()+value+b'\r\n')
            else:parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        payload=b''.join(parts)+f'--{boundary}--\r\n'.encode();content_type='multipart/form-data; boundary='+boundary
    else:payload=json.dumps(body).encode();content_type='application/json'
    request=Request('https://api.telegram.org/bot'+token+'/'+method,data=payload,headers={'Content-Type':content_type},method='POST')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    try:
        with build_opener(NoRedirect(),HTTPSHandler(context=telegram_ssl_context())).open(request,timeout=12) as response:data=json.loads(response.read(2*1024*1024))
    except HTTPError as error:
        retry=60
        try:retry=int(json.loads(error.read(16384)).get('parameters',{}).get('retry_after',60))
        except (ValueError,TypeError):pass
        message={401:'Telegram отклонил токен. Проверьте токен бота.',403:'Бот не может писать в чат. Разблокируйте его и подключите чат заново.',409:'Бот используется другим приложением. Создайте отдельного бота для 3DMonitor.',429:'Telegram ограничил частоту сообщений. Повторим позже.'}.get(error.code,'Telegram временно недоступен.')
        raise TelegramError(message,min(3600,max(1,retry)),retry_safe=error.code==429) from None
    except (URLError,TimeoutError,OSError) as error:raise TelegramError(network_message(error)) from None
    except ValueError:raise TelegramError('Telegram вернул некорректный ответ. Проверьте VPN/прокси.') from None
    if not isinstance(data,dict) or data.get('ok') is not True or 'result' not in data:raise TelegramError('Telegram не подтвердил запрос.')
    return data['result']

def display_filename(value):
    # Normalize both path separators independently of the host operating system.
    return str(value).replace('\\','/').rsplit('/',1)[-1]

def text(event,translate=lambda value:value):
    t=translate
    lines=[t(EVENTS.get(event['kind'],'3DMonitor'))+' — '+event.get('name',t('Принтер'))]
    if event['kind']=='filament_low':
        lines.extend([t('Расчётный остаток: ')+str(round(event['available_g']))+t(' г'),
                      t('До конца задания потребуется примерно: ')+str(round(event['required_g']))+t(' г'),
                      t('Учтён запас на погрешность. Проверьте катушку; печать продолжается.')])
        if event.get('file'):lines.append(display_filename(event['file'])[:180])
        return '\n'.join(lines)[:3500]
    if event['kind']=='temperature_warning':
        reasons={'deviation':'Отклонение от цели не менее 10 °C держится 30 секунд.',
                 'jumps':'Повторяющиеся перепады не менее 10 °C между проверками.',
                 'not_heated':'За 5 минут при неизменной цели сопло не вышло на заданную температуру.'}
        lines.extend([t(reasons[event['temperature_kind']]),
                      t('Сейчас: ')+str(round(event['actual_c'],1))+t(' °C · цель: ')+str(round(event['target_c'],1))+t(' °C'),
                      t('За последнюю минуту: ')+str(round(event['minimum_c'],1))+'–'+str(round(event['maximum_c'],1))+t(' °C'),
                      t('Проверьте нагреватель, датчик, проводку и обдув. Это предупреждение, а не диагноз; печать продолжается.')])
        if event.get('file'):lines.append(display_filename(event['file'])[:180])
        return '\n'.join(lines)[:3500]
    if event['kind']=='started':
        started=event.get('started')
        started=started if isinstance(started,(int,float)) and not isinstance(started,bool) and 946684800<=started<=event['created'] else event['created']
        lines.append(t('Начало печати: ')+datetime.fromtimestamp(started).astimezone().strftime('%d.%m %H:%M'))
    if event['kind'] in ('error','failed'):
        error=event.get('error',{});message=error.get('message')
        code=error.get('key') or error.get('code')
        if event.get('runout'):message=t('Закончился филамент')
        elif str(code).removeprefix('key') in ('116','2839'):message=t('Закончился филамент')
        if message:lines.append(str(message)[:350])
        elif code:lines.append(t('Код ошибки: ')+str(code))
    if event['kind'] not in ('completed','cancelled','failed'):
        remaining=event.get('remaining')
        if isinstance(remaining,(int,float)) and not isinstance(remaining,bool) and 0<remaining<366*86400:
            eta=datetime.fromtimestamp(event['created']+remaining).astimezone().strftime('%d.%m %H:%M')
            lines.append(t('Примерное окончание: ')+eta+(t(' — пауза: прогноз изменится') if event.get('paused') else ''))
        else:lines.append(t('Примерное окончание: принтер пока не сообщил оставшееся время.'))
    if event.get('file'):lines.append(display_filename(event['file'])[:180])
    return '\n'.join(lines)[:3500]

class Notifications:
    def __init__(self,path,store,windows=None,request=telegram_request,clock=time.time,translate=lambda value:value):
        self.translate=translate
        self.path=path;self.store=store;self.windows=windows;self.request=request;self.clock=clock
        self.data={'windows':True,'telegram':False,'events':list(EVENTS),'chats':[]}
        self.error='';self.pairing=None;self.pair_task=None;self.bot_task=None;self.status_task=None;self.lock=asyncio.Lock();self.token='';self.status_provider=None;self.photo_provider=None;self.light_provider=None;self.status_last={};self.light_last=-1e12
        self.status_chats=set();self.status_lock=asyncio.Lock();self.status_retry_delay=1
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding='utf-8-sig')))
            try:self.token=secret(self.data.get('token',''),True) if self.data.get('token') else ''
            except (ValueError,OSError):self.error='Токен недоступен для этого пользователя Windows. Введите его заново.'
            events=[kind for kind in self.data['events'] if kind in EVENTS]
            if events!=self.data['events']:self.data['events']=events;self.save()
        if not self.data.get('filament_tracking_v1'):
            self.data['events']=list(dict.fromkeys([*self.data['events'],'filament_low']))
            self.data['filament_tracking_v1']=True
            self.save()
        if not self.data.get('temperature_watch_v1'):
            self.data['events']=list(dict.fromkeys([*self.data['events'],'temperature_warning']))
            self.data['temperature_watch_v1']=True
            self.save()
    def save(self):atomic_json(self.path,self.data)
    def public(self):
        pair=self.pairing if self.pairing and self.pairing['expires']>self.clock() else None
        return {k:self.data.get(k) for k in ('windows','telegram','events','chats')}|{'has_token':bool(self.token),'windows_available':self.windows is not None,'error':self.error,'bot_username':self.data.get('bot_username',''),'pairing':{'code':pair['code'],'expires':pair['expires']} if pair else None}
    async def call(self,method,body):
        if method in ('sendMessage','sendPhoto'):
            body={**body}
            for key in ('text','caption'):
                if key in body:body[key]=self.translate(body[key])
        return await asyncio.to_thread(self.request,self.token,method,body)
    async def configure(self,windows,telegram,events,token=None):
        async with self.lock:
            # A cached older dashboard may still submit the removed switches.
            events=[kind for kind in events if kind not in ('offline','online')]
            if not set(events)<=EVENTS.keys():raise ValueError('Неизвестное событие уведомления')
            if token:
                if not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,100}',token):raise ValueError('Некорректный токен Telegram-бота')
                old=self.token;self.token=token
                try:
                    bot=await self.call('getMe',{});webhook=await self.call('getWebhookInfo',{})
                    if webhook.get('url'):raise ValueError('У бота включён webhook другого приложения. Создайте отдельного бота в BotFather.')
                except Exception:self.token=old;raise
                await self.cancel_pairing()
                self.data.update(token=secret(token),bot_username=bot.get('username',''),chats=[],update_offset=0);self.status_last.clear()
            if telegram and not self.token:raise ValueError('Сначала введите токен Telegram-бота')
            self.data.update(windows=windows,telegram=telegram,events=list(dict.fromkeys(events)));self.error='';self.save()
            return self.public()
    def enqueue(self,event):
        if event['kind'] in EVENTS and event['kind'] in self.data['events']:
            event={**event,'recipients':[chat['id'] for chat in self.data['chats']]}
            self.store.enqueue(event,self.data['windows'] and self.windows is not None,self.data['telegram'] and bool(self.token and self.data['chats']))
    async def dispatch(self):
        async with self.lock:
            for row in self.store.pending(self.clock()):
                event=row['event']
                if event['kind'] not in EVENTS or event['kind'] not in self.data['events']:
                    self.store.delivery(row['id'],windows=False,telegram=False);continue
                message=text(event,self.translate)
                if row['windows']:
                    self.store.delivery(row['id'],windows=False)
                    if self.data['windows'] and self.windows:self.windows('3DMonitor · '+event.get('name','Принтер'),message,event['kind'] in ('error','runout','failed','filament_low','temperature_warning'))
                if row['telegram']:
                    if not self.data['telegram'] or not self.token or not self.data['chats']:self.store.delivery(row['id'],telegram=False);continue
                    try:
                        # Persist a recipient receipt so one offline chat cannot
                        # cause duplicate messages in already successful chats.
                        receipts=event.setdefault('receipts',[])
                        for chat in list(self.data['chats']):
                            if chat['id'] not in event.get('recipients',[]):continue
                            if chat['id'] in receipts or chat['id'] in self.status_chats:continue
                            await self.call('sendMessage',{'chat_id':chat['id'],'text':message,'link_preview_options':{'is_disabled':True}})
                            receipts.append(chat['id'])
                            with self.store.db() as db:db.execute('UPDATE events SET payload=? WHERE id=?',(json.dumps(event,ensure_ascii=False),row['id']))
                        deferred=any(chat['id'] in self.status_chats and chat['id'] in event.get('recipients',[]) and chat['id'] not in receipts for chat in self.data['chats'])
                        self.store.delivery(row['id'],telegram=deferred);self.error=''
                    except TelegramError as error:
                        self.error=str(error);self.store.delivery(row['id'],telegram=False if row['attempts']>=4 else True,retry=self.clock()+max(error.retry_after,min(300,30*2**row['attempts'])))
    async def begin_pairing(self):
        async with self.lock:
            if not self.token:raise ValueError('Сначала сохраните токен бота')
            await self.cancel_pairing()
            webhook=await self.call('getWebhookInfo',{})
            if webhook.get('url'):raise ValueError('У бота включён webhook другого приложения. Используйте отдельного бота.')
            updates=await self.call('getUpdates',{'timeout':0,'limit':100,'allowed_updates':['message']})
            offset=max((update['update_id'] for update in updates),default=-1)+1
            self.data['update_offset']=offset;self.save()
            self.pairing={'code':secrets.token_hex(4).upper(),'expires':self.clock()+300}
            self.pair_task=asyncio.create_task(self.poll_pairing(offset));return self.public()
    async def poll_pairing(self,offset):
        try:
            while self.pairing and self.pairing['expires']>self.clock():
                updates=await self.call('getUpdates',{'offset':offset,'timeout':0,'limit':100,'allowed_updates':['message']})
                for update in updates:
                    offset=max(offset,update['update_id']+1);self.data['update_offset']=offset;message=update.get('message',{});chat=message.get('chat',{})
                    if chat.get('type')!='private' or message.get('text','').strip() not in (self.pairing['code'],'/start '+self.pairing['code']):continue
                    async with self.lock:
                        if not self.pairing or self.pairing['expires']<=self.clock():break
                        chats=[c for c in self.data['chats'] if c['id']!=chat['id']]
                        if len(chats)>=5:raise ValueError('Можно подключить не более пяти чатов')
                        chats.append({'id':chat['id'],'name':str(chat.get('first_name') or chat.get('username') or 'Telegram')[:64]})
                        self.data.update(chats=chats,telegram=True);self.save();self.pairing=None;self.error=''
                    # Linking authorizes this specific chat to receive bot messages.
                    await self.call('sendMessage',{'chat_id':chat['id'],'text':'3DMonitor подключён. Здесь будут уведомления о ваших принтерах. Отправьте «статус» или /status, чтобы получить состояние всех принтеров и снимки камер. «свет» переключает общий свет; «свет вкл» / «свет выкл» задают состояние явно.'});return
                await asyncio.sleep(2)
        except asyncio.CancelledError:raise
        except (TelegramError,ValueError) as error:self.error=str(error);self.pairing=None
        finally:self.pairing=None;self.save()

    def authorized(self,chat_id,token):
        return bool(token and token==self.token and self.data['telegram'] and any(chat['id']==chat_id for chat in self.data['chats']))

    async def reply_status(self,chat_id,token):
        async with self.status_lock:
            await self.status_sequence(chat_id,token)

    async def status_sequence(self,chat_id,token):
        if not self.authorized(chat_id,token) or not self.status_provider:return
        if self.clock()-self.status_last.get(chat_id,-1e12)<30:return
        self.status_last[chat_id]=self.clock()
        plans=self.status_provider()
        self.status_chats.add(chat_id)
        try:
            if not plans:
                await self.send_reply(chat_id,token,'3DMonitor · принтеры не добавлены.');return
            for index,plan in enumerate(plans,1):
                report=plan;photo=None;changed=False;attempts=0
                # Pin the initial order and endpoint, but read fresh telemetry for
                # each attempt. Never associate an old camera with a changed printer.
                def refresh():
                    if 'printer_id' not in plan:return plan
                    return next((item for item in self.status_provider() if item.get('printer_id')==plan['printer_id'] and item.get('endpoint')==plan['endpoint']),None)
                for attempt in range(3):
                    if not self.authorized(chat_id,token):return
                    report=refresh()
                    if report is None:changed=True;report=plan;break
                    if not report['camera'] or not self.photo_provider:
                        if report.get('online') is False and attempt<2:
                            await asyncio.sleep(self.status_retry_delay);continue
                        break
                    attempts+=1
                    try:
                        async with asyncio.timeout(20):photo=await self.photo_provider(report['printer_id'],report['endpoint'])
                    except asyncio.CancelledError:raise
                    except Exception:photo=None
                    current=refresh()
                    if current is None:changed=True;report=plan;photo=None;break
                    report=current
                    if photo:break
                    if attempt<2:await asyncio.sleep(self.status_retry_delay)
                prefix=f'[{index}/{len(plans)}] '
                message=prefix+report['text']
                if changed:message=prefix+plan['name']+self.translate('\nПодключение изменено или принтер удалён. Повторите «статус».')
                if photo:
                    caption=prefix+report.get('caption',report['text'])+self.translate('\nСнимок: ')+datetime.fromtimestamp(self.clock()).astimezone().strftime('%d.%m %H:%M:%S')
                    failure=None
                    for delivery in range(3):
                        async with self.lock:
                            if not self.authorized(chat_id,token):return
                            try:
                                await self.call('sendPhoto',{'chat_id':chat_id,'photo':photo,'caption':caption})
                                failure=None;break
                            except TelegramError as error:failure=error
                        # Telegram explicitly rejected a rate-limited request,
                        # so a short retry is safe. Timeouts remain ambiguous.
                        if not failure.retry_safe or failure.retry_after>10 or delivery==2:break
                        await asyncio.sleep(failure.retry_after)
                    if failure is None:continue
                    message+=self.translate('\nОтправка снимка не подтверждена Telegram.')
                elif attempts:message+=self.translate('\nСнимок недоступен после ')+str(attempts)+self.translate(' попыток.')
                async with self.lock:
                    if not self.authorized(chat_id,token):return
                    for start in range(0,len(message),3500):
                        await self.call('sendMessage',{'chat_id':chat_id,'text':message[start:start+3500],'link_preview_options':{'is_disabled':True}})
        finally:self.status_chats.discard(chat_id)

    async def send_reply(self,chat_id,token,message):
        async with self.lock:
            if self.authorized(chat_id,token):await self.call('sendMessage',{'chat_id':chat_id,'text':message[:3500],'link_preview_options':{'is_disabled':True}})

    async def reply_light(self,chat_id,token,value,stamp):
        if not self.authorized(chat_id,token) or not self.light_provider:return
        if not 0<=self.clock()-stamp<=30:
            await self.send_reply(chat_id,token,'Команда «свет» устарела и не выполнена. Отправьте её заново.');return
        if self.clock()-self.light_last<3:
            await self.send_reply(chat_id,token,'Подождите 3 секунды после предыдущей команды света.');return
        guard=lambda:self.authorized(chat_id,token) and 0<=self.clock()-stamp<=30
        try:
            async with asyncio.timeout(max(0,30-(self.clock()-stamp))):result=await self.light_provider(value,secrets.token_hex(16),guard)
        except TimeoutError:result=['Время выполнения команды истекло. Часть команд могла быть отправлена; автоматического повтора нет. Проверьте состояние принтеров.']
        self.light_last=self.clock()
        # Results describe single-attempt sends, not an assumed acknowledgement.
        chunks=[];chunk=self.translate('3DMonitor · свет на всех принтерах\n')
        for line in result:
            if len(chunk)+len(line)+1>3500:chunks.append(chunk);chunk=''
            chunk+=line+'\n'
        chunks.append(chunk if result else chunk+self.translate('Принтеры не добавлены.'))
        for chunk in chunks:await self.send_reply(chat_id,token,chunk)

    async def status_background(self,chat_id,token):
        try:await self.reply_status(chat_id,token)
        except asyncio.CancelledError:raise
        except TelegramError as error:self.error=str(error)
        except Exception:self.error='Не удалось получить статус принтеров. Повторите запрос.'

    async def poll_commands_once(self,background_status=False):
        async with self.lock:
            if not self.token or not self.data['telegram'] or not self.data['chats'] or self.pair_task and not self.pair_task.done():return
            token=self.token;offset=self.data.get('update_offset',0)
            updates=await self.call('getUpdates',{'offset':offset,'timeout':0,'limit':100,'allowed_updates':['message']})
            if not isinstance(updates,list):raise TelegramError('Telegram вернул некорректный список сообщений.')
            if updates:
                self.data['update_offset']=max(offset,max(update['update_id']+1 for update in updates));self.save()
        seen=set()
        for update in updates:
            if update['update_id']<offset or update['update_id'] in seen:continue
            seen.add(update['update_id'])
            message=update.get('message',{});chat=message.get('chat',{});stamp=message.get('date',0)
            if chat.get('type')!='private' or not self.authorized(chat.get('id'),token) or not isinstance(stamp,(int,float)) or not 0<=self.clock()-stamp<=120:continue
            value=message.get('text','')
            if not isinstance(value,str):continue
            command=' '.join(value.strip().casefold().split());parts=command.split(' ')
            head=parts[0]
            if '@' in head:
                head,username=head.split('@',1)
                if username!=self.data.get('bot_username','').casefold():continue
            if head in ('help','/help','помощь','/помощь','start','/start') and len(parts)==1:
                help_ru='Команды 3DMonitor:\n/status или статус — состояние принтеров и снимки камер.\n/light или свет — переключить свет на всех доступных принтерах.\n/light on или свет вкл — включить свет.\n/light off или свет выкл — выключить свет.\n/help или помощь — показать эту справку.\nКоманды доступны только привязанным личным чатам при включённом Telegram.'
                help_en='3DMonitor commands:\n/status or status — printer status and camera snapshots.\n/light or light — toggle lights on all available printers.\n/light on — turn lights on.\n/light off — turn lights off.\n/help or help — show this help.\nCommands are available only in paired private chats while Telegram is enabled.'
                await self.send_reply(chat['id'],token,help_en if head in ('help','/help') else self.translate(help_ru))
            elif head in ('статус','status','/status','/статус') and len(parts)==1:
                if not background_status:await self.reply_status(chat['id'],token)
                elif self.status_task is None or self.status_task.done():self.status_task=asyncio.create_task(self.status_background(chat['id'],token))
                else:await self.send_reply(chat['id'],token,'Статус и снимки уже собираются. Команда «свет» доступна во время ожидания.')
            elif head in ('свет','light','/light','/свет','lights','/lights'):
                mode=None if len(parts)==1 else {'вкл':True,'включить':True,'on':True,'выкл':False,'выключить':False,'off':False}.get(parts[1]) if len(parts)==2 else None
                if len(parts)>1 and (len(parts)!=2 or mode is None):
                    await self.send_reply(chat['id'],token,'Команды: «свет» — переключить, «свет вкл» — включить, «свет выкл» — выключить.');continue
                await self.reply_light(chat['id'],token,mode,stamp)

    async def poll_commands(self):
        while True:
            delay=2
            try:await self.poll_commands_once(background_status=True)
            except asyncio.CancelledError:raise
            except TelegramError as error:self.error=str(error);delay=max(2,error.retry_after)
            except Exception:self.error='Не удалось обработать команду Telegram. Повторите запрос.';delay=30
            await asyncio.sleep(delay)

    async def start(self):
        if self.bot_task is None:self.bot_task=asyncio.create_task(self.poll_commands())
    async def cancel_pairing(self):
        if self.pair_task:
            self.pair_task.cancel();await asyncio.gather(self.pair_task,return_exceptions=True);self.pair_task=None
        self.pairing=None
    async def remove_chat(self,chat_id):
        async with self.lock:
            self.data['chats']=[chat for chat in self.data['chats'] if chat['id']!=chat_id];self.save();return self.public()
    async def test(self,channel):
        if channel=='windows':
            if self.windows is None:raise ValueError('Уведомления Windows требуют работающего значка в трее')
            self.windows('3DMonitor','Проверка уведомления. Принтерам команды не отправляются.',False)
        elif channel=='telegram':
            async with self.lock:
                if not self.token or not self.data['chats']:raise ValueError('Сначала подключите чат Telegram')
                for chat in self.data['chats']:await self.call('sendMessage',{'chat_id':chat['id'],'text':'3DMonitor: проверка уведомлений.'})
        else:raise ValueError('Неизвестный канал уведомления')
        return {'sent':True}
    async def stop(self):
        if self.bot_task:
            self.bot_task.cancel();await asyncio.gather(self.bot_task,return_exceptions=True);self.bot_task=None
        if self.status_task:
            self.status_task.cancel();await asyncio.gather(self.status_task,return_exceptions=True);self.status_task=None
        await self.cancel_pairing()
