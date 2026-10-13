import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.101';
import {modelFormat} from './model-preview-utils.js';

export function setupModelPreview(){
  const $=id=>document.getElementById(id),dialog=$('model-dialog'),stage=$('model-stage');
  let viewer=null,worker=null,timer=null,revision=0,loaded=false,opening=null;
  const controls=[...dialog.querySelectorAll('[data-model-view]'),$('model-wireframe')];
  function message(text,error=false){$('model-status').textContent=t(text);$('model-status').classList.toggle('form-error',error);$('model-status').setAttribute('role',error?'alert':'status');}
  function stopWorker(){clearTimeout(timer);timer=null;worker?.terminate();worker=null;}
  function setBusy(value){$('model-cancel').hidden=!value;$('model-dialog').setAttribute('aria-busy',String(value));}
  function cleanup(){revision++;stopWorker();viewer?.dispose();viewer=null;opening=null;loaded=false;$('model-file').value='';$('model-info').textContent='';$('model-wireframe').checked=false;controls.forEach(button=>button.disabled=true);setBusy(false);$('model-placeholder').hidden=false;}
  async function ensureViewer(){
    if(viewer)return viewer;
    if(opening)return opening;
    const current=revision;
    opening=import('./model-preview-renderer.js').then(({createModelRenderer})=>{
      if(current!==revision||!dialog.open)return null;
      viewer=createModelRenderer(stage);return viewer;
    }).finally(()=>{if(current===revision)opening=null;});
    return opening;
  }
  $('model-open').addEventListener('click',()=>{if(dialog.open)return;dialog.showModal();message(t('Выберите свою модель STL, STEP или STP.'));});
  $('model-choose').addEventListener('click',()=>$('model-file').click());
  async function openFile(file){
    if(!file)return;
    if(!dialog.open)dialog.showModal();
    let format;
    try{format=modelFormat(file);}catch(error){message(t(error.message),true);return;}
    cleanup();const current=revision;setBusy(true);message(t('Открытие модели…'));$('model-info').textContent=file.name;
    const fail=text=>{if(current!==revision||!dialog.open)return;stopWorker();setBusy(false);message(text,true);};
    try{
      const renderer=await ensureViewer();if(!renderer||current!==revision)return;
      const buffer=await file.arrayBuffer();if(current!==revision||!dialog.open)return;
      worker=new Worker(new URL('./model-preview-worker.js',import.meta.url));
      timer=setTimeout(()=>{revision++;stopWorker();setBusy(false);message(t('Модель открывается слишком долго. Попробуйте файл меньшего размера.'),true);},120000);
      worker.onerror=event=>{event.preventDefault();fail(t('Не удалось открыть модель. Попробуйте другой файл или обновите браузер.'));};
      worker.onmessage=({data})=>{
        if(current!==revision||!dialog.open)return;
        if(data.phase==='parsing'){message(data.message);return;}
        if(data.phase==='error'){fail(data.message);return;}
        if(data.phase!=='ready')return;
        try{
          const details=renderer.load(data.meshes);stopWorker();setBusy(false);loaded=true;controls.forEach(button=>button.disabled=false);$('model-placeholder').hidden=true;
          const number=value=>new Intl.NumberFormat(locale,{maximumFractionDigits:2}).format(value);
          $('model-info').textContent=tm`${file.name} · ${details.size.map(number).join(' × ')} ${format==='step'?t('мм'):t('ед. STL')} · ${number(details.triangles)} треугольников`;
          message(format==='stl'?t('Модель открыта. STL не хранит единицы: обычно это миллиметры.'):t('Модель открыта.'));
        }catch(error){fail(t(error.message)||t('Не удалось отобразить модель.'));}
      };
      worker.postMessage({format,buffer},[buffer]);
    }catch(error){fail(t(error.message)?.includes('WebGL')?t('3D-просмотр недоступен. Включите аппаратное ускорение браузера или попробуйте другой браузер.'):t('Не удалось подготовить просмотр. Обновите страницу или переустановите 3DMonitor.'));}
  }
  $('model-file').addEventListener('change',()=>{const file=$('model-file').files?.[0];$('model-file').value='';return openFile(file);});
  $('model-cancel').addEventListener('click',()=>{cleanup();message(t('Открытие отменено. Можно выбрать другую модель.'));});
  for(const button of dialog.querySelectorAll('[data-model-view]'))button.addEventListener('click',()=>viewer?.view(button.dataset.modelView));
  $('model-wireframe').addEventListener('change',()=>viewer?.wireframe($('model-wireframe').checked));
  stage.addEventListener('model-context-lost',()=>{cleanup();message(t('Браузер освободил ресурсы 3D-просмотра. Откройте модель ещё раз.'),true);});
  dialog.addEventListener('close',cleanup);
  window.addEventListener('pagehide',cleanup);
  return {openFile};
}
