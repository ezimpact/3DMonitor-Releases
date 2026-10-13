import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setupFiles} from '../k1monitor/static/files-ui.js';

function fixture({onApi,onGet,inline=false}={}){
  class Element{
    constructor(){this.children=[];this.handlers={};this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.open=false;}
    append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(){}remove(){}click(){this.handlers.click?.();}
    addEventListener(event,handler){this.handlers[event]=handler;}showModal(){this.open=true;}close(){this.open=false;this.handlers.close?.();}
    querySelectorAll(tag){return this.children.flatMap(node=>[...(node.type===tag?[node]:[]),...node.querySelectorAll(tag)]);}
  }
  const elements=new Map();globalThis.document={getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement(tag){const node=new Element();node.type=tag;return node;}};
  const printer={printer:{id:'p1',name:'Max',ip:'192.168.4.104'},connection:{online:true,stale:false,sessions:1},state_code:1,status:'printing',print:{file:'/usr/data/printer_data/gcodes/active.gcode'},controls:{start:false}};
  const data={printer:printer.printer,session:1,revision:'revision',files:[{id:'a',name:'active.gcode'},{id:'b',name:'free.gcode'},{id:'c',name:'other.gcode'}]};
  const calls=[],printers=[printer];
  const ui=setupFiles({api:async(path,method='GET',body)=>{calls.push({path,method,body});assert.ok(!path.endsWith('/files/start'),'Printing must never be tested');if(onApi&&method!=='GET')return onApi(path,method,body);assert.equal(method,'GET','No command may be sent from these tests');return onGet?onGet(path):structuredClone(data);},getPrinters:()=>printers,inline});ui.update(printer);
  const $=id=>document.getElementById(id);
  return {ui,printer,printers,calls,$,async open(){await $('files-open').handlers.click();await new Promise(resolve=>setImmediate(resolve));}};
}

test('inline files follow the selected printer and discard a late reply from the previous printer',async()=>{
  const pending=new Map(),f=fixture({inline:true,onGet:path=>new Promise(resolve=>pending.set(path,resolve))});
  const second={printer:{id:'p2',name:'Second',ip:'192.168.254.2'},connection:{online:true,stale:false,sessions:2},status:'idle',state_code:0,print:{file:null},controls:{start:true}};
  f.printers.push(second);f.ui.update(second);
  pending.get('/printers/p2/files')({printer:second.printer,session:2,revision:'r2',files:[{id:'new',name:'second.gcode'}]});
  await new Promise(resolve=>setImmediate(resolve));
  pending.get('/printers/p1/files')({printer:f.printer.printer,session:1,revision:'old',files:[{id:'old',name:'wrong.gcode'}]});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.$('files-list').children.length,1);assert.equal(f.$('files-list').children[0].children[2].children[0].textContent,'second.gcode');
  assert.match(f.$('files-printer').textContent,/Second/);f.ui.update(second);assert.equal(f.calls.length,2);
  second.connection.online=false;f.ui.update(second);assert.equal(f.$('files-upload').disabled,true);assert.equal(f.$('files-list').children.length,0);
  second.connection.online=true;second.connection.sessions=3;f.ui.update(second);assert.equal(f.calls.length,3);
  assert.ok(f.calls.every(call=>call.method==='GET'));
});
test('select all leaves the active print unchecked and renders file thumbnails',async()=>{
  const f=fixture();await f.open();const rows=f.$('files-list').children;
  assert.equal(rows.length,3);assert.equal(rows[0].children[0].disabled,true);
  assert.match(rows[1].children[1].children[0].src,/\/files\/b\/thumbnail\?/);
  f.$('files-all').checked=true;f.$('files-all').handlers.change();
  assert.equal(f.$('files-list').children[0].children[0].checked,false);assert.equal(f.$('files-delete').textContent,'Удалить выбранные (2)');
  f.$('files-list').children[1].children[0].checked=false;f.$('files-list').children[1].children[0].handlers.change();assert.equal(f.$('files-all').indeterminate,true);
});

test('folders navigate with encoded GET, scope files and clear selected deletions',async()=>{
 const f=fixture({onGet:path=>{
   const directory=new URL('http://fixture'+path).searchParams.get('directory')??'';
   return {printer:f.printer.printer,session:1,revision:'folders',files:[{id:'root',name:'free.gcode',relative_path:'free.gcode'},{id:'nested',name:'free.gcode',relative_path:'Модели/free.gcode'}],directory,directories:directory?[]:[{name:'Модели',path:'Модели'},{name:'Empty',path:'Empty'}]};
 }});
 await f.open();assert.equal(f.$('files-list').children.length,3);f.$('files-all').checked=true;f.$('files-all').handlers.change();assert.equal(f.$('files-delete').textContent,'Удалить выбранные (1)');
 await f.$('files-list').children[0].handlers.click();assert.match(f.calls[1].path,/directory=%D0/);assert.equal(f.$('files-list').children.length,1);assert.equal(f.$('files-delete').textContent,'Удалить выбранные (0)');assert.match(f.$('files-list').children[0].children[2].children[1].textContent,/Модели\/free.gcode/);
 await f.$('files-up').handlers.click();assert.equal(f.$('files-list').children.length,3);assert.ok(f.calls.every(call=>call.method==='GET'));
 await f.$('files-list').children[1].handlers.click();assert.equal(f.$('files-list').children.length,0);assert.equal(f.$('files-empty').hidden,false);
});

test('same basename in another folder is distinguished in print confirmation without sending a print command',async()=>{
 const f=fixture({onGet:path=>({printer:f.printer.printer,session:1,revision:'folders',files:[{id:'other',name:'active.gcode',relative_path:'Folder/active.gcode'}],directories:path.includes('?')?[]:[{name:'Folder',path:'Folder'}]})});
 f.printer.print.file='Root/active.gcode';await f.open();await f.$('files-list').children[0].handlers.click();assert.equal(f.$('files-list').children[0].children[0].disabled,false);
 f.printer.status='idle';f.printer.state_code=0;f.printer.controls.start=true;f.ui.update(f.printer);f.$('files-list').children[0].children[3].children[0].handlers.click();assert.equal(f.$('start-file').textContent,'Folder/active.gcode');assert.ok(f.calls.every(call=>call.method==='GET'));
});
test('filtered select all selects only shown files and deletion confirmation stays on its printer',async()=>{
  const f=fixture();await f.open();f.$('files-search').value='free';f.$('files-search').handlers.input();
  f.$('files-all').checked=true;f.$('files-all').handlers.change();assert.equal(f.$('files-delete').textContent,'Удалить выбранные (1)');
  f.ui.update({printer:{id:'p2'}});f.$('files-delete').handlers.click();
  assert.equal(f.$('delete-files-printer').textContent,'Max · 192.168.4.104');assert.equal(f.$('delete-files-names').textContent,'free.gcode');assert.equal(f.calls.length,1);
});
test('disconnect and catalog refresh disable deletion and clear old selection',async()=>{
  const f=fixture();await f.open();f.$('files-all').checked=true;f.$('files-all').handlers.change();
  f.printer.connection.stale=true;f.ui.update(f.printer);assert.equal(f.$('files-delete').disabled,true);
  f.printer.connection.stale=false;await f.$('files-refresh').handlers.click();assert.equal(f.$('files-delete').textContent,'Удалить выбранные (0)');assert.equal(f.calls.length,2);
});

test('Play icons preselect their calibration option without sending a print request',async()=>{
  const f=fixture();f.printer.status='idle';f.printer.state_code=0;f.printer.controls.start=true;await f.open();
  const actions=f.$('files-list').children[1].children[3].children;
  assert.match(actions[0].title,/без калибровки/);assert.match(actions[1].title,/с калибровкой/);assert.match(actions[2].title,/Скачать/);
  actions[0].handlers.click();assert.equal(f.$('start-calibrate').checked,false);assert.equal(f.$('start-without-calibration').checked,true);
  f.$('start-dialog').close();actions[1].handlers.click();assert.equal(f.$('start-calibrate').checked,true);assert.equal(f.$('start-without-calibration').checked,false);
  assert.equal(f.calls.length,1);
});

test('send menu targets the requested printer while file list retains its source',async()=>{
  const f=fixture({onApi:(path,method,body)=>{assert.equal(method,'POST');assert.equal(path,'/printers/p1/files/b/transfer');assert.equal(body.target_id,'p2');assert.equal(body.target_ip,'192.168.4.114');assert.equal(body.expected_ip,'192.168.4.104');return {phase:'complete',message:'Sent'};}});
  f.printers.push({printer:{id:'p2',name:'Second',ip:'192.168.4.114'},connection:{online:true,stale:false,sessions:7}});
  await f.open();f.ui.update({printer:{id:'p2'}});
  const choice=f.$('files-list').children[1].children[3].children[3].children[1].children[1];assert.equal(choice.textContent,'Second');
  choice.handlers.click();await new Promise(resolve=>setImmediate(resolve));assert.equal(f.calls.length,2);assert.equal(f.calls[1].body.target_session,7);
});

test('upload picker keeps its printer when dashboard selection changes',async()=>{
  const f=fixture({onApi:(path,method,body)=>{assert.equal(method,'POST');assert.match(path,/^\/printers\/p1\/files\/upload\?/);assert.ok(body instanceof Blob);return {phase:'complete',message:'Uploaded'};}});
  await f.open();f.$('files-upload').handlers.click();f.ui.update({printer:{id:'other'}});
  const file=new Blob(['; only a test file']);file.name='fixture.gcode';f.$('files-upload-input').files=[file];await f.$('files-upload-input').handlers.change();
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);assert.equal(f.$('files-transfer-note').textContent,'Uploaded');
});

test('offline destination is listed but cannot receive a transfer',async()=>{
  const f=fixture();f.printers.push({printer:{id:'p2',name:'Offline',ip:'192.168.4.114'},connection:{online:false,stale:true,sessions:1}});await f.open();
  const choice=f.$('files-list').children[1].children[3].children[3].children[1].children[1];assert.equal(choice.disabled,true);choice.handlers.click();assert.equal(f.calls.length,1);
});

test('only hovering the send control opens its menu, with no network calls',async()=>{
  const f=fixture();await f.open();const row=f.$('files-list').children[1],send=row.children[3].children[3];
  assert.equal(row.handlers.mouseenter,undefined);send.handlers.mouseenter();assert.equal(send.open,true);
  send.handlers.mouseleave();assert.equal(send.open,false);
  f.printer.connection.stale=true;send.handlers.mouseenter();assert.equal(send.open,false);assert.equal(f.calls.length,1);
});

test('direct dropped G-code upload pins its target without needing a loaded file catalog or printing',async()=>{
 let finish;const f=fixture({inline:true,onApi:async(path,method)=>{
  assert.ok(path.includes('/files/upload?'));assert.equal(method,'POST');await new Promise(resolve=>finish=resolve);return {phase:'complete',message:'Mocked'};
 }});
 const first=structuredClone(f.printer),file={name:'drop.gcode',size:100};
 const upload=f.ui.uploadFile(file,first);await new Promise(resolve=>setImmediate(resolve));
 const second={...structuredClone(first),printer:{id:'p2',name:'Second',ip:'192.168.254.2'},connection:{online:true,stale:false,sessions:2}};
 f.printers.push(second);f.ui.update(second);finish();await upload;
 const write=f.calls.find(call=>call.method==='POST');assert.match(write.path,/^\/printers\/p1\/files\/upload\?/);assert.match(write.path,/expected_session=1/);assert.equal(write.body,file);
 assert.ok(f.calls.every(call=>!call.path.includes('/files/start')));
});
test('dropped upload rejects a changed printer session and unsupported extension without a write',async()=>{
 const f=fixture({inline:true}),first=structuredClone(f.printer);f.printer.connection.sessions=2;
 await f.ui.uploadFile({name:'drop.gcode',size:100},first);assert.ok(f.calls.every(call=>call.method==='GET'));
 await f.ui.uploadFile({name:'drop.exe',size:100},f.printer);assert.ok(f.calls.every(call=>call.method==='GET'));
});

test('default file page shows the latest ten and page changes clear hidden selections',async()=>{
 globalThis.localStorage={getItem:()=>null,setItem(){}};
 const files=Array.from({length:36},(_,i)=>({id:String(i),name:'job-'+i+'.gcode',modified:1000+i}));
 const f=fixture({onGet:()=>({printer:f.printer.printer,session:1,revision:'many',files})});await f.open();
 assert.equal(f.$('files-list').children.length,10);assert.equal(f.$('files-list').children[0].children[2].children[0].textContent,'job-35.gcode');
 f.$('files-all').checked=true;f.$('files-all').handlers.change();assert.match(f.$('files-delete').textContent,/10/);
 f.$('files-page-next').handlers.click();assert.equal(f.$('files-list').children[0].children[2].children[0].textContent,'job-25.gcode');assert.equal(f.$('files-delete').disabled,true);
 f.$('files-page-prev').handlers.click();assert.equal(f.$('files-list').children[0].children[2].children[0].textContent,'job-35.gcode');
});
test('file page size offers 10, 25, 50, 100 and is restored from browser preference',async()=>{
 let saved='25';globalThis.localStorage={getItem:()=>saved,setItem:(_key,value)=>saved=value};
 const files=Array.from({length:125},(_,i)=>({id:String(i),name:'job-'+i+'.gcode'}));
 const f=fixture({onGet:()=>({printer:f.printer.printer,session:1,revision:'many',files})});await f.open();assert.equal(f.$('files-list').children.length,25);
 for(const size of [10,25,50,100]){f.$('files-page-size').value=String(size);f.$('files-page-size').handlers.change();assert.equal(f.$('files-list').children.length,size);assert.equal(saved,String(size));}
 f.$('files-page-next').handlers.click();assert.equal(f.$('files-list').children.length,25);assert.equal(f.$('files-page-next').disabled,true);
});
test('file search considers older pages, resets pagination and retains order without dates',async()=>{
 globalThis.localStorage={getItem:()=>null,setItem(){}};
 const files=Array.from({length:35},(_,i)=>({id:String(i),name:'job-'+i+'.gcode'}));
 const f=fixture({onGet:()=>({printer:f.printer.printer,session:1,revision:'many',files})});await f.open();
 assert.equal(f.$('files-list').children[0].children[2].children[0].textContent,'job-0.gcode');
 f.$('files-page-next').handlers.click();f.$('files-search').value='job-34';f.$('files-search').handlers.input();assert.equal(f.$('files-list').children.length,1);assert.equal(f.$('files-list').children[0].children[2].children[0].textContent,'job-34.gcode');assert.equal(f.$('files-page-prev').disabled,true);
});

test('numbered pages reach the last file page directly and react to page size and search',async()=>{
 globalThis.localStorage={getItem:()=>null,setItem(){}};
 const files=Array.from({length:29},(_,i)=>({id:String(i),name:'job-'+i+'.gcode'}));
 const f=fixture({onGet:()=>({printer:f.printer.printer,session:1,revision:'pages',files})});await f.open();
 assert.deepEqual(f.$('files-page-numbers').children.map(button=>button.textContent),['1','2','3']);
 f.$('files-all').checked=true;f.$('files-all').handlers.change();f.$('files-page-numbers').children[2].click();
 assert.equal(f.$('files-list').children.length,9);assert.equal(f.$('files-list').children[0].children[2].children[0].textContent,'job-20.gcode');assert.equal(f.$('files-page-next').disabled,true);assert.equal(f.$('files-delete').disabled,true);
 f.$('files-page-size').value='25';f.$('files-page-size').handlers.change();assert.deepEqual(f.$('files-page-numbers').children.map(button=>button.textContent),['1','2']);
 f.$('files-search').value='job-28';f.$('files-search').handlers.input();assert.deepEqual(f.$('files-page-numbers').children.map(button=>button.textContent),['1']);
 assert.ok(f.calls.every(call=>call.method==='GET'));
});

test('root breadcrumbs have no All files label and the home button returns from a subfolder',async()=>{
 const f=fixture({onGet:path=>({printer:f.printer.printer,session:1,revision:'root',files:[{id:'nested',name:'part.gcode',relative_path:'Folder/part.gcode'}],directories:path.includes('?')?[]:[{name:'Folder',path:'Folder'}]})});
 await f.open();assert.equal(f.$('files-breadcrumbs').children.length,0);
 await f.$('files-list').children[0].handlers.click();assert.deepEqual(f.$('files-breadcrumbs').children.map(button=>button.textContent),['⌂','Folder']);
 await f.$('files-breadcrumbs').children[0].handlers.click();assert.equal(f.$('files-breadcrumbs').children.length,0);
});
