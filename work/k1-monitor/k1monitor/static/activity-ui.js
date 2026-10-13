import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.101';
const kinds={started:t('Начало печати'),paused:t('Пауза'),resumed:t('Продолжение'),completed:t('Завершение'),cancelled:t('Отмена'),failed:t('Неудачное завершение'),error:t('Ошибки'),runout:t('Окончание филамента')};
const states={printing:t('Печатает'),paused:t('На паузе'),pausing:t('Пауза'),stopping:t('Остановка'),restoring:t('Восстановление'),completed:t('Завершено'),stopped:t('Отменено'),failed:t('Не завершено'),unknown:t('Результат неизвестен')};
export function setupActivity({api,remote,getSelected,prepareHistoryPrint}){
 const $=id=>document.getElementById(id);let timer=null,generation=0,printerId=null,offset=0,total=0,preparing=false;
 function update(){const item=getSelected();for(const button of $('history-list').querySelectorAll('.history-play'))button.disabled=preparing||!prepareHistoryPrint||item?.printer.id!==printerId||!item?.connection.online||item.connection.stale||!item.controls.start;}
 function replayButton(record){
  const cell=document.createElement('td'),button=document.createElement('button');button.type='button';button.className='history-play file-action secondary';button.title=t('Повторить печать · ')+record.file;button.setAttribute('aria-label',button.title);
  button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 4 12 8-12 8Z" fill="currentColor"/></svg>';
  button.addEventListener('click',async()=>{if(preparing)return;const gen=generation,pid=printerId;preparing=true;update();error('history-error','');$('history-message').textContent=t('Проверяем G-code на принтере…');
   try{const open=await prepareHistoryPrint(pid,record.id);if(gen===generation&&$('history-dialog').open){open();$('history-message').textContent=t('Проверьте файл и подтвердите запуск печати.');}}
   catch(e){if(gen===generation)error('history-error',t(e.message));}finally{preparing=false;update();}
  });cell.append(button);return cell;
 }
 function error(id,text){$(id).textContent=t(text);$(id).hidden=!text;}
 function date(value){return typeof value==='number'?new Date(value*1000).toLocaleString(locale):'—';}
 function duration(value){if(typeof value!=='number')return '—';const minutes=Math.floor(value/60);return tm`${Math.floor(minutes/60)} ч ${minutes%60} мин`;}
 $('notifications-open').hidden=remote;
 for(const [key,label] of Object.entries(kinds)){
  const row=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=key;row.append(input,document.createTextNode(label));$('notification-events').append(row);
 }
 function display(data,inputs=false){
  if(inputs){$('notification-windows').checked=data.windows;$('notification-telegram').checked=data.telegram;for(const input of $('notification-events').querySelectorAll('input'))input.checked=data.events.includes(input.value);}
  $('notification-token').placeholder=data.has_token?t('Токен сохранён. Оставьте поле пустым, чтобы сохранить его.'):t('Токен от BotFather');
  $('notification-test-windows').disabled=!data.windows_available;
  $('notification-test-telegram').disabled=!data.chats.length;
  $('notification-pair').disabled=!data.has_token;
  $('notification-pair-box').hidden=!data.pairing;
  $('notification-pair-code').textContent=data.pairing?.code||'';
  const username=data.bot_username;
  $('notification-bot-link').hidden=!username;
  if(username){$('notification-bot-link').href='https://t.me/'+encodeURIComponent(username);$('notification-bot-link').textContent=t('Открыть @')+username;}
  $('notification-chats').replaceChildren();
  for(const chat of data.chats){const row=document.createElement('div'),name=document.createElement('span'),remove=document.createElement('button');name.textContent=chat.name;remove.textContent=t('Отключить');remove.className='text-button';remove.addEventListener('click',async()=>{try{display(await api('/notifications/chats/'+encodeURIComponent(chat.id),'DELETE'));}catch(e){error('notification-error',t(e.message));}});row.className='notification-chat';row.append(name,remove);$('notification-chats').append(row);}
  error('notification-error',data.error||'');
 }
 async function poll(){clearTimeout(timer);if(!$('notifications-dialog').open)return;try{display(await api('/notifications'));}catch(e){error('notification-error',t(e.message));}if($('notifications-dialog').open)timer=setTimeout(poll,2500);}
 $('notifications-open').addEventListener('click',async()=>{
  $('notifications-dialog').showModal();$('notification-token').value='';error('notification-error','');
  try{display(await api('/notifications'),true);timer=setTimeout(poll,2500);}catch(e){error('notification-error',t(e.message));}
 });
 $('notifications-dialog').addEventListener('close',()=>{clearTimeout(timer);$('notification-token').value='';});
 $('notification-form').addEventListener('submit',async event=>{
  event.preventDefault();$('notification-save').disabled=true;error('notification-error','');
  try{const token=$('notification-token').value.trim();display(await api('/notifications','PUT',{windows:$('notification-windows').checked,telegram:$('notification-telegram').checked,events:[...$('notification-events').querySelectorAll('input:checked')].map(i=>i.value),...(token?{token}:{})}),true);$('notification-token').value='';$('notification-result').textContent=t('Настройки сохранены.');}catch(e){error('notification-error',t(e.message));}finally{$('notification-save').disabled=false;}
 });
 $('notification-pair').addEventListener('click',async()=>{try{display(await api('/notifications/pair','POST'));}catch(e){error('notification-error',t(e.message));}});
 for(const channel of ['windows','telegram'])$('notification-test-'+channel).addEventListener('click',async()=>{try{await api('/notifications/test','POST',{channel});$('notification-result').textContent=t('Тестовое уведомление отправлено.');}catch(e){error('notification-error',t(e.message));}});
 async function load(refresh=false){
  const current=++generation;error('history-error','');$('history-message').textContent=refresh?t('Получаем историю с принтера…'):t('Загружаем историю…');$('history-refresh').disabled=true;
  try{const data=await api('/printers/'+encodeURIComponent(printerId)+'/history?offset='+offset+(refresh?'&refresh=true':''));if(current!==generation||!$('history-dialog').open)return;
   total=data.total;$('history-list').replaceChildren();$('history-message').textContent=t(data.message);
   for(const record of data.items){const row=document.createElement('tr');for(const value of [record.file,states[record.status]||t('Неизвестно'),date(record.started??record.last_printed)+(record.last_printed?t(' · дата из принтера'):''),date(record.ended),duration(record.elapsed),record.source==='printer'?t('Из принтера')+(record.last_printed?t(' · последняя для файла'):''):'3DMonitor'+(record.partial?t(' · неполная запись'):'')]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}row.append(replayButton(record));$('history-list').append(row);}update();
   $('history-empty').hidden=!!data.items.length;$('history-page').textContent=total?tm`${offset+1}–${Math.min(offset+50,total)} из ${total}`:t('Нет записей');$('history-prev').disabled=offset===0;$('history-next').disabled=offset+50>=total;
  }catch(e){if(current===generation)error('history-error',t(e.message));}finally{if(current===generation)$('history-refresh').disabled=false;}
 }
 $('history-open').addEventListener('click',()=>{const selected=getSelected();if(!selected)return;printerId=selected.printer.id;offset=0;$('history-title').textContent=t('История печати — ')+selected.printer.name;$('history-list').replaceChildren();$('history-dialog').showModal();load();});
 $('history-dialog').addEventListener('close',()=>{generation++;});
 $('history-refresh').addEventListener('click',()=>{offset=0;load(true);});
 $('history-prev').addEventListener('click',()=>{offset=Math.max(0,offset-50);load();});$('history-next').addEventListener('click',()=>{offset+=50;load();});
 window.addEventListener('pagehide',()=>{clearTimeout(timer);generation++;});
 return {update};
}
