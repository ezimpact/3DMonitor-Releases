import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.102.1';
export function setupPhone({api,remote}) {
  const $=id=>document.getElementById(id);
  if(remote){
    document.body.classList.add('remote-view');
    $('phone-open').hidden=true;$('phone-logout').hidden=false;$('mobile-note').hidden=false;
    document.querySelector('.brand span:last-child').textContent=t('Через Tailscale');
    $('phone-logout').addEventListener('click',async()=>{
      $('phone-logout').disabled=true;
      try{await api('/auth/logout','POST');location.replace('/');}
      catch(error){$('phone-logout').disabled=false;$('mobile-note').textContent=t(error.message);}
    });
    return;
  }
  let expires=0,loading=false,editing=null;
  function show(data){
    $('phone-setup').hidden=!!data.url;$('phone-ready').hidden=!data.url;
    if(data.url){$('phone-url').textContent=data.url;$('phone-url').href=data.url;}
    $('phone-code-box').hidden=!data.code;$('phone-code').textContent=data.code??'';expires=data.code_expires??0;
    $('phone-devices').replaceChildren();
    for(const device of data.devices){
      const row=document.createElement('div');row.className='phone-device';
      const name=document.createElement('span');name.textContent=device.name;
      const rename=document.createElement('button');rename.className='text-button';rename.textContent='✎';rename.title=t('Переименовать ')+device.name;rename.setAttribute('aria-label',rename.title);
      rename.addEventListener('click',()=>{editing=device.id;$('device-rename-name').value=device.name;$('device-rename-error').hidden=true;$('device-rename-dialog').showModal();});
      const remove=document.createElement('button');remove.className='text-button';remove.textContent=t('Отключить');
      remove.setAttribute('aria-label',t('Отключить ')+device.name);
      remove.addEventListener('click',async()=>{remove.disabled=true;try{show(await api('/remote/devices/'+encodeURIComponent(device.id),'DELETE'));}catch(error){errorText(t(error.message));remove.disabled=false;}});
      row.append(name,rename,remove);$('phone-devices').append(row);
    }
    countdown();
  }
  function errorText(message=''){$('phone-error').textContent=message;$('phone-error').hidden=!message;}
  function countdown(){
    const left=Math.max(0,Math.ceil(expires-Date.now()/1000));
    $('phone-expiry').textContent=left?tm`Код действует ещё ${Math.floor(left/60)}:${String(left%60).padStart(2,'0')}`:t('Код истёк. Создайте новый.');
  }
  async function refresh(){
    if(loading)return;loading=true;
    try{show(await api('/remote/status'));}catch(error){errorText(t(error.message));}finally{loading=false;}
  }
  $('phone-open').addEventListener('click',()=>{errorText();$('phone-dialog').showModal();refresh();});
  $('phone-code-new').addEventListener('click',async()=>{
    errorText();$('phone-code-new').disabled=true;
    try{show(await api('/remote/pair-code','POST'));}catch(error){errorText(t(error.message));}finally{$('phone-code-new').disabled=false;}
  });
  $('device-rename-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!editing)return;
    $('device-rename-submit').disabled=true;
    try{show(await api('/remote/devices/'+encodeURIComponent(editing),'PATCH',{name:$('device-rename-name').value}));$('device-rename-dialog').close();editing=null;}
    catch(error){$('device-rename-error').textContent=t(error.message);$('device-rename-error').hidden=false;}
    finally{$('device-rename-submit').disabled=false;}
  });
  setInterval(()=>{if($('phone-dialog').open&&!document.hidden)refresh();},2000);
}
