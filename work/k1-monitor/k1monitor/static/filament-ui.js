import {t,tm,locale} from './i18n.js?v=0.102.1';

export function setupFilament({api,remote,getSelected,getPrinters}){
 const $=id=>document.getElementById(id);let edited=null,saving=false,watchSaving=false;
 const grams=value=>typeof value==='number'&&Number.isFinite(value)?Math.max(0,Math.round(value)).toLocaleString(locale)+t(' г'):t('Неизвестно');
 function update(){
  const item=getSelected(),data=item?.filament,job=data?.job;
  $('filament-panel').hidden=!data;
  if(data){
   $('filament-weight').textContent=data.enabled?grams(data.remaining_g):t('Учёт выключен');
   $('filament-needed').textContent=job?.active?grams(job.needed_g):'—';
   $('filament-warning').hidden=!job?.low||!job.active||!data.enabled;
   let note=t('Остаток приблизительный; продувка и ручная подача могут изменить его.');
   if(!data.enabled)note=t('Включите учёт в настройках катушки.');
   else if(data.awaiting_resume)note=t('Филамент закончился. После продолжения этого задания начнётся новая катушка.');
   else if(data.remaining_g===null)note=t('Остаток пока неизвестен. Отсчёт начнётся после окончания филамента и продолжения печати, либо после уточнения веса.');
   else if(data.origin==='automatic')note=tm`Новая катушка принята за ${Math.round(data.capacity_g)} г. Если установлена начатая катушка, уточните остаток.`;
   if(job?.active){
    if(job.analysis==='reading')note+=' '+t('Читаем расход из G-code в фоне…');
    else if(job.analysis==='unavailable')note+=' '+t(job.error||'Расход задания неизвестен. Проверка запаса недоступна.');
    else if(job.method==='reservation')note+=' '+t('Позиция в G-code неизвестна: весь расход задания зарезервирован заранее.');
    if(job.assumed_density)note+=' '+t('Плотность взята из настройки принтера.');
    if(job.macro_estimate)note+=' '+t('Подача пластика внутри макросов принтера не входит в расчёт.');
   }
   $('filament-note').textContent=note;
   $('filament-settings').hidden=remote;
   $('filament-settings').disabled=saving;
  }
  const thermal=getPrinters().filter(p=>p.temperature_watch?.active);
  $('temperature-alert').hidden=thermal.length===0;
  $('temperature-alert').textContent=thermal.length?t('Нестабильная температура сопла')+' — '+thermal.map(p=>p.printer.name).join(', ')+'. '+t('Проверьте нагреватель, датчик, проводку и обдув.'):'';
  const watch=item?.temperature_watch;
  $('temperature-watch-control').hidden=!watch||remote;
  $('temperature-watch-enabled').disabled=watchSaving||!item;
  if(!watchSaving)$('temperature-watch-enabled').checked=!!watch?.enabled;
  $('temperature-watch-note').textContent=watch?(watch.enabled?t('Контроль сопла: раз в 5 секунд. Отклонение ≥10 °C в течение 30 секунд или повторяющиеся скачки ≥10 °C. Разогрев и смена цели учитываются.')+(watch.active?' '+t('Нестабильная температура сопла'):''):t('Контроль температуры выключен. Включается отдельно для каждого принтера.')):'';
  const low=getPrinters().filter(p=>p.filament?.enabled&&p.filament.job?.active&&p.filament.job.low);
  $('filament-alert').hidden=low.length===0;
  $('filament-alert').textContent=low.length?t('Остатка катушки может не хватить')+' — '+low.map(p=>p.printer.name).join(', ')+'. '+t('Проверьте катушку; печать продолжается.'):'';
 }
 $('filament-settings').addEventListener('click',()=>{
  const item=getSelected();if(!item?.filament||remote||saving)return;
  const p=item.printer;edited={id:p.id,endpoint:[p.protocol||'',p.ip,p.api_port||''].join('|')};
  $('filament-dialog-name').textContent=p.name;
  for(const [id,key] of [['filament-capacity','capacity_g'],['filament-density','density'],['filament-diameter','diameter']])$(id).value=item.filament[key];
  $('filament-enabled').checked=item.filament.enabled;
  $('filament-correction').value='';$('filament-error').hidden=true;
  $('filament-current').textContent=t('Расчётный остаток: ')+grams(item.filament.remaining_g);
  $('filament-dialog').showModal();
 });
 $('filament-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!edited||saving||remote)return;
  const target=edited,body={expected_endpoint:target.endpoint,enabled:$('filament-enabled').checked,
   capacity_g:Number($('filament-capacity').value),density:Number($('filament-density').value),diameter:Number($('filament-diameter').value)};
  if($('filament-correction').value.trim()!=='')body.remaining_g=Number($('filament-correction').value);
  saving=true;$('filament-save').disabled=true;$('filament-error').hidden=true;
  try{await api('/printers/'+encodeURIComponent(target.id)+'/filament','PUT',body);$('filament-dialog').close();}
  catch(error){$('filament-error').textContent=t(error.message);$('filament-error').hidden=false;}
  finally{saving=false;$('filament-save').disabled=false;update();}
 });
 $('temperature-watch-enabled').addEventListener('change',async()=>{
  const item=getSelected();if(!item||remote||watchSaving)return;
  const p=item.printer,enabled=$('temperature-watch-enabled').checked;
  const body={expected_endpoint:[p.protocol||'',p.ip,p.api_port||''].join('|'),enabled};
  watchSaving=true;$('temperature-watch-enabled').disabled=true;$('temperature-watch-error').hidden=true;
  try{
   const saved=await api('/printers/'+encodeURIComponent(p.id)+'/temperature-watch','PUT',body);
   const current=getPrinters().find(value=>value.printer.id===p.id);
   if(current?.printer.ip===p.ip)current.temperature_watch=saved;
  }
  catch(error){$('temperature-watch-error').textContent=t(error.message);$('temperature-watch-error').hidden=false;}
  finally{watchSaving=false;update();}
 });
 return {update};
}
