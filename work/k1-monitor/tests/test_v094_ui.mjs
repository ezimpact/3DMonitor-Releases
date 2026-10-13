import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setupPrinterTools} from '../k1monitor/static/printer-tools.js';
import {createWallProgress} from '../k1monitor/static/wall-controls.js';
import {setupPhone} from '../k1monitor/static/remote-ui.js';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function dom(){
  class Element{
    constructor(){this.children=[];this.handlers={};this.attrs={};this.value='100';this.min='10';this.max='200';this.dataset={};this.style={setProperty(){}};this.classList={toggle:(name,value)=>{this.attrs[name]=value;}};}
    append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(name,value){this.attrs[name]=value;}addEventListener(name,fn){this.handlers[name]=fn;}showModal(){this.open=true;}close(){this.open=false;}
  }
  const items=new Map(),$=id=>{if(!items.has(id))items.set(id,new Element());return items.get(id);};
  globalThis.document={getElementById:$,createElement:()=>new Element()};return $;
}
function tools(){
  const $=dom();let selected={printer:{id:'one',name:'One',ip:'192.168.4.104'},connection:{sessions:1,online:true,stale:false},print:{file:'one.gcode',job_id:'job1'},temperatures:{nozzle_target:240,bed_target:80},controls:{nozzle:true,bed:true,speed:true},auto_unload:{enabled:true,temperature:240},objects:[]};
  const calls=[],ui=setupPrinterTools({getSelected:()=>selected,getPrinters:()=>[selected],api:async(path,method,body)=>{calls.push({path,method,body});assert.ok(!path.includes('/files/start'));return {message:'Mocked'};}});ui.update(selected);
  return {$,calls,ui,get item(){return selected;},set item(value){selected=value;}};
}
test('temperature edits send nothing until Apply and carry the printer session and job',async()=>{
  const f=tools();f.$('nozzle-set').value='255';f.$('nozzle-set').handlers.input();assert.equal(f.calls.length,0);
  f.$('nozzle-apply').handlers.click();await flush();assert.equal(f.calls.length,1);
  const call=f.calls[0];assert.equal(call.path,'/printers/one/settings-command');assert.equal(call.body.action,'nozzle');assert.equal(call.body.value,255);assert.equal(call.body.expected_ip,'192.168.4.104');assert.equal(call.body.expected_session,1);assert.equal(call.body.expected_job,'job1');
});
test('changed session or job rejects a pending temperature edit before sending',async()=>{
  for(const field of ['session','job']){
    const f=tools();f.$('bed-set').value='90';f.$('bed-set').handlers.input();
    if(field==='session')f.item.connection.sessions=2;else f.item.print.job_id='other';
    f.$('bed-apply').handlers.click();await flush();assert.equal(f.calls.length,0);
  }
});
test('invalid temperature or disconnected printer sends no request',async()=>{
  for(const value of ['','301','-1','240.5']){const f=tools();f.$('nozzle-set').value=value;f.$('nozzle-set').handlers.input();f.$('nozzle-apply').handlers.click();await flush();assert.equal(f.calls.length,0);}
  const f=tools();f.item.connection.stale=true;f.item.controls.nozzle=false;f.ui.update(f.item);assert.equal(f.$('nozzle-apply').disabled,true);f.$('nozzle-apply').handlers.click();assert.equal(f.calls.length,0);
});
test('unload temperature saves its value without changing the enabled flag or issuing a heater command',async()=>{
  const f=tools();f.$('unload-temperature').value='250';f.$('unload-temperature').handlers.input();f.$('unload-temperature-apply').handlers.click();await flush();
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/printers/one/auto-unload');assert.deepEqual(f.calls[0].body,{temperature:250});assert.equal(f.item.auto_unload.enabled,true);
});
test('camera progress follows its printer, marks stale values and hides absent or finished jobs',()=>{
  dom();const progress=createWallProgress(),item={printer:{name:'Second'},status:'printing',print:{file:'test.gcode',progress_percent:42},connection:{online:true,stale:false}};
  progress.update(item);assert.equal(progress.element.value,42);assert.equal(progress.element.hidden,false);assert.match(progress.element.attrs['aria-label'],/Second.*42/);
  item.connection.stale=true;progress.update(item);assert.equal(progress.element.attrs.stale,true);
  item.status='completed';progress.update(item);assert.equal(progress.element.hidden,true);
  item.status='printing';item.print.progress_percent=null;progress.update(item);assert.equal(progress.element.hidden,true);
});
test('device rename submits only the new name and retained device id',async()=>{
  const $=dom(),calls=[];const savedInterval=globalThis.setInterval;globalThis.setInterval=()=>0;
  try{setupPhone({remote:false,api:async(path,method='GET',body)=>{calls.push({path,method,body});return {url:'https://pc.tail12345.ts.net/',devices:[{id:'device1',name:body?.name??'Old'}]};}});}finally{globalThis.setInterval=savedInterval;}
  $('phone-open').handlers.click();await flush();$('phone-devices').children[0].children[1].handlers.click();assert.equal($('device-rename-dialog').open,true);
  $('device-rename-name').value='New';await $('device-rename-form').handlers.submit({preventDefault(){}});
  assert.equal(calls.at(-1).path,'/remote/devices/device1');assert.equal(calls.at(-1).method,'PATCH');assert.deepEqual(calls.at(-1).body,{name:'New'});assert.equal($('device-rename-dialog').open,false);
});
