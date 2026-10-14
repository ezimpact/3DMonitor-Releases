import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.103.3';
export function setupPrinterTools({api,getSelected,getPrinters,onMessage=()=>{},onStateChange=()=>{}}){
  const $=id=>document.getElementById(id);let busy=false,lastId=null,objectTarget=null,objectKey='';
  function message(text){$('tools-message').textContent=t(text);if(text)onMessage(text);}
  async function command(action,value,item=getSelected()){
    if(!item||busy)return;
    busy=true;update(getSelected());onStateChange();message(t('Отправляем команду…'));
    try{const result=await api('/printers/'+encodeURIComponent(item.printer.id)+'/settings-command','POST',{action,value,request_id:crypto.randomUUID(),expected_ip:item.printer.ip,expected_session:item.connection.sessions,expected_file:item.print.file,expected_job:item.print.job_id??null});message(result.message);}
    catch(exc){message(t(exc.message));}finally{busy=false;update(getSelected());onStateChange();}
  }
  $('light-toggle').addEventListener('click',()=>{const item=getSelected();if(typeof item?.light_on==='boolean')command('light',!item.light_on);});
  function speedPreview(){
    const slider=$('speed-value');$('speed-percent').textContent=slider.value+'%';
    slider.style.setProperty('--speed-fill',((Number(slider.value)-Number(slider.min))/(Number(slider.max)-Number(slider.min))*100)+'%');
  }
  $('speed-value').addEventListener('input',speedPreview);
  $('speed-apply').addEventListener('click',()=>command('speed',Number($('speed-value').value)));
  const temperatureEdits={};
  for(const kind of ['nozzle','bed']){
    $(kind+'-set').addEventListener('input',()=>{temperatureEdits[kind]=structuredClone(getSelected());});
    $(kind+'-apply').addEventListener('click',()=>{
      const item=getSelected(),value=Number($(kind+'-set').value),bound=temperatureEdits[kind];
      if(!item||!item.controls?.[kind]||busy)return;
      if(bound&&(bound.printer.id!==item.printer.id||bound.printer.ip!==item.printer.ip||bound.connection.sessions!==item.connection.sessions||bound.print.file!==item.print.file||bound.print.job_id!==item.print.job_id)){message(t('Подключение или задание изменилось. Задайте температуру заново.'));return;}
      if($(kind+'-set').value.trim()===''||!Number.isInteger(value)||value<0||value>(item.temperature_limits?.[kind]??(kind==='nozzle'?300:100))){message(kind==='nozzle'?t('Температура сопла: 0–300 °C'):t('Температура стола: 0–100 °C'));return;}
      command(kind,value,structuredClone(item));
    });
  }
  let unloadEdit=null;
  $('unload-temperature').addEventListener('input',()=>{unloadEdit=structuredClone(getSelected());});
  $('unload-temperature-apply').addEventListener('click',async()=>{
    const item=getSelected(),temperature=Number($('unload-temperature').value);if(!item||busy||item.auto_unload?.supported===false)return;
    if(unloadEdit&&(unloadEdit.printer.id!==item.printer.id||unloadEdit.printer.ip!==item.printer.ip)){message(t('Принтер изменился. Задайте температуру заново.'));return;}
    if(!Number.isInteger(temperature)||temperature<180||temperature>300){message(t('Температура выгрузки: 180–300 °C'));return;}
    busy=true;update(item);
    try{await api('/printers/'+encodeURIComponent(item.printer.id)+'/auto-unload','PUT',{temperature});item.auto_unload={...item.auto_unload,temperature};message(t('Температура автовыгрузки сохранена: ')+temperature+' °C · '+item.printer.name);}
    catch(exc){message(t(exc.message));}finally{busy=false;update(getSelected());}
  });
  $('auto-unload').addEventListener('change',async()=>{
    const item=getSelected(),enabled=$('auto-unload').checked;if(!item||busy)return;
    busy=true;$('auto-unload').disabled=true;
    try{await api('/printers/'+encodeURIComponent(item.printer.id)+'/auto-unload','PUT',{enabled});item.auto_unload={...item.auto_unload,enabled};message(enabled?t('Автовыгрузка включена для ')+item.printer.name:t('Автовыгрузка выключена для ')+item.printer.name);}
    catch(exc){message(t(exc.message));}finally{busy=false;update(getSelected());}
  });
  function objects(){
    if(!$('objects-dialog').open||!objectTarget)return;
    const item=getPrinters().find(printer=>printer.printer.id===objectTarget.printer.id);
    const same=item&&item.printer.ip===objectTarget.printer.ip&&item.connection.sessions===objectTarget.connection.sessions&&item.print.file===objectTarget.print.file&&item.print.job_id===objectTarget.print.job_id;
    const key=JSON.stringify([same,item?.objects,item?.controls.exclude,busy]);if(key===objectKey)return;objectKey=key;
    $('objects-list').replaceChildren();$('objects-note').textContent=same?t('Исключение действует до конца текущей печати.'):t('Задание или подключение изменились. Закройте окно и откройте заново.');
    for(const model of same?item.objects:[]){
      const row=document.createElement('div'),name=document.createElement('span'),button=document.createElement('button');row.className='object-row';name.textContent=model.name+(model.current?t(' · печатается сейчас'):'');button.className='danger-secondary';button.textContent=model.excluded?t('Исключена'):t('Исключить');button.disabled=model.excluded||model.current||busy||!item.controls.exclude;
      button.addEventListener('click',async()=>{if(!confirm(t('Исключить модель «')+model.name+t('» из текущей печати?')))return;await command('exclude',model.name,item);$('objects-note').textContent=$('tools-message').textContent;});row.append(name,button);$('objects-list').append(row);
    }
    if(same&&!item.objects.length)$('objects-note').textContent=t('Принтер не передал список моделей. Включите маркировку объектов в слайсере для будущих файлов.');
    else if(same&&item.objects.filter(model=>!model.excluded).length<=1)$('objects-note').textContent=t('Осталась одна модель: её исключить нельзя. Для завершения печати используйте кнопку «Стоп».');
  }
  async function openObjects(item=getSelected()){
    if(!item||busy)return;objectTarget=structuredClone(item);objectKey='';
    $('objects-printer').textContent=item.printer.name+' · '+(item.print.file?.split('/').pop()??t('Нет задания'));
    $('objects-dialog').showModal();$('objects-list').replaceChildren();$('objects-note').textContent=t('Читаем модели принтера…');
    try{
      const result=await api('/printers/'+encodeURIComponent(item.printer.id)+'/objects');
      const live=getPrinters().find(printer=>printer.printer.id===item.printer.id);
      if(objectTarget?.printer.id!==item.printer.id)return;
      if(live&&live.printer.ip===item.printer.ip&&live.connection.sessions===result.connection.sessions&&live.print.file===result.print.file&&live.print.job_id===result.print.job_id){live.objects=result.objects;live.controls.exclude=result.controls.exclude;}
      objectKey='';objects();
    }catch(exc){if(objectTarget?.printer.id===item.printer.id)$('objects-note').textContent=t(exc.message);}
  }
  $('objects-open').addEventListener('click',()=>openObjects());
  function update(item){
    const fresh=item?.connection.online&&!item.connection.stale;
    if(lastId!==item?.printer.id){lastId=item?.printer.id;message('');$('speed-value').value=item?.speed_percent??100;unloadEdit=null;$('unload-temperature').value=item?.auto_unload?.temperature??240;
      for(const kind of ['nozzle','bed']){delete temperatureEdits[kind];$(kind+'-set').value=item?.temperatures?.[kind+'_target']??0;}
    }
    for(const kind of ['nozzle','bed']){
      if(!temperatureEdits[kind]&&document.activeElement!==$(kind+'-set'))$(kind+'-set').value=item?.temperatures?.[kind+'_target']??0;
      $(kind+'-set').max=item?.temperature_limits?.[kind]??(kind==='nozzle'?300:100);
      $(kind+'-set').disabled=$(kind+'-apply').disabled=busy||!fresh||!item?.controls?.[kind];
    }
    if(!unloadEdit&&document.activeElement!==$('unload-temperature'))$('unload-temperature').value=item?.auto_unload?.temperature??240;
    $('unload-temperature').disabled=$('unload-temperature-apply').disabled=busy||!item||item.auto_unload?.supported===false;
    $('unload-temperature-saved').textContent=item?.auto_unload?.temperature!=null?t('Температура выгрузки: ')+item.auto_unload.temperature+' °C':t('По умолчанию 240 °C. Выберите температуру для своего материала.');
    const light=$('light-toggle'),on=item?.light_on===true;
    const label=typeof item?.light_on==='boolean'?(on?t('Выключить подсветку'):t('Включить подсветку')):t('Подсветка недоступна');
    light.setAttribute('aria-label',label);light.title=label;light.setAttribute('aria-pressed',String(on));
    light.disabled=busy||!fresh||typeof item?.light_on!=='boolean';
    $('speed-current').textContent=Number.isFinite(item?.speed_percent)?Math.round(item.speed_percent)+'%':'—';
    $('speed-value').min=item?.speed_limits?.min??10;$('speed-value').max=item?.speed_limits?.max??200;
    $('speed-value').value=Math.max(Number($('speed-value').min),Math.min(Number($('speed-value').max),Number($('speed-value').value)));
    $('speed-value').disabled=busy||!item?.controls.speed;$('speed-apply').disabled=busy||!item?.controls.speed;speedPreview();
    $('auto-unload').checked=item?.auto_unload?.enabled??false;$('auto-unload').disabled=busy||!item||item.auto_unload?.supported===false;$('unload-status').textContent=t(item?.auto_unload?.message)??t('Автовыгрузка выключена');
    $('objects-open').disabled=!item||item.capabilities?.objects===false;objects();
  }
  return {update,openObjects,toggleLight:item=>command('light',!item.light_on,item),isBusy:()=>busy};
}
