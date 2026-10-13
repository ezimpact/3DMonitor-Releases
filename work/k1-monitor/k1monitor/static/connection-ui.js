import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.102.1';
// Credentials are write-only. Blank secrets retain an existing login on edits.
export const defaultPort=protocol=>({creality:9999,moonraker:7125,octoprint:80,prusalink:80})[protocol]??9999;
export function setupConnectionFields(prefix){
  const $=suffix=>document.getElementById(prefix+'-'+suffix);let existing={mode:'none'};
  function authFields(){const mode=$('auth-mode').value;$('api-key-field').hidden=mode!=='apikey';$('digest-fields').hidden=mode!=='digest';}
  function fields(reset=false){
    const protocol=$('protocol').value,rest=['octoprint','prusalink'].includes(protocol);
    $('moon-options').hidden=protocol==='creality';$('unload-fields').hidden=protocol!=='moonraker';
    $('auth-options').hidden=!rest;$('beta-note').hidden=!rest;
    $('digest-option').disabled=protocol!=='prusalink';
    if(reset){$('port').value=defaultPort(protocol);load();}
    authFields();
  }
  function load(authentication={mode:'none'}){
    existing={...authentication};$('api-key').value='';$('password').value='';
    $('auth-mode').value=existing.mode??'none';$('username').value=existing.username??'';
    $('secret-note').textContent=existing.configured?t('Ключ или пароль уже сохранён. Пустое поле сохраняет его. Чтобы удалить авторизацию, выберите «Без авторизации».'):t('Введите данные из настроек сервера принтера. OctoPi обычно использует порт 80, отдельный OctoPrint — 5000.');
    authFields();
  }
  function options(){
    const protocol=$('protocol').value,result={protocol,api_port:Number($('port').value),camera_path:protocol!=='creality'?$('camera').value.trim():'',unload_macro:$('unload').value.trim()||'UNLOAD_FILAMENT'};
    if(['octoprint','prusalink'].includes(protocol)){
      const mode=$('auth-mode').value,key=$('api-key').value,password=$('password').value,username=$('username').value.trim();
      if(mode==='none')result.credentials={mode};
      else if(mode==='apikey'){
        if(key)result.credentials={mode,api_key:key};
        else if(existing.mode!=='apikey'||!existing.configured)throw new Error(t('Введите API-ключ'));
      }else if(mode==='digest'){
        if(password&&username)result.credentials={mode,username,password};
        else if(existing.mode!=='digest'||!existing.configured||username!==existing.username)throw new Error(t('Введите логин и пароль PrusaLink'));
      }
    }
    return result;
  }
  $('protocol').addEventListener('change',()=>fields(true));$('auth-mode').addEventListener('change',authFields);
  return {fields,options,load};
}
