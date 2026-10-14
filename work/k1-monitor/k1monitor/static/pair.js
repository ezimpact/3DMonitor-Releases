import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.103.3';
const $=id=>document.getElementById(id);
const target=()=>'/';
async function checkSession(){
  $('pair-form').hidden=true;$('pair-check-retry').hidden=true;
  $('pair-hint').textContent=t('Проверяем сохранённое подключение…');
  try{
    const response=await fetch('/api/v1/auth/session',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw new Error('Session check failed');
    const data=await response.json();
    if(data.paired){location.replace(target());return;}
    $('pair-hint').textContent=t('На ПК откройте «Удалённое подключение» в 3DMonitor и создайте код подключения.');
    $('pair-form').hidden=false;
  }catch(error){
    $('pair-hint').textContent=t('Не удалось проверить подключение. Проверьте Tailscale и связь с ПК.');
    $('pair-check-retry').hidden=false;
  }
}
$('pair-check-retry').addEventListener('click',checkSession);
checkSession();
$('pair-form').addEventListener('submit',async event=>{
  event.preventDefault();$('pair-error').hidden=true;$('pair-submit').disabled=true;
  try{
    const response=await fetch('/api/v1/auth/pair',{method:'POST',headers:{'Content-Type':'application/json','X-K1-Token':document.querySelector('meta[name="k1-token"]').content},body:JSON.stringify({code:$('pair-code').value.trim(),name:$('pair-name').value.trim()}),signal:AbortSignal.timeout(15000)});
    const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:t('Проверьте код и название устройства.'));
    location.replace(target());
  }catch(error){$('pair-error').textContent=t(error.message);$('pair-error').hidden=false;$('pair-submit').disabled=false;}
});
