import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.103.3';
import {toolbarIcon} from './toolbar-icons.js?v=0.103.3';

export function setupToolbar({document:doc=document,environment=globalThis}={}){
  const definitions={
    'language-open':['language',t('Язык программы'),t('Выбор языка')],
    'notifications-open':['notifications',t('Уведомления'),t('Уведомления Windows и Telegram: события печати и примерное время окончания.')],
    'model-open':['model',t('Предпросмотр STL/STEP'),t('Предпросмотр STL/STEP: открыть свою модель, вращать и приближать. Файл остаётся на этом устройстве.')],
    'about-open':['about',t('О программе'),t('Версия программы и обратная связь.')],
    'donate-open':['donate',t('Поддержать 3DMonitor'),t('Добровольная поддержка разработки через ЮMoney или Озон Банк.')],
    'phone-open':['remote',t('Удалённое подключение'),t('Подключение другого устройства через Tailscale и одноразовый код.')],
    'appearance-open':['appearance',t('Оформление'),t('Цвета и собственный фон. Настройки сохраняются только на этом устройстве.')],
  };
  for(const [id,[icon,label,help]] of Object.entries(definitions)){
    const button=doc.getElementById(id);button.innerHTML=toolbarIcon(icon);button.setAttribute('aria-label',label);button.setAttribute('data-tooltip',help);button.removeAttribute('title');
  }
  const tip=doc.createElement('div');tip.id='toolbar-tooltip';tip.className='toolbar-tooltip';tip.setAttribute('role','tooltip');tip.hidden=true;doc.body.append(tip);
  let timer=null,target=null,shown=null;
  function hide(){environment.clearTimeout(timer);timer=null;target=null;tip.hidden=true;
    if(shown){const ids=(shown.getAttribute('aria-describedby')||'').split(' ').filter(id=>id&&id!==tip.id);if(ids.length)shown.setAttribute('aria-describedby',ids.join(' '));else shown.removeAttribute('aria-describedby');shown=null;}}
  function schedule(button){if(target===button)return;hide();if(!button||button.hidden)return;target=button;
    const requestedDelay=Number(button.getAttribute('data-tooltip-delay')??3000);
    const delay=Number.isFinite(requestedDelay)&&requestedDelay>=0&&requestedDelay<=10000?requestedDelay:3000;
    timer=environment.setTimeout(()=>{if(target!==button||!button.isConnected||button.hidden)return;shown=button;tip.textContent=button.getAttribute('data-tooltip');tip.hidden=false;
      const rect=button.getBoundingClientRect(),size=tip.getBoundingClientRect(),viewport=doc.documentElement.clientWidth;
      tip.style.left=Math.max(8,Math.min(viewport-size.width-8,rect.right-size.width))+'px';
      const below=rect.bottom+10,available=doc.documentElement.clientHeight;
      tip.style.top=(below+size.height>available-8?Math.max(8,rect.top-size.height-10):below)+'px';
      const ids=(button.getAttribute('aria-describedby')||'').split(' ').filter(Boolean);button.setAttribute('aria-describedby',[...ids,tip.id].join(' '));
    },delay);
  }
  doc.addEventListener('pointerover',event=>schedule(event.target.closest?.('[data-tooltip]')));
  doc.addEventListener('pointerout',event=>{if(target&&!target.contains(event.relatedTarget))hide();});
  doc.addEventListener('focusin',event=>schedule(event.target.closest?.('[data-tooltip]')));
  doc.addEventListener('focusout',hide);doc.addEventListener('pointerdown',hide);
  doc.addEventListener('keydown',event=>{if(event.key==='Escape')hide();});
  environment.addEventListener('pagehide',hide);
}
