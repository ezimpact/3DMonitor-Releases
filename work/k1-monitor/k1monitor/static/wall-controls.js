import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.102.1';
const icons={
  light:'<path d="M9 18h6m-5 3h4M8.5 14.5a6 6 0 1 1 7 0c-.9.7-1.5 1.8-1.5 3.5h-4c0-1.7-.6-2.8-1.5-3.5Z"/><path class="bulb-rays" d="M12 1v1M3 4l1 1m16-1-1 1M1 10h2m18 0h2"/>',
  pause:'<path d="M8 5v14M16 5v14" stroke-width="3"/>',
  resume:'<path d="m8 4 12 8-12 8Z"/>',
  stop:'<rect x="5" y="5" width="14" height="14" rx="1" fill="currentColor" stroke="none"/>',
  objects:'<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Zm-8 4.5 8 4.5 8-4.5M12 12v9M3 3l18 18"/>',
  fullscreen:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  collapse:'<path d="M3 8h5V3m13 5h-5V3M8 21v-5H3m13 5v-5h5"/>',
};
const svg=name=>'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[name]+'</svg>';

export function createWallProgress(){
  const element=document.createElement('progress');element.className='wall-print-progress';element.max=100;
  function update(item){
    const value=item?.print?.progress_percent,active=!!item?.print?.file&&['printing','paused','preparing'].includes(item.status);
    element.hidden=!active||!Number.isFinite(value);element.value=Number.isFinite(value)?Math.min(100,Math.max(0,value)):0;
    const stale=!item?.connection.online||item.connection.stale;
    element.classList.toggle('stale',stale);element.title=`${item?.printer.name??t('Принтер')} · ${Math.round(element.value)}%${stale?t(' · последние данные'):''}`;element.setAttribute('aria-label',t('Прогресс печати · ')+element.title);
  }
  return {element,update};
}

// Every click resolves the tile's latest printer, independent of dashboard selection.
export function createWallControls({getItem,isBusy,onLight,onPause,onStop,onObjects,onError,onFullscreen,isFullscreen=()=>false}){
  const element=document.createElement('div');element.className='wall-camera-controls';
  const buttons={};let pauseIcon='';
  function availability(item){
    const fresh=!!item?.connection.online&&!item.connection.stale;
    const ready=fresh&&!isBusy();
    return {light:ready&&typeof item.light_on==='boolean',pause:ready&&!!item.controls?.[item.status==='paused'?'resume':'pause'],
      stop:ready&&!!item.controls?.stop,objects:ready&&item.capabilities?.objects!==false&&!!item.print?.file&&['printing','paused'].includes(item.status),fullscreen:!!item};
  }
  function update(){
    const item=getItem(),allowed=availability(item),name=item?.printer.name??t('Принтер');
    element.setAttribute('role','group');element.setAttribute('aria-label',t('Управление камерой ')+name);
    const on=item?.light_on===true,resume=item?.status==='paused';
    const labels={light:on?t('Выключить подсветку'):t('Включить подсветку'),pause:resume?t('Продолжить печать'):t('Пауза'),stop:t('Стоп'),objects:t('Исключить модель'),fullscreen:isFullscreen()?t('Выйти из полного экрана'):t('Камера на весь экран')};
    for(const [action,button] of Object.entries(buttons)){
      button.disabled=!allowed[action];button.title=labels[action]+' · '+name;button.setAttribute('aria-label',button.title);
    }
    buttons.light.setAttribute('aria-pressed',String(on));
    if(buttons.fullscreen){buttons.fullscreen.innerHTML=svg(isFullscreen()?'collapse':'fullscreen');buttons.fullscreen.setAttribute('aria-pressed',String(isFullscreen()));}
    const next=resume?'resume':'pause';if(next!==pauseIcon){buttons.pause.innerHTML=svg(next);pauseIcon=next;}
  }
  const actions={light:onLight,pause:onPause,stop:onStop,objects:onObjects};
  if(onFullscreen)actions.fullscreen=onFullscreen;
  for(const [action,callback] of Object.entries(actions)){
    const button=document.createElement('button');button.type='button';button.className='camera-light wall-action wall-'+action;button.innerHTML=svg(action);buttons[action]=button;
    button.addEventListener('click',async event=>{
      event.stopPropagation();const item=getItem();if(!availability(item)[action])return;
      try{await callback(structuredClone(item));}catch(error){onError(t(error.message));}
    });element.append(button);
  }
  update();return {element,update};
}
