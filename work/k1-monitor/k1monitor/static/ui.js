import {setupCosts} from './cost-ui.js?v=0.103.3';
import {setupFilament} from './filament-ui.js?v=0.103.3';
import {setupFileDrop} from './file-drop.js?v=0.103.3';
import {t,tm,m,trMarkup,locale,setupLanguage} from './i18n.js?v=0.103.3';
import {setupActivity} from './activity-ui.js?v=0.103.3';
import {setupModelPreview} from './model-preview.js?v=0.103.3';
import {setupWallGroup} from './wall-group.js?v=0.103.3';
import {setupConnectionFields,defaultPort} from './connection-ui.js?v=0.103.3';
'use strict';
import {CameraPlayer} from './camera-player.js?v=0.103.3';
import {installReorder,normalizeOrder,swapOrder} from './reorder.js';
import {setupPhone} from './remote-ui.js?v=0.103.3';
import {setupUpdates} from './updates-ui.js?v=0.103.3';
import {setupPrintAlerts,errorLabel,describePrinterError} from './print-alerts.js?v=0.103.3';
import {setupToolbar} from './toolbar.js?v=0.103.3';
import {setupAppearance} from './appearance.js?v=0.103.3';
import {setupSupport} from './support-ui.js?v=0.103.3';
import {setupJobThumbnail} from './job-thumbnail.js?v=0.103.3';
import {setupPrinterTools} from './printer-tools.js?v=0.103.3';
import {setupFiles} from './files-ui.js?v=0.103.3';
import {createWallControls,createWallProgress} from './wall-controls.js?v=0.103.3';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="k1-token"]').content;
const remote = document.querySelector('meta[name="k1-access"]')?.content==='remote';
const apiBase=document.querySelector('meta[name="k1-api-base"]')?.content||'/api/v1';
setupUpdates({api,remote});
setupLanguage({api,remote});
setupToolbar();setupAppearance();setupSupport();const modelPreview=setupModelPreview();
const storagePrefix='k1-';
const labels = {printing:t('Печатает'), paused:t('На паузе'), pausing:t('Останавливается на паузу'), stopping:t('Останавливается'), restoring:t('Восстановление печати'), failed:t('Печать не завершена'), stopped:t('Остановлен'), completed:t('Завершено'), preparing:t('Подготовка'), idle:t('Готов к печати'), self_testing:t('Самопроверка'), error:t('Ошибка принтера'), unknown:t('Ожидание данных')};
let selectedId = null;
try { selectedId = localStorage.getItem(storagePrefix+'selected'); } catch (_) {}
let latest = null, selected = null, serverOnline = false, controlBusy = false, confirmAction = null, renameId = null;
let toastTimer, pollTimer, networkKey = '', scanResultsKey = '';
let viewMode='printer', hasWallPreference=false;
let printerOrder=[],cameraScale=100;
let orderReady=false,orderSent='',orderQueue=Promise.resolve();
let sidebarHidden=false;
try{sidebarHidden=localStorage.getItem(storagePrefix+'sidebar-hidden')==='true';}catch(_){}
try{const saved=JSON.parse(localStorage.getItem(storagePrefix+'printer-order'));if(Array.isArray(saved))printerOrder=saved.filter(id=>typeof id==='string');}catch(_){}
try{const saved=Number(localStorage.getItem(storagePrefix+'camera-scale'));if(saved>=50&&saved<=200)cameraScale=Math.round(saved/10)*10;}catch(_){}
const wallIds=new Set(), wallPlayers=new Map();
try {const saved=JSON.parse(localStorage.getItem(storagePrefix+'camera-wall'));if(Array.isArray(saved)){hasWallPreference=true;for(const id of saved)if(typeof id==='string')wallIds.add(id);}}catch(_){}
const dashboardCamera=new CameraPlayer({video:$('camera-video'),image:$('camera-image'),placeholder:$('camera-placeholder'),message:$('camera-text'),negotiate:negotiateCamera,relay:remote,apiBase,onState:renderDashboardCamera});
const printAlerts=setupPrintAlerts();
const jobThumbnail=setupJobThumbnail({image:$('job-thumbnail'),placeholder:$('thumbnail-placeholder'),apiBase});
const filesUI=setupFiles({api,apiBase,getPrinters:()=>latest?.printers??[],inline:true});
setupFileDrop({openModel:file=>modelPreview.openFile(file),uploadGcode:(file,item)=>filesUI.uploadFile(file,item),getSelected:()=>viewMode==='printer'?selected:null,onError:message=>toast(message,true)});
const filamentUI=setupFilament({api,remote,getSelected:()=>selected,getPrinters:()=>latest?.printers??[]});
const costUI=setupCosts({api,remote,getSelected:()=>viewMode==='printer'?selected:null,getPrinters:()=>latest?.printers??[]});
const activityUI=setupActivity({api,remote,getSelected:()=>selected,prepareHistoryPrint:(pid,id)=>filesUI.prepareHistoryPrint(pid,id)});
const printerTools=setupPrinterTools({api,getSelected:()=>selected,getPrinters:()=>latest?.printers??[],onMessage:text=>{if(viewMode==='cameras')toast(text);},onStateChange:()=>render(latest)});
const wallGroup=setupWallGroup({api,getPrinters:()=>latest?.printers??[],getIds:()=>wallIds,isBusy:()=>controlBusy||printerTools.isBusy()||!serverOnline,onStateChange:()=>render(latest),onFinished:poll});
const dashboardOverlay=createWallControls({getItem:()=>selected,isBusy:()=>controlBusy||printerTools.isBusy()||wallGroup.isBusy()||!serverOnline,
  onLight:item=>printerTools.toggleLight(item),onPause:item=>sendControl(item.status==='paused'?'resume':'pause',item),
  onStop:item=>openConfirm('stop',item),onObjects:item=>printerTools.openObjects(item),onError:text=>toast(text,true),
  onFullscreen:async()=>{if(document.fullscreenElement===$('camera-stage'))await document.exitFullscreen();else await $('camera-stage').requestFullscreen?.();},
  isFullscreen:()=>document.fullscreenElement===$('camera-stage')});
dashboardOverlay.element.classList.add('dashboard-camera-controls');$('camera-stage').append(dashboardOverlay.element);

function setText(id, value) { $(id).textContent = value ?? '—'; }
function errorIn(id, message='') { $(id).textContent=t(message); $(id).hidden=!message; }
function toast(message, error=false) { clearTimeout(toastTimer); $('toast').textContent=t(message); $('toast').classList.toggle('error',error); $('toast').hidden=false; toastTimer=setTimeout(()=>$('toast').hidden=true,5000); }
function numeric(value) { return Number.isFinite(value) ? Math.round(value) : '—'; }
function duration(value) { if (!Number.isFinite(value) || value<0) return '—'; const minutes=Math.floor(value/60); return minutes>=60 ? tm`${Math.floor(minutes/60)} ч ${minutes%60} мин` : tm`${minutes} мин`; }
function jobName(path){return typeof path==='string'?path.split(/[\\/]/).pop():'';}
async function api(path, method='GET', body, signal) {
  const timeout=AbortSignal.timeout(path.startsWith('/updates')||path.includes('/history?')||path.endsWith('/files/delete')||path.includes('/files/upload?')||path.endsWith('/transfer')?180000:path.startsWith('/notifications')?45000:15000);
  const binary=body instanceof Blob;
  const response=await fetch(apiBase+path,{method,headers:{'Content-Type':binary?'application/octet-stream':'application/json','X-K1-Token':token},body:body===undefined?undefined:binary?body:JSON.stringify(body),signal:signal?AbortSignal.any([timeout,signal]):timeout});
  const data=await response.json();
  if(remote&&response.status===401){location.replace('/');throw new Error(t('Подключите удалённое устройство заново'));}
  if(!response.ok) throw new Error(typeof data.detail==='string'?t(data.detail):t('Проверьте заполненные поля и повторите действие.'));
  return data;
}
function node(tag, className, text) {const el=document.createElement(tag); if(className)el.className=className; if(text!==undefined)el.textContent=text; return el;}
function printerPath(id=selectedId) { return '/printers/'+encodeURIComponent(id); }
function choose(id) {
  if(selectedId===id&&viewMode==='printer') return;
  selectedId=id; try{localStorage.setItem(storagePrefix+'selected',id);}catch(_){}
  dashboardCamera.stop();dashboardCamera.desired=true;
  viewMode='printer';stopWall();
  render(latest);
}
function renderList(printers) {
  const list=$('printer-list'), ids=new Set(printers.map(item=>item.printer.id));
  if(!remote&&!orderReady&&Object.hasOwn(latest,'printer_order')){
    if(Array.isArray(latest.printer_order))printerOrder=latest.printer_order;
    orderReady=true;
  }
  const order=normalizeOrder(printerOrder,[...ids]);
  if(JSON.stringify(order)!==JSON.stringify(printerOrder)){printerOrder=order;savePrinterOrder();}
  syncPrinterOrder();
  [...list.children].forEach(row=>{if(!ids.has(row.dataset.id))row.remove();});
  for(const item of printers) {
    let row=[...list.children].find(row=>row.dataset.id===item.printer.id);
    if(!row) {
      row=node('div','printer-row');row.dataset.id=item.printer.id;
      const select=node('button','printer-item');
      select.append(node('strong'),node('span','item-state'),node('small'),node('div','item-progress'));
      select.children[1].append(node('span','dot'),node('span'));
      const bar=node('progress');bar.max=100;bar.setAttribute('aria-label',t('Прогресс ')+item.printer.name);select.children[3].append(bar,node('span','item-percent'));
      select.addEventListener('click',()=>choose(item.printer.id));
      const actions=node('div','camera-selection'), add=node('button','camera-add','+'), remove=node('button','camera-remove','−');
      add.addEventListener('click',()=>sidebarAction(item.printer.id,true));remove.addEventListener('click',()=>sidebarAction(item.printer.id,false));
      actions.append(add,remove);row.append(select,actions);list.append(row);
      row.append(node('button','printer-drag reorder-handle','⠿'));
    }
    const online=serverOnline&&item.connection.online;
    const select=row.children[0], actions=row.children[1], included=wallIds.has(item.printer.id);
    select.classList.toggle('selected',viewMode==='printer'&&item.printer.id===selectedId);select.classList.toggle('in-wall',viewMode==='cameras'&&included);select.setAttribute('aria-pressed',String(viewMode==='printer'&&item.printer.id===selectedId));
    select.children[0].textContent=item.printer.name;
    select.children[1].children[0].classList.toggle('live',online);
    select.children[1].children[1].textContent=online?(item.status==='error'?describePrinterError(item).summary:labels[item.status]):t('Нет связи');
    select.children[1].children[1].title=item.error?.active?describePrinterError(item).diagnostic:'';
    select.children[2].textContent=item.printer.ip;
    select.children[3].hidden=!(['printing','paused','preparing','error','completed'].includes(item.status)||!online&&item.print.file);
    select.children[3].firstChild.value=item.print.progress_percent??0;
    select.children[3].children[1].textContent=Number.isFinite(item.print.progress_percent)?Math.round(item.print.progress_percent)+'%':'—%';
    select.children[3].classList.toggle('stale-progress',!online||item.connection.stale);
    const cameras=viewMode==='cameras';
    actions.hidden=remote&&!cameras;
    actions.children[0].textContent=cameras?'+':'✎';
    actions.children[0].title=cameras?t('Добавить камеру к просмотру'):t('Изменить название и IP принтера');
    actions.children[1].title=cameras?t('Убрать камеру из просмотра'):t('Удалить принтер из списка');
    actions.children[0].setAttribute('aria-label',cameras?tm`Добавить камеру ${item.printer.name} к просмотру`:tm`Изменить название и IP принтера ${item.printer.name}`);
    actions.children[1].setAttribute('aria-label',cameras?tm`Убрать камеру ${item.printer.name} из просмотра`:tm`Удалить принтер ${item.printer.name} из списка`);
    actions.children[0].disabled=cameras&&included;actions.children[1].disabled=cameras&&!included;
    row.style.order=printerOrder.indexOf(item.printer.id);
    const handle=row.querySelector('.printer-drag');handle.dataset.label=item.printer.name;
    handle.title=t('Перетащите на другой принтер, чтобы поменять их местами. Можно использовать стрелки.');
    handle.setAttribute('aria-label',t('Переместить принтер ')+item.printer.name);
  }
  setText('printer-count',printers.length);
}
function render(data) {
  if(!data)return;
  latest=data;
  if(!data.printers.some(item=>item.printer.id===selectedId)) {
    dashboardCamera.stop();selectedId=data.printers[0]?.printer.id??null;dashboardCamera.desired=true;
  }
  selected=data.printers.find(item=>item.printer.id===selectedId)??null;
  printAlerts.update(data.printers,serverOnline);
  filesUI.update(viewMode==='printer'&&serverOnline?selected:null);activityUI.update();printerTools.update(selected);filamentUI.update();costUI.update();
  dashboardOverlay.update();wallGroup.update();
  jobThumbnail.update(viewMode==='printer'?selected:null,serverOnline&&!!selected?.connection.online);
  const ids=new Set(data.printers.map(item=>item.printer.id));
  let cleaned=false;for(const id of wallIds)if(!ids.has(id)){wallIds.delete(id);cleaned=true;}if(cleaned)saveWall();
  document.body.classList.toggle('camera-view',viewMode==='cameras');
  $('view-switch').setAttribute('aria-pressed',String(viewMode==='cameras'));setText('view-switch',viewMode==='cameras'?t('▱ Панель принтера'):t('◉ Только камеры'));
  $('camera-wall').hidden=viewMode!=='cameras';
  renderList(data.printers); renderScan(data.scan);
  const newKey=JSON.stringify(data.networks);
  if(newKey!==networkKey){networkKey=newKey;$('network-options').replaceChildren(...data.networks.map(cidr=>{const opt=node('option');opt.value=cidr;return opt;}));if(!latest._networkInitialized){$('scan-network').value=data.networks[0]??'192.168.4.0/24';}}
  $('empty').hidden=!!selected||viewMode==='cameras';$('printer-content').hidden=!selected||viewMode==='cameras';
  if(viewMode==='cameras'){syncWall(data.printers);return;}
  if(!selected){dashboardCamera.stop();return;}
  const s=selected, online=serverOnline&&s.connection.online, fresh=online&&!s.connection.stale;
  $('printer-content').classList.toggle('stale',!fresh);
  setText('printer-name',s.printer.name);setText('printer-model',s.printer.model??'Creality');setText('printer-ip',s.printer.ip);
  $('connection').classList.toggle('live',online);setText('connection-text',!serverOnline?t('Программа недоступна'):online?t('В сети'):t('Нет связи с принтером'));
  setText('updated',s.connection.last_seen?new Date(s.connection.last_seen).toLocaleTimeString(locale):t('Ожидание'));
  const status=online?s.status:'unknown';setText('status',online?labels[status]:t('Нет связи'));
  $('status').className='status'+(['paused','preparing','self_testing'].includes(status)?' warning':status==='error'?' error':['unknown','idle','completed','stopped'].includes(status)?' muted':'');
  setText('filename',jobName(s.print.file)||(['idle','completed','stopped'].includes(status)?t('Нет активного задания'):t('Нет данных о задании')));
  const hasJob=!!s.print.file||['printing','paused','preparing'].includes(status);
  setText('progress',hasJob?numeric(s.print.progress_percent):'—');$('progress-bar').value=hasJob?(s.print.progress_percent??0):0;
  setText('remaining',hasJob?duration(s.print.remaining_seconds):'—');setText('elapsed',hasJob?duration(s.print.elapsed_seconds):'—');setText('layers',!hasJob||s.print.layer===null?'—':`${s.print.layer} / ${s.print.total_layers??'—'}`);
  for(const name of ['nozzle','bed','chamber'])setText(name,numeric(s.temperatures[name]));
  setText('nozzle-target',numeric(s.temperatures.nozzle_target));setText('bed-target',numeric(s.temperatures.bed_target));
  setText('firmware',t('Прошивка ')+s.printer.firmware+(s.printer.firmware_confirmed?'':t(' · ожидаем проверку')));
  setText('errors',!fresh?t('Показаны последние известные данные'):s.error?.active?describePrinterError(s).summary:s.error_code===0?t('Ошибок нет'):t('Нет данных об ошибках'));
  $('errors').title=s.error?.active?describePrinterError(s).diagnostic:'';
  $('notice').hidden=fresh;
  setText('notice',!serverOnline?t('Windows-программа не отвечает. Проверьте, что 3DMonitor запущен.'):online?t('Принтер поддерживает связь, но давно не присылал параметры. Показаны последние значения; управление отключено до обновления.'):t('Нет связи с принтером. Проверьте питание, IP и подключение к сети. Кнопки управления отключены.'));
  const resume=status==='paused';setText('pause-button',resume?t('▶ Продолжить'):t('Ⅱ Пауза'));
  $('pause-button').disabled=!online||controlBusy||wallGroup.isBusy()||!(resume?s.controls.resume:s.controls.pause);
  $('stop-open').disabled=!online||controlBusy||wallGroup.isBusy()||!s.controls.stop;
  const cmd=s.command;$('command-message').className='command-message'+(cmd?' '+cmd.phase:'');
  setText('command-message',cmd?t(cmd.message):online&&['printing','paused','preparing'].includes(status)?t('Управление выбранным принтером.'):t('Кнопки доступны во время печати.'));
  dashboardCamera.update(s,online);
}
function renderScan(scan) {
  if(scan?.error)errorIn('scan-error',scan.error);
  const running=scan?.state==='running';$('scan-start').disabled=running;$('scan-cancel').hidden=!running;$('scan-network').disabled=running;
  $('scan-progress').hidden=!scan;
  if(scan){$('scan-bar').max=scan.total;$('scan-bar').value=scan.checked;setText('scan-summary',tm`${running?t('Ищем'):scan.state==='cancelled'?t('Поиск отменён'):t('Поиск завершён')} · ${scan.checked} из ${scan.total} · найдено ${scan.results.length}`);}
  const results=scan?.results??[], list=$('scan-results'), resultKey=JSON.stringify(results);
  if(resultKey!==scanResultsKey){scanResultsKey=resultKey;list.replaceChildren();
    for(const result of results){const row=node('div','scan-result'),info=node('div');info.append(node('strong',null,result.model),node('small',null,result.ip));row.append(info);if(result.added){row.append(node('span',null,t('Уже добавлен')));}else{const button=node('button','secondary',t('Добавить'));button.addEventListener('click',()=>{$('add-ip').value=result.ip;$('add-name').value=result.model;$('add-protocol').value=result.protocol??'creality';connectionFields('add',true);$('add-port').value=result.api_port??$('add-port').value;showTab('manual');$('add-name').focus();});row.append(button);}list.append(row);}
  }
  $('scan-empty').hidden=results.length>0;setText('scan-empty',scan?.state==='completed'?t('Принтеры не найдены. Проверьте подсеть или добавьте IP вручную.'):running?t('Проверяем адреса…'):t('Запустите поиск, чтобы увидеть принтеры.'));
}
async function poll() {
  clearTimeout(pollTimer);
  try{const data=await api('/status');serverOnline=true;setText('server-state',t('Служба работает'));$('server-dot').classList.add('live');render(data);}
  catch(_){serverOnline=false;setText('server-state',t('Нет связи со службой'));$('server-dot').classList.remove('live');render(latest);}
  pollTimer=setTimeout(poll,document.hidden?5000:1000);
}
function showTab(mode){$('add-form').hidden=mode!=='manual';$('scan-panel').hidden=mode!=='scan';$('manual-tab').classList.toggle('active',mode==='manual');$('scan-tab').classList.toggle('active',mode==='scan');}
const connections=Object.fromEntries(['add','rename'].map(prefix=>[prefix,setupConnectionFields(prefix)]));
const connectionFields=(prefix,reset=false)=>connections[prefix].fields(reset);
const connectionOptions=prefix=>connections[prefix].options();
function openAdd(){connectionFields("add");errorIn('add-error');errorIn('scan-error');$('add-dialog').showModal();}
$('add-open').addEventListener('click',openAdd);$('empty-add').addEventListener('click',openAdd);
$('manual-tab').addEventListener('click',()=>showTab('manual'));$('scan-tab').addEventListener('click',()=>showTab('scan'));
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.close).close()));
$('add-form').addEventListener('submit',async event=>{event.preventDefault();errorIn('add-error');$('add-submit').disabled=true;try{const item=await api('/printers','POST',{ip:$('add-ip').value.trim(),name:$('add-name').value.trim(),...connectionOptions('add')});selectedId=item.id;dashboardCamera.stop();dashboardCamera.desired=true;viewMode='printer';stopWall();$('add-dialog').close();$('add-form').reset();connectionFields('add',true);toast(t('Принтер добавлен'));await poll();}catch(exc){errorIn('add-error',t(exc.message));}finally{$('add-submit').disabled=false;}});
$('scan-start').addEventListener('click',async()=>{errorIn('scan-error');$('scan-start').disabled=true;try{const scan=await api('/scan','POST',{network:$('scan-network').value.trim()});renderScan(scan);}catch(exc){errorIn('scan-error',t(exc.message));$('scan-start').disabled=false;}});
$('scan-cancel').addEventListener('click',async()=>{try{renderScan(await api('/scan','DELETE'));}catch(exc){errorIn('scan-error',t(exc.message));}});
function openPrinterSettings(id){
  if(remote)return;
  const item=latest?.printers.find(item=>item.printer.id===id);
  if(!item)return;
  renameId=id;$('rename-name').value=item.printer.name;$('rename-ip').value=item.printer.ip;
  $('rename-protocol').value=item.printer.protocol??'creality';$('rename-port').value=item.printer.api_port??defaultPort(item.printer.protocol??'creality');$('rename-camera').value=item.printer.camera_path??item.camera?.path??'';$('rename-unload').value=item.printer.unload_macro??'UNLOAD_FILAMENT';connections.rename.load(item.authentication);connectionFields('rename');
  errorIn('rename-error');$('rename-dialog').showModal();
}
function sidebarAction(id,add){
  if(viewMode==='cameras'){setWallCamera(id,add);return;}
  if(remote)return;
  const item=latest?.printers.find(item=>item.printer.id===id);
  if(!item)return;
  if(add)openPrinterSettings(id);else openConfirm('remove',item);
}
$('rename-open').addEventListener('click',()=>openPrinterSettings(selectedId));
$('rename-form').addEventListener('submit',async event=>{event.preventDefault();$('rename-submit').disabled=true;errorIn('rename-error');try{await api(printerPath(renameId),'PATCH',{name:$('rename-name').value.trim(),ip:$('rename-ip').value.trim(),...connectionOptions('rename')});$('rename-dialog').close();toast(t('Настройки принтера сохранены'));await poll();}catch(exc){errorIn('rename-error',t(exc.message));}finally{$('rename-submit').disabled=false;}});
async function sendControl(action, snapshot) {
  if(controlBusy||wallGroup.isBusy())return;
  controlBusy=true;render(latest);
  try{const command=await api(printerPath(snapshot.printer.id)+'/control','POST',{action,request_id:crypto.randomUUID(),expected_session:snapshot.connection.sessions,expected_ip:snapshot.printer.ip,expected_file:snapshot.print.file,...(snapshot.print.job_id!=null?{expected_job:snapshot.print.job_id}:{})});toast(command.phase==='unconfirmed'?command.message:t('Команда отправлена; ожидаем подтверждение'),command.phase==='unconfirmed');await poll();}
  finally{controlBusy=false;render(latest);}
}
$('pause-button').addEventListener('click',async()=>{if(!selected)return;const s=structuredClone(selected);try{await sendControl(s.status==='paused'?'resume':'pause',s);}catch(exc){toast(t(exc.message),true);}});
function openConfirm(type,snapshot){confirmAction={type,snapshot:structuredClone(snapshot)};errorIn('confirm-error');const stop=type==='stop';setText('confirm-title',stop?t('Остановить печать?'):t('Убрать принтер из списка?'));setText('confirm-text',`${snapshot.printer.name} · ${snapshot.printer.ip}`+(stop&&snapshot.print.file?'\n'+jobName(snapshot.print.file):''));setText('confirm-detail',stop?(snapshot.printer.protocol==='moonraker'?t('Текущее задание будет отменено. Штатный макрос Klipper также может парковать принтер. Для временной остановки используйте паузу.'):t('Текущее задание будет отменено. Чтобы временно прервать печать, используйте паузу.')):t('Принтер можно будет добавить снова по IP. Его текущее задание продолжится.'));setText('confirm-submit',stop?t('Остановить печать'):t('Убрать из списка'));$('confirm-dialog').showModal();}
$('stop-open').addEventListener('click',()=>{if(selected)openConfirm('stop',selected);});
$('remove-open').addEventListener('click',()=>{const item=latest?.printers.find(item=>item.printer.id===renameId);if(item){$('rename-dialog').close();openConfirm('remove',item);}});
$('confirm-submit').addEventListener('click',async()=>{const action=confirmAction;if(!action)return;$('confirm-submit').disabled=true;errorIn('confirm-error');try{if(action.type==='stop'){await sendControl('stop',action.snapshot);}else{await api(printerPath(action.snapshot.printer.id),'DELETE');toast(t('Принтер убран из списка'));await poll();}$('confirm-dialog').close();}catch(exc){errorIn('confirm-error',t(exc.message));}finally{$('confirm-submit').disabled=false;}});


function negotiateCamera(id,sdp,signal) {return api(printerPath(id)+'/camera/offer','POST',{sdp},signal);}
function renderDashboardCamera(phase,player) {
  const absent=player.printer?.camera?.present===false;$('camera-toggle').disabled=absent;
  if(absent){setText('camera-badge',t('Нет камеры'));setText('camera-info',t('Камера не настроена'));setText('camera-toggle',t('Нет камеры'));return;}
  setText('camera-badge',{live:t('● Прямой эфир'),connecting:t('Подключение'),retrying:t('Переподключение'),error:t('Нет видео'),offline:t('Нет связи'),off:t('Выключена')}[phase]);
  setText('camera-info',phase==='live'?(player.mode==='webrtc'?`${player.video.videoWidth} × ${player.video.videoHeight} · WebRTC`:player.mode==='relay'?tm`${player.image.naturalWidth} × ${player.image.naturalHeight} · через ПК · до 5 кадр/с`:'MJPEG'):remote?t('Видео через ПК'):t('Видео из локальной сети'));
  setText('camera-toggle',phase==='error'?t('Повторить'):player.desired?t('Выключить'):t('Подключить'));
}
function saveWall(){hasWallPreference=true;try{localStorage.setItem(storagePrefix+'camera-wall',JSON.stringify([...wallIds]));}catch(_) {}}
function savePrinterOrder(){try{localStorage.setItem(storagePrefix+'printer-order',JSON.stringify(printerOrder));}catch(_) {}}
function syncPrinterOrder(){
  if(remote||!orderReady)return;
  const ids=[...printerOrder],key=JSON.stringify(ids);
  if(key===orderSent)return;
  orderSent=key;
  orderQueue=orderQueue.then(()=>api('/printer-order','PUT',{ids})).catch(error=>{if(orderSent===key)orderSent='';toast(t('Не удалось сохранить порядок для Telegram: ')+t(error.message),true);});
}
function swapPrinters(first,second){printerOrder=swapOrder(printerOrder,first,second);savePrinterOrder();render(latest);}
function swapCameras(first,second){const order=swapOrder([...wallIds],first,second);wallIds.clear();for(const id of order)wallIds.add(id);saveWall();render(latest);}
function setCameraScale(value){
  const number=Number(value);if(!Number.isFinite(number))return;
  cameraScale=Math.max(50,Math.min(200,Math.round(number/10)*10));
  $('camera-scale').value=cameraScale;setText('camera-scale-value',cameraScale+'%');
  $('camera-scale').setAttribute('aria-valuetext',cameraScale+'%');
  $('camera-grid').style.setProperty('--camera-size',380*cameraScale/100+'px');
  try{localStorage.setItem(storagePrefix+'camera-scale',String(cameraScale));}catch(_){}
}
function setSidebarHidden(hidden){
  sidebarHidden=hidden;printerReorder.cancel();cameraReorder.cancel();
  const toggleParent=document.querySelector(hidden?'.shell':'.sidebar-header');
  if($('sidebar-toggle').parentElement!==toggleParent)toggleParent.appendChild($('sidebar-toggle'));
  document.body.classList.toggle('sidebar-hidden',hidden);
  const label=hidden?t('Показать левую панель'):t('Скрыть левую панель');
  $('sidebar-toggle').setAttribute('aria-expanded',String(!hidden));$('sidebar-toggle').setAttribute('aria-label',label);$('sidebar-toggle').title=label;setText('sidebar-toggle',hidden?'›':'‹');
  try{localStorage.setItem(storagePrefix+'sidebar-hidden',String(hidden));}catch(_){}
}
function setWallCamera(id,included) {
  if(included)wallIds.add(id);else wallIds.delete(id);
  saveWall();render(latest);
}
function disposeWall(entry){entry.player.dispose();entry.tile.remove();if(!document.querySelector('.camera-expanded'))document.body.classList.remove('camera-expanded-active');}
function stopWall() {cameraReorder.cancel();for(const entry of wallPlayers.values())disposeWall(entry);wallPlayers.clear();}
function setView(mode) {

  if(viewMode===mode)return;
  viewMode=mode;
  if(mode==='cameras') {
    dashboardCamera.stop();
    if(!hasWallPreference&&selectedId){wallIds.add(selectedId);saveWall();}
  } else {stopWall();dashboardCamera.desired=true;}
  render(latest);
}
function makeWallCamera(id) {
  const tile=node('article','wall-tile');tile.dataset.printerId=id;
  const button=node('button','wall-camera-button'),video=node('video'),image=node('img'),placeholder=node('div','wall-placeholder'),message=node('p'),label=node('span','wall-label'),dot=node('span','dot'),name=node('span');
  video.autoplay=true;video.muted=true;video.playsInline=true;video.hidden=true;
  image.hidden=true;image.alt=t('Камера принтера');placeholder.append(node('span','wall-camera-icon','◉'),message);label.append(dot,name);
  button.append(video,image,placeholder,label);button.addEventListener('click',()=>choose(id));
  const retry=node('button','wall-retry secondary',t('Повторить'));retry.hidden=true;
  const handle=node('button','camera-drag reorder-handle','⠿');
  handle.title=t('Перетащите на другую камеру, чтобы поменять их местами. Можно использовать стрелки.');
  const controls=createWallControls({getItem:()=>latest?.printers.find(item=>item.printer.id===id),isBusy:()=>controlBusy||printerTools.isBusy()||wallGroup.isBusy()||!serverOnline,
    onLight:item=>printerTools.toggleLight(item),onPause:item=>sendControl(item.status==='paused'?'resume':'pause',item),
    onStop:item=>openConfirm('stop',item),onObjects:item=>printerTools.openObjects(item),onError:text=>toast(text,true),
    onFullscreen:()=>toggleWallFullscreen(tile),isFullscreen:()=>document.fullscreenElement===tile||tile.classList.contains('camera-expanded')});
  const progress=createWallProgress();tile.append(button,retry,handle,controls.element,progress.element);$('camera-grid').append(tile);
  const player=new CameraPlayer({video,image,placeholder,message,negotiate:negotiateCamera,relay:remote,apiBase,onState:phase=>{dot.classList.toggle('live',phase==='live');retry.hidden=phase!=='error';}});
  retry.addEventListener('click',()=>player.start());player.desired=true;
  return {tile,button,name,image,player,handle,controls,progress};
}
function syncWall(printers) {
  const available=new Map(printers.map(item=>[item.printer.id,item]));
  for(const [id,entry] of wallPlayers)if(!wallIds.has(id)||!available.has(id)){disposeWall(entry);wallPlayers.delete(id);}
  for(const id of wallIds) {
    const item=available.get(id);if(!item)continue;
    let entry=wallPlayers.get(id);if(!entry){entry=makeWallCamera(id);wallPlayers.set(id,entry);}
    entry.name.textContent=item.printer.name;entry.button.setAttribute('aria-label',t('Открыть принтер ')+item.printer.name);entry.image.alt=t('Камера ')+item.printer.name;
    entry.tile.style.order=[...wallIds].indexOf(id);entry.handle.dataset.label=item.printer.name;entry.handle.setAttribute('aria-label',t('Переместить камеру ')+item.printer.name);
    entry.controls.update();
    entry.progress.update(item);
    entry.player.update(item,serverOnline&&item.connection.online);
  }
  $('wall-empty').hidden=wallPlayers.size>0;
  $('camera-grid').hidden=wallPlayers.size===0;
  $('camera-grid').classList.toggle('single-camera',wallPlayers.size===1);
}
$('view-switch').addEventListener('click',()=>setView(viewMode==='printer'?'cameras':'printer'));
$('camera-scale').addEventListener('input',event=>setCameraScale(event.target.value));
$('camera-scale-reset').addEventListener('click',()=>setCameraScale(100));
const printerReorder=installReorder({container:$('printer-list'),itemSelector:'.printer-row',handleSelector:'.printer-drag',idKey:'id',getOrder:()=>printerOrder,swap:swapPrinters});
const cameraReorder=installReorder({container:$('camera-grid'),itemSelector:'.wall-tile',handleSelector:'.camera-drag',idKey:'printerId',getOrder:()=>[...wallIds],swap:swapCameras});
setCameraScale(cameraScale);
$('sidebar-toggle').addEventListener('click',()=>setSidebarHidden(!sidebarHidden));
setSidebarHidden(sidebarHidden);
$('camera-toggle').addEventListener('click',()=>{if(dashboardCamera.desired&&dashboardCamera.phase!=='error')dashboardCamera.stop();else dashboardCamera.start();});
$('camera-full').addEventListener('click',()=>{$('camera-stage').requestFullscreen?.().catch(()=>toast(t('Не удалось развернуть видео'),true));});
async function toggleWallFullscreen(tile){
  if(document.fullscreenElement===tile){await document.exitFullscreen();return;}
  if(tile.classList.contains('camera-expanded')){tile.classList.remove('camera-expanded');document.body.classList.remove('camera-expanded-active');}
  else if(tile.requestFullscreen)await tile.requestFullscreen();
  else{tile.classList.add('camera-expanded');document.body.classList.add('camera-expanded-active');}
  for(const entry of wallPlayers.values())entry.controls.update();
}
document.addEventListener('fullscreenchange',()=>{dashboardOverlay.update();for(const entry of wallPlayers.values())entry.controls.update();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){for(const entry of wallPlayers.values())entry.tile.classList.remove('camera-expanded');document.body.classList.remove('camera-expanded-active');for(const entry of wallPlayers.values())entry.controls.update();}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
window.addEventListener('pagehide',()=>{dashboardCamera.stop();stopWall();jobThumbnail.dispose();});
window.addEventListener('pageshow',event=>{if(event.persisted){if(viewMode==='printer')dashboardCamera.desired=true;render(latest);poll();}});
dashboardCamera.desired=true;
setupPhone({api,remote});
poll();
