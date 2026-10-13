import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.101';
const svg=path=>'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'+path+'</svg>';
const icons={light:svg('<path d="M9 18h6m-5 3h4M8.5 14.5a6 6 0 1 1 7 0c-.9.7-1.5 1.8-1.5 3.5h-4c0-1.7-.6-2.8-1.5-3.5Z"/>'),pause:svg('<path d="M8 5v14M16 5v14" stroke-width="3"/>'),stop:svg('<rect x="5" y="5" width="14" height="14" fill="currentColor"/>')};
export function eligible(item,action){
  if(!item?.connection.online||item.connection.stale)return false;
  if(action==='light')return typeof item.light_on==='boolean'&&item.capabilities?.light!==false;
  return !!item.controls?.[action]&&(action!=='pause'||item.status!=='paused');
}
export function groupPlan(printers,ids,action){
  const members=[...ids].sort(),included=printers.filter(item=>members.includes(item.printer.id));
  const targets=included.filter(item=>eligible(item,action));
  return {action,members,targets:structuredClone(targets),skipped:included.filter(item=>!eligible(item,action)).map(item=>item.printer.name),
    value:action==='light'?targets.some(item=>!item.light_on):undefined};
}
export function targetUnchanged(saved,current,action){
  return eligible(current,action)&&saved.printer.ip===current.printer.ip&&saved.connection.sessions===current.connection.sessions
    &&saved.print.file===current.print.file&&(saved.print.job_id??null)===(current.print.job_id??null);
}
export function setupWallGroup({api,getPrinters,getIds,isBusy,onStateChange=()=>{},onFinished=()=>{}}){
  const $=id=>document.getElementById(id);let busy=false,plan=null;
  function update(){
    for(const action of ['light','pause','stop']){
      const button=$('wall-all-'+action),candidate=groupPlan(getPrinters(),getIds(),action);
      button.disabled=busy||isBusy()||!candidate.targets.length;
      button.title=action==='light'?(candidate.value?t('Включить свет на камерах вкладки'):t('Выключить свет на камерах вкладки')):action==='pause'?t('Поставить на паузу принтеры камер вкладки'):t('Остановить печать на принтерах камер вкладки');
      button.setAttribute('aria-label',button.title);button.innerHTML=icons[action]+'<span>'+({light:t('Свет'),pause:t('Пауза'),stop:t('Стоп')})[action]+'</span>';
      if(action==='light')button.setAttribute('aria-pressed',String(candidate.targets.length>0&&!candidate.value));
    }
  }
  function open(action){
    if(busy||isBusy())return;plan=groupPlan(getPrinters(),getIds(),action);if(!plan.targets.length)return;
    $('wall-group-title').textContent=action==='stop'?t('Остановить печать на камерах вкладки?'):action==='pause'?t('Поставить принтеры камер вкладки на паузу?'):plan.value?t('Включить подсветку камер вкладки?'):t('Выключить подсветку камер вкладки?');
    $('wall-group-targets').textContent=plan.targets.map(item=>item.printer.name+' · '+item.printer.ip).join('\n');
    $('wall-group-detail').textContent=(action==='stop'?t('Текущие задания будут отменены. '):'')+t('Действие применяется только к перечисленным принтерам.')+(plan.skipped.length?t(' Пропущены недоступные или не требующие действия: ')+plan.skipped.join(', ')+'.':'');
    $('wall-group-results').textContent='';$('wall-group-submit').hidden=false;$('wall-group-submit').disabled=false;
    $('wall-group-submit').className=action==='stop'?'danger':'primary';$('wall-group-dialog').showModal();
  }
  for(const action of ['light','pause','stop'])$('wall-all-'+action).addEventListener('click',()=>open(action));
  $('wall-group-dialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  $('wall-group-close').addEventListener('click',()=>{if(!busy)$('wall-group-dialog').close();});
  $('wall-group-submit').addEventListener('click',async()=>{
    if(!plan||busy||isBusy())return;
    if(JSON.stringify([...getIds()].sort())!==JSON.stringify(plan.members)){$('wall-group-results').textContent=t('Состав камер изменился. Закройте окно и выберите действие заново.');$('wall-group-submit').disabled=true;return;}
    busy=true;$('wall-group-submit').disabled=true;$('wall-group-close').disabled=true;update();onStateChange();
    const action=plan,results=[];
    try {
      for(const saved of action.targets){
        const current=getPrinters().find(item=>item.printer.id===saved.printer.id);
        if(!getIds().has(saved.printer.id)||!targetUnchanged(saved,current,action.action)){results.push(saved.printer.name+t(': пропущен — подключение или задание изменилось'));continue;}
        const body={action:action.action,expected_ip:saved.printer.ip,expected_session:saved.connection.sessions,expected_file:saved.print.file,expected_job:saved.print.job_id??null,request_id:crypto.randomUUID()};
        if(action.action==='light')body.value=action.value;
        try {const result=await api('/printers/'+encodeURIComponent(saved.printer.id)+(action.action==='light'?'/settings-command':'/control'),'POST',body);results.push(saved.printer.name+': '+result.message);}
        catch(error){results.push(saved.printer.name+': '+t(error.message));}
        $('wall-group-results').textContent=results.join('\n');
      }
    } finally {busy=false;plan=null;$('wall-group-submit').hidden=true;$('wall-group-close').disabled=false;update();onStateChange();await onFinished();}
  });
  update();return {update,isBusy:()=>busy};
}
