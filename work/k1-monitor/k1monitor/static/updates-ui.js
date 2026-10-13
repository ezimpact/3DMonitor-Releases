import {t,tm} from './i18n.js?v=0.101';
export function setupUpdates({api,remote,environment=globalThis}){
  const $=id=>document.getElementById(id);if(remote){$('updates-section').hidden=true;return;}
  let state=null,busy=false,polling=false,ticks=0;
  function show(value){
    state=value;$('updates-auto').checked=value.auto_check;
    const labels={idle:t('Проверить наличие новой версии'),checking:t('Проверяем GitHub…'),current:t('Установлена последняя версия'),available:t('Доступно обновление'),downloading:t('Загружаем установщик…'),ready:t('Установщик проверен и готов'),installing:t('Обновление устанавливается. Программа перезапустится.'),error:t('Не удалось проверить обновление')};
    $('updates-status').textContent=value.error?t(value.error):value.last_install?.success===false?t('Установка обновления не завершилась. Программа запущена снова.'):labels[value.phase]??'';
    $('updates-version').textContent=value.available?tm`Новая версия: ${value.available.version}`:'';
    $('updates-download').hidden=!value.available||value.downloaded;$('updates-install').hidden=!value.downloaded;
    const active=busy||['checking','downloading','installing'].includes(value.phase);
    for(const id of ['updates-save','updates-auto'])$(id).disabled=active;
    $('updates-check').disabled=active;$('updates-download').disabled=active;$('updates-install').disabled=active||!value.installation_supported;
    $('about-open').classList.toggle('has-update',!!value.available);
    $('updates-progress').hidden=value.phase!=='downloading';$('updates-progress').max=value.available?.size??1;$('updates-progress').value=value.received??0;
  }
  async function refresh(){if(polling)return;polling=true;try{show(await api('/updates'));}catch(error){$('updates-status').textContent=t(error.message);}finally{polling=false;}}
  async function perform(path,method='POST',body){
    if(busy)return;busy=true;if(state)show(state);let failure='';
    try{show(await api(path,method,body));return true;}catch(error){failure=t(error.message);return false;}
    finally{busy=false;if(state)show(state);if(failure)$('updates-status').textContent=failure;}
  }
  $('updates-form').addEventListener('submit',async event=>{event.preventDefault();if(await perform('/updates','PUT',{auto_check:$('updates-auto').checked}))await perform('/updates/check');});
  $('updates-check').addEventListener('click',()=>perform('/updates/check'));
  $('updates-download').addEventListener('click',()=>perform('/updates/download'));
  $('updates-install').addEventListener('click',()=>{if(!busy&&state?.downloaded&&state.installation_supported)$('update-confirm-dialog').showModal();});
  $('update-confirm-submit').addEventListener('click',async()=>{if(busy)return;if(await perform('/updates/install'))$('update-confirm-dialog').close();});
  $('about-open').addEventListener('click',refresh);
  refresh();const timer=environment.setInterval(()=>{if(++ticks%30===0||$('about-dialog').open||state?.phase==='checking'||state?.phase==='downloading')refresh();},2000);
  environment.addEventListener('pagehide',()=>environment.clearInterval(timer),{once:true});
}
