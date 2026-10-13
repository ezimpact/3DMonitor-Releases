import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.101';
// Printing requires an explicit confirmation. No launch probes or tests.
export function setupFiles({api,getPrinters,apiBase='/api/v1',inline=false}){
  const $=id=>document.getElementById(id);
  let target=null,data=null,generation=0,confirmation=null,deletion=null,uploadTarget=null,busy=false,renderKey='';
  let pageSize=10,page=0,pageButtonsKey='';
  try{const saved=Number(localStorage.getItem('k1-files-page-size'));if([10,25,50,100].includes(saved))pageSize=saved;}catch(_){}
  $('files-page-size').value=String(pageSize);
  const checked=new Set(),current=()=>getPrinters().find(item=>item.printer.id===target);
  let desiredItem=null,selectionKey=null,folder='';
  function followSelection(item){
    desiredItem=item;if(busy)return;
    const fresh=item?.connection.online&&!item.connection.stale;
    const key=item?JSON.stringify([item.printer.id,item.printer.ip,item.connection.sessions,!!fresh]):'';
    if(key===selectionKey)return;selectionKey=key;
    ++generation;target=item?.printer.id??null;data=null;folder='';page=0;checked.clear();renderKey='';error();breadcrumbs();
    $('files-search').value='';$('files-transfer-note').textContent='';$('files-list').replaceChildren();
    $('files-printer').textContent=item?item.printer.name:'';$('files-note').textContent='';$('files-empty').hidden=false;
    $('files-empty').textContent=fresh?t('Читаем файлы принтера…'):t('Файлы доступны после подключения к принтеру.');selection();
    if(fresh)refresh();
  }
  const connected=()=>{const item=current();return !!(item&&data&&item.printer.ip===data.printer.ip&&item.connection.sessions===data.session&&item.connection.online&&!item.connection.stale);};
  const available=()=>connected()&&current().controls.start;
  const protectedFile=file=>{const item=current(),path=item?.print.file?.replace(/^\/usr\/data\/printer_data\/gcodes\//,'').replace(/^\//,'');return !!(file.read_only||item&&(![0,2,3,4].includes(item.state_code)||item.status==='preparing')&&(path?.includes('/')?filePath(file)===path:file.name===path));};
  const filePath=file=>file.relative_path??file.name;
  const filtered=()=>{
    const query=$('files-search').value.toLocaleLowerCase(locale),prefix=folder?folder+'/':'';
    return (data?.files.filter(file=>{const path=filePath(file);return query?path.startsWith(prefix)&&path.toLocaleLowerCase(locale).includes(query):path.split('/').slice(0,-1).join('/')===folder;})??[]).slice().sort((a,b)=>(b.modified??0)-(a.modified??0));
  };
  const visible=()=>filtered().slice(page*pageSize,(page+1)*pageSize);
  function breadcrumbs(){
    const list=$('files-breadcrumbs');list.replaceChildren();
    const parts=folder?folder.split('/'):[],paths=folder?[['⌂',''],...parts.map((name,index)=>[name,parts.slice(0,index+1).join('/')])]:[];
    for(const [name,path] of paths){const button=document.createElement('button');button.type='button';button.textContent=name;if(!path)button.setAttribute('aria-label',t('Корневая папка'));button.disabled=busy||path===folder;button.addEventListener('click',()=>openFolder(path));list.append(button);}
    $('files-up').disabled=busy||!connected()||!folder;
  }
  function openFolder(path){
    if(busy||!connected())return;folder=path;page=0;checked.clear();$('files-search').value='';return refresh();
  }
  function error(text=''){$('files-error').textContent=t(text);$('files-error').hidden=!text;}
  const icon=(kind)=>'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+({play:'<path d="m8 4 12 8-12 8Z" fill="currentColor" stroke="none"/>',calibrate:'<path d="m8 3 12 7-12 7Z" fill="currentColor" stroke="none"/><path d="M5 21h16"/>',download:'<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',send:'<path d="m3 3 18 9-18 9 4-9-4-9Zm4 9h14"/>'})[kind]+'</svg>';
  function actionButton(kind,label){const button=document.createElement('button');button.type='button';button.className='file-action secondary';button.innerHTML=icon(kind);button.title=label;button.setAttribute('aria-label',label);return button;}
  function startConfirmation(file,calibrate,catalog=data,from_history=false){
    const item=getPrinters().find(item=>item.printer.id===catalog?.printer.id);
    if(!item?.connection.online||item.connection.stale||!item.controls.start||item.printer.ip!==catalog.printer.ip||item.connection.sessions!==catalog.session||busy||calibrate&&item.capabilities?.calibration===false)return false;
    confirmation={...structuredClone(catalog),file:structuredClone(file),request_id:crypto.randomUUID(),from_history};
    $('start-printer').textContent=`${catalog.printer.name} · ${catalog.printer.ip}`;$('start-file').textContent=filePath(file);
    $('start-calibrate').disabled=current()?.capabilities?.calibration===false;$('start-calibrate').title=current()?.capabilities?.calibration_label??t('Автокалибровка принтера');
    $('start-calibrate').checked=calibrate;$('start-without-calibration').checked=!calibrate;
    $('start-error').hidden=true;$('start-submit').disabled=false;$('start-dialog').showModal();return true;
  }
  async function transfer(file,destination){
    if(!connected()||busy||!destination.connection.online||destination.connection.stale)return;
    const source=structuredClone(data),to=structuredClone(destination),gen=generation;
    busy=true;error();$('files-transfer-note').textContent=tm`Отправляем «${file.name}» на ${to.printer.name}…`;renderKey='';render();
    try{
      const result=await api('/printers/'+encodeURIComponent(source.printer.id)+'/files/'+file.id+'/transfer','POST',{revision:source.revision,expected_session:source.session,expected_ip:source.printer.ip,target_id:to.printer.id,target_session:to.connection.sessions,target_ip:to.printer.ip,request_id:crypto.randomUUID()});
      if(gen===generation){$('files-transfer-note').textContent=t(result.message);if(result.phase!=='complete')error(result.message);}
    }catch(exc){if(gen===generation){$('files-transfer-note').textContent='';error(t(exc.message));}}
    finally{busy=false;if(inline)followSelection(desiredItem);renderKey='';if(data)render();}
  }
  async function download(file){
    if(!connected()||busy)return;
    const source=structuredClone(data),gen=generation;busy=true;error();$('files-transfer-note').textContent=t('Скачиваем «')+file.name+'»…';renderKey='';render();
    try{
      const response=await fetch(apiBase+'/printers/'+encodeURIComponent(source.printer.id)+'/files/'+file.id+'/download?'+new URLSearchParams({session:source.session,revision:source.revision,ip:source.printer.ip}),{credentials:'same-origin',signal:AbortSignal.timeout(180000)});
      if(!response.ok){const body=await response.json();throw new Error(body.detail??t('Не удалось скачать файл'));}
      const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=file.name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
      if(gen===generation)$('files-transfer-note').textContent=t('Файл передан браузеру для сохранения.');
    }catch(exc){if(gen===generation){$('files-transfer-note').textContent='';error(t(exc.message));}}
    finally{busy=false;if(inline)followSelection(desiredItem);renderKey='';if(data)render();}
  }
  function selection(){
    const selectable=visible().filter(file=>!protectedFile(file));
    $('files-all').checked=selectable.length>0&&selectable.every(file=>checked.has(file.id));
    $('files-all').indeterminate=selectable.some(file=>checked.has(file.id))&&!$('files-all').checked;
    $('files-all').disabled=busy||!connected()||!selectable.length;
    $('files-delete').textContent=tm`Удалить выбранные (${checked.size})`;
    $('files-delete').disabled=busy||!connected()||!checked.size;
    $('files-upload').disabled=busy||!connected();$('files-refresh').disabled=busy;
    const total=filtered().length;page=Math.min(page,Math.max(0,Math.ceil(total/pageSize)-1));
    $('files-page-prev').disabled=busy||page===0;
    $('files-page-next').disabled=busy||(page+1)*pageSize>=total;
    $('files-page-size').disabled=busy;
    $('files-page-info').textContent=total?tm`${page*pageSize+1}–${Math.min((page+1)*pageSize,total)} из ${total}`:t('0 файлов');
    const pages=Math.ceil(total/pageSize),buttonsKey=JSON.stringify([pages,page,busy]);
    if(buttonsKey!==pageButtonsKey){
      pageButtonsKey=buttonsKey;const numbers=$('files-page-numbers');numbers.replaceChildren();
      for(let index=0;index<pages;index++){
        const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=String(index+1);button.disabled=busy;
        if(index===page)button.setAttribute('aria-current','page');
        button.addEventListener('click',()=>{if(busy||page===index)return;page=index;checked.clear();render();});numbers.append(button);
      }
    }
    breadcrumbs();
  }
  function render(){
    page=Math.min(page,Math.max(0,Math.ceil(filtered().length/pageSize)-1));
    const list=$('files-list'),printer=current();
    const destinations=getPrinters().filter(item=>item.printer.id!==target);
    const key=JSON.stringify([data?.revision,folder,page,pageSize,data?.directories,$('files-search').value,connected(),available(),busy,printer?.printer,printer?.print.file,printer?.state_code,destinations.map(item=>[item.printer,item.connection.online,item.connection.stale,item.connection.sessions])]);
    if(key===renderKey){selection();return;}renderKey=key;list.replaceChildren();
    $('files-printer').textContent=printer?`${printer.printer.name} · ${printer.printer.ip}`:t('Принтер удалён');
    $('files-note').textContent=available()?t('Выберите файл и вариант калибровки перед запуском. На Moonraker выполняется проверка поверхности стола, если она доступна.'):t('Файлы доступны для просмотра. Печать можно запустить, когда принтер свободен. Файл текущей печати удалить нельзя.');
    if(folder)$('files-note').textContent+=t(' Загрузка новых файлов выполняется в корневой каталог.');
    if(data?.directory_notice)$('files-note').textContent+=' '+t(data.directory_notice);
    for(const directory of data?.directories??[]){
      if($('files-search').value&&!directory.name.toLocaleLowerCase(locale).includes($('files-search').value.toLocaleLowerCase(locale)))continue;
      const button=document.createElement('button');button.type='button';button.className='gcode-folder';button.textContent='▱ '+directory.name;button.setAttribute('aria-label',t('Открыть папку ')+directory.name);button.disabled=busy||!connected();button.addEventListener('click',()=>openFolder(directory.path));list.append(button);
    }
    for(const file of visible()){
      const row=document.createElement('div'),box=document.createElement('input'),preview=document.createElement('div'),info=document.createElement('div'),name=document.createElement('strong'),detail=document.createElement('small');
      row.className='gcode-file';box.type='checkbox';box.setAttribute('aria-label',t('Выбрать ')+file.name);box.checked=checked.has(file.id);box.disabled=busy||!connected()||protectedFile(file);
      if(protectedFile(file)){checked.delete(file.id);box.checked=false;}
      box.addEventListener('change',()=>{box.checked?checked.add(file.id):checked.delete(file.id);selection();});
      preview.className='file-preview';preview.textContent='▱';
      const img=document.createElement('img');img.alt=t('Миниатюра ')+file.name;img.loading='lazy';img.src=apiBase+'/printers/'+encodeURIComponent(target)+'/files/'+file.id+'/thumbnail?'+new URLSearchParams({session:data.session,revision:data.revision});
      img.addEventListener('load',()=>{preview.replaceChildren(img);},{once:true});img.addEventListener('error',()=>{img.remove();},{once:true});preview.append(img);
      name.textContent=file.name;detail.textContent=(file.size_bytes!=null?(file.size_bytes/1024/1024).toFixed(1)+t(' МБ'):t('Размер неизвестен'))+(file.modified?' · '+new Date(file.modified*1000).toLocaleString(locale):'')+(filePath(file).includes('/')?' · '+filePath(file):'');
      const actions=document.createElement('div');actions.className='file-actions';
      const play=actionButton('play',t('Печатать без калибровки · ')+file.name),calibrate=actionButton('calibrate',t('Печатать с калибровкой · ')+file.name),save=actionButton('download',t('Скачать · ')+file.name);
      play.disabled=!available()||busy;calibrate.disabled=play.disabled||printer?.capabilities?.calibration===false;calibrate.title=t(printer?.capabilities?.calibration_label)??calibrate.title;save.disabled=!connected()||busy;
      play.addEventListener('click',()=>startConfirmation(file,false));calibrate.addEventListener('click',()=>startConfirmation(file,true));save.addEventListener('click',()=>download(file));
      const send=document.createElement('details'),summary=document.createElement('summary'),menu=document.createElement('div'),heading=document.createElement('strong');send.className='file-send';summary.className='file-action secondary';summary.innerHTML=icon('send');summary.title=t('Отправить на…');summary.setAttribute('aria-label',t('Отправить на… · ')+file.name);menu.className='send-menu';heading.textContent=t('Отправить на…');menu.append(heading);
      summary.addEventListener('click',event=>{if(busy||!connected())event.preventDefault();});
      function positionMenu(){
        if(!send.open||!send.getBoundingClientRect)return;
        const edge=Math.min($('files-dialog').getBoundingClientRect().bottom,document.documentElement.clientHeight)-12;
        send.classList.toggle('menu-up',send.getBoundingClientRect().bottom+Math.min(menu.scrollHeight,240)>edge);
      }
      send.addEventListener('mouseenter',()=>{if(!busy&&connected()){send.open=true;positionMenu();}});
      send.addEventListener('toggle',positionMenu);
      send.addEventListener('mouseleave',()=>{if(!send.contains?.(document.activeElement))send.open=false;});
      for(const destination of destinations){const choice=document.createElement('button');choice.type='button';choice.textContent=destination.printer.name;choice.title=destination.printer.ip;choice.disabled=busy||!connected()||!destination.connection.online||destination.connection.stale;choice.addEventListener('click',()=>{send.open=false;transfer(file,getPrinters().find(item=>item.printer.id===destination.printer.id)??destination);});menu.append(choice);}
      if(!destinations.length){const empty=document.createElement('span');empty.textContent=t('Добавьте ещё один принтер');menu.append(empty);}
      send.append(summary,menu);actions.append(play,calibrate,save,send);
      info.append(name,detail);row.append(box,preview,info,actions);list.append(row);
    }
    $('files-empty').hidden=!!list.children.length;$('files-empty').textContent=data?.files.length?t('Файлы не найдены по запросу.'):t('G-code файлы не найдены.');selection();
  }
  async function refresh(){
    const gen=++generation;data=null;checked.clear();renderKey='';error();$('files-refresh').disabled=true;$('files-list').replaceChildren();$('files-empty').hidden=false;$('files-empty').textContent=t('Читаем файлы принтера…');selection();
    try{const result=await api('/printers/'+encodeURIComponent(target)+'/files'+(folder?'?'+new URLSearchParams({directory:folder}):''));if(gen!==generation)return;data=result;render();}
    catch(exc){if(gen===generation){error(t(exc.message));$('files-empty').hidden=true;}}
    finally{if(gen===generation)$('files-refresh').disabled=busy;}
  }
  $('files-up').addEventListener('click',()=>openFolder(folder.split('/').slice(0,-1).join('/')));
  $('files-open').addEventListener('click',()=>{if(inline){$('files-dialog').scrollIntoView?.({behavior:'smooth',block:'start'});return;}if(busy)return;target=$('files-open').dataset.printerId;folder='';confirmation=null;$('files-search').value='';$('files-transfer-note').textContent='';$('files-dialog').showModal();refresh();});
  $('files-upload').addEventListener('click',()=>{if(!connected()||busy)return;uploadTarget=structuredClone(data);$('files-upload-input').value='';$('files-upload-input').click();});
  async function uploadFile(file,action){
    if(!file||!action)return;
    if(busy)throw new Error(t('Дождитесь завершения передачи файла.'));
    const item=getPrinters().find(item=>item.printer.id===action.printer.id);
    if(!item||item.printer.ip!==action.printer.ip||item.connection.sessions!==action.session||!item.connection.online||item.connection.stale){error(t('Подключение изменилось. Обновите список.'));return;}
    if(!(/\.gcode$/i.test(file.name)||['octoprint','prusalink'].includes(item.printer.protocol)&&/\.gco$/i.test(file.name))||!file.size||file.size>512*1024*1024){error(t('Выберите непустой G-code размером до 512 МБ (.gcode; для OctoPrint/PrusaLink также .gco).'));return;}
    const gen=generation;busy=true;error();$('files-transfer-note').textContent=tm`Загружаем «${file.name}» на ${action.printer.name}…`;renderKey='';render();
    try{
      const result=await api('/printers/'+encodeURIComponent(action.printer.id)+'/files/upload?'+new URLSearchParams({filename:file.name,expected_session:action.session,expected_ip:action.printer.ip,request_id:crypto.randomUUID()}),'POST',file);
      if(gen===generation){busy=false;await refresh();$('files-transfer-note').textContent=t(result.message);if(result.phase!=='complete')error(result.message);}
    }catch(exc){if(gen===generation){$('files-transfer-note').textContent='';error(t(exc.message));}}
    finally{busy=false;if(inline)followSelection(desiredItem);renderKey='';if(data)render();}
  }
  $('files-upload-input').addEventListener('change',()=>{const file=$('files-upload-input').files?.[0];$('files-upload-input').value='';return uploadFile(file,uploadTarget).catch(exc=>error(t(exc.message)));});
  $('files-refresh').addEventListener('click',refresh);$('files-search').addEventListener('input',()=>{page=0;checked.clear();render();});
  $('files-page-size').addEventListener('change',()=>{const value=Number($('files-page-size').value);if(![10,25,50,100].includes(value))return;pageSize=value;page=0;checked.clear();try{localStorage.setItem('k1-files-page-size',String(value));}catch(_){}render();});
  $('files-page-prev').addEventListener('click',()=>{if(busy||page===0)return;page--;checked.clear();render();});
  $('files-page-next').addEventListener('click',()=>{if(busy||(page+1)*pageSize>=filtered().length)return;page++;checked.clear();render();});
  $('files-dialog').addEventListener('close',()=>{generation++;data=null;checked.clear();$('files-refresh').disabled=false;});
  $('files-all').addEventListener('change',()=>{for(const file of visible().filter(file=>!protectedFile(file)))$('files-all').checked?checked.add(file.id):checked.delete(file.id);renderKey='';render();});
  $('files-delete').addEventListener('click',()=>{
    if(!connected()||busy)return;
    const files=data.files.filter(file=>checked.has(file.id)&&!protectedFile(file));if(!files.length)return;
    deletion={...structuredClone(data),files,request_id:crypto.randomUUID()};
    $('delete-files-printer').textContent=`${data.printer.name} · ${data.printer.ip}`;
    $('delete-files-names').textContent=files.map(file=>filePath(file)).join('\n');$('delete-files-error').hidden=true;$('delete-files-submit').disabled=false;$('delete-files-dialog').showModal();
  });
  $('delete-files-submit').addEventListener('click',async()=>{
    if(busy||!deletion)return;const action=deletion;
    busy=true;$('delete-files-submit').disabled=true;$('files-refresh').disabled=true;render();
    try{
      const result=await api('/printers/'+encodeURIComponent(action.printer.id)+'/files/delete','POST',{file_ids:action.files.map(file=>file.id),revision:action.revision,request_id:action.request_id,expected_session:action.session,expected_ip:action.printer.ip});
      $('delete-files-dialog').close();busy=false;await refresh();error(result.phase==='unconfirmed'?result.message:'');
    }catch(exc){$('delete-files-error').textContent=t(exc.message)+t(' Обновите список перед новым удалением.');$('delete-files-error').hidden=false;}
    finally{busy=false;if(inline)followSelection(desiredItem);$('delete-files-submit').disabled=false;$('files-refresh').disabled=false;renderKey='';if(data)render();}
  });
  $('start-submit').addEventListener('click',async()=>{
    if(busy||!confirmation)return;
    const action=confirmation,item=getPrinters().find(item=>item.printer.id===action.printer.id);
    if(!item?.controls.start||item.printer.ip!==action.printer.ip||item.connection.sessions!==action.session){$('start-error').textContent=t('Состояние принтера изменилось. Закройте окно и обновите список.');$('start-error').hidden=false;return;}
    busy=true;$('start-submit').disabled=true;$('start-error').hidden=true;
    try{
      const command=await api('/printers/'+encodeURIComponent(action.printer.id)+'/files/start','POST',{file_id:action.file.id,revision:action.revision,request_id:action.request_id,expected_session:action.session,expected_ip:action.printer.ip,calibrate:$('start-calibrate').checked});
      $('start-dialog').close();if(action.from_history)$('history-dialog').close();$('files-note').textContent=command.message;for(const button of $('files-list').querySelectorAll('button'))button.disabled=true;
    }catch(exc){$('start-error').textContent=t(exc.message);$('start-error').hidden=false;}
    finally{busy=false;if(inline)followSelection(desiredItem);$('start-submit').disabled=false;}
  });
  return {uploadFile(file,snapshot){return uploadFile(file,{printer:structuredClone(snapshot.printer),session:snapshot.connection.sessions});},async prepareHistoryPrint(printerId,recordId){
    if(busy||target!==printerId)throw new Error(t('Выберите этот принтер и дождитесь завершения текущей операции.'));
    const gen=generation,result=await api('/printers/'+encodeURIComponent(printerId)+'/history/'+encodeURIComponent(recordId)+'/file');
    const file=result.catalog.files.find(file=>file.id===result.file_id);
    return ()=>{if(gen!==generation||target!==printerId||!file||!startConfirmation(file,false,result.catalog,true))throw new Error(t('Состояние принтера изменилось. Обновите историю и повторите выбор.'));};
  },update(item){$('files-open').disabled=busy||!item;$('files-open').dataset.printerId=item?.printer.id??'';if(inline)followSelection(item);if((inline||$('files-dialog').open)&&data)render();}};
}
