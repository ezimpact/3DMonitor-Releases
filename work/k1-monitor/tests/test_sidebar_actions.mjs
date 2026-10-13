import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {defaultPort} from '../k1monitor/static/connection-ui.js';
import {t,tm,m} from '../k1monitor/static/i18n.js';

const source=readFileSync(new URL('../k1monitor/static/ui.js',import.meta.url),'utf8');
const functions=source.slice(source.indexOf('function renderList('),source.indexOf('function render(data)'))+
  source.slice(source.indexOf('function openPrinterSettings('),source.indexOf("$('rename-open').addEventListener"));
function fixture(remote=false){
  class Element {
    constructor(){this.children=[];this.dataset={};this.style={};this.attributes={};this.handlers={};this.hidden=false;this.classList={toggle(){}};}
    append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
    get firstChild(){return this.children[0];}
    setAttribute(key,value){this.attributes[key]=value;}
    addEventListener(name,callback){this.handlers[name]=callback;}
    querySelector(){return this.children[2];}
    showModal(){this.open=true;}
    remove(){this.parent.children=this.parent.children.filter(child=>child!==this);}
  }
  const elements=new Map(),calls=[];
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const item=(id,name,ip)=>({printer:{id,name,ip},connection:{online:true},print:{file:'job.gcode',progress_percent:5},status:'printing'});
  const context=vm.createContext({t,tm,m,orderReady:false,syncPrinterOrder(){},defaultPort,connections:{rename:{load(){}}},connectionFields(){},remote,viewMode:'printer',latest:{printers:[item('one','Первый','192.168.4.104'),item('two','Второй','192.168.4.114')]},selectedId:'one',renameId:null,serverOnline:true,printerOrder:[],wallIds:new Set(['one']),labels:{printing:'Печатает'},$:get,
    node:(_tag,className,text)=>Object.assign(new Element(),{className,textContent:text}),
    normalizeOrder:(_old,ids)=>ids,savePrinterOrder(){},setText(){},errorIn(){},
    setWallCamera:(id,add)=>calls.push(['wall',id,add]),openConfirm:(type,snapshot)=>calls.push([type,structuredClone(snapshot)]),choose:id=>calls.push(['choose',id])});
  vm.runInContext(functions,context);
  const render=()=>context.renderList(context.latest.printers);
  render();return {context,get,calls,render};
}
test('normal sidebar edits and confirms deletion of the row printer even when another printer is selected',()=>{
  const f=fixture(),row=f.get('printer-list').children[1],buttons=row.children[1].children;
  assert.equal(buttons[0].textContent,'✎');assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,false);
  buttons[0].handlers.click();assert.equal(f.context.renameId,'two');assert.equal(f.get('rename-ip').value,'192.168.4.114');assert.equal(f.get('rename-dialog').open,true);
  buttons[1].handlers.click();assert.equal(f.calls[0][0],'remove');assert.equal(f.calls[0][1].printer.id,'two');assert.equal(f.context.selectedId,'one');
});
test('sidebar retains a native progress bar plus explicit percent as new telemetry arrives',()=>{
  const f=fixture(),progress=f.get('printer-list').children[0].children[0].children[3];
  assert.equal(progress.hidden,false);assert.equal(progress.children[0].value,5);assert.equal(progress.children[1].textContent,'5%');
  f.context.latest.printers[0].print.progress_percent=42;f.render();assert.equal(progress.children[0].value,42);assert.equal(progress.children[1].textContent,'42%');
  f.context.latest.printers[0].print.progress_percent=null;f.render();assert.equal(progress.children[1].textContent,'—%');
});
test('reused buttons switch between printer management and camera selection without changing camera behavior',()=>{
  const f=fixture(),row=f.get('printer-list').children[0],buttons=row.children[1].children;
  f.context.viewMode='cameras';f.render();assert.equal(buttons[0].textContent,'+');assert.equal(buttons[0].disabled,true);assert.equal(buttons[1].disabled,false);
  buttons[1].handlers.click();assert.deepEqual(f.calls,[['wall','one',false]]);
  f.context.wallIds.delete('one');f.render();assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,true);
  buttons[0].handlers.click();assert.deepEqual(f.calls[1],['wall','one',true]);
  f.context.viewMode='printer';f.render();assert.equal(buttons[0].textContent,'✎');assert.equal(buttons[1].disabled,false);
  buttons[0].handlers.click();assert.equal(f.context.renameId,'one');assert.equal(f.calls.length,2);
});
test('sidebar actions use refreshed printer data and ignore rows already removed',()=>{
  const f=fixture(),buttons=f.get('printer-list').children[1].children[1].children;
  f.context.latest.printers[1].printer={id:'two',name:'Новое имя',ip:'192.168.4.117'};f.render();
  buttons[0].handlers.click();assert.equal(f.get('rename-name').value,'Новое имя');assert.equal(f.get('rename-ip').value,'192.168.4.117');
  buttons[1].handlers.click();assert.equal(f.calls[0][1].printer.ip,'192.168.4.117');
  f.context.latest.printers.pop();buttons[1].handlers.click();assert.equal(f.calls.length,1);
});
test('phone retains camera selection while printer registry settings remain local to the PC',()=>{
  const f=fixture(true),actions=f.get('printer-list').children[0].children[1];
  assert.equal(actions.hidden,true);actions.children[0].handlers.click();actions.children[1].handlers.click();assert.equal(f.calls.length,0);
  f.context.viewMode='cameras';f.render();assert.equal(actions.hidden,false);actions.children[1].handlers.click();assert.deepEqual(f.calls,[['wall','one',false]]);
});
