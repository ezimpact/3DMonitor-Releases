import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createWallControls} from '../k1monitor/static/wall-controls.js';

class Element{
  constructor(){this.children=[];this.attrs={};this.handlers={};}
  append(item){this.children.push(item);}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(type,fn){this.handlers[type]=fn;}
}
globalThis.document={createElement:()=>new Element()};
function fixture(){
  let busy=false,item={printer:{id:'second',name:'Второй',ip:'192.168.4.114'},connection:{online:true,stale:false,sessions:3},
    print:{file:'second.gcode',job_id:'job-2'},status:'printing',light_on:false,controls:{pause:true,resume:false,stop:true}};
  const calls=[],errors=[];
  const view=createWallControls({getItem:()=>item,isBusy:()=>busy,
    onLight:s=>calls.push(['light',s]),onPause:s=>calls.push(['pause',s]),onStop:s=>calls.push(['stop-confirm',s]),onObjects:s=>calls.push(['objects',s]),onError:text=>errors.push(text)});
  const click=index=>view.element.children[index].handlers.click({stopPropagation(){}});
  return {view,calls,errors,click,get item(){return item;},set item(next){item=next;},set busy(next){busy=next;}};
}
test('all tile actions target its printer and keep a snapshot for stop confirmation',async()=>{
  const f=fixture();for(let index=0;index<4;index++)await f.click(index);
  assert.deepEqual(f.calls.map(call=>call[0]),['light','pause','stop-confirm','objects']);
  for(const [,item] of f.calls){assert.equal(item.printer.id,'second');assert.equal(item.print.job_id,'job-2');}
  f.item.printer.ip='192.168.4.117';assert.equal(f.calls[2][1].printer.ip,'192.168.4.114');
});
test('late clicks use refreshed data and never retain removed printers',async()=>{
  const f=fixture();f.item={...f.item,printer:{...f.item.printer,name:'Переименован',ip:'192.168.4.117'},light_on:true};
  f.view.update();await f.click(0);assert.equal(f.calls[0][1].printer.ip,'192.168.4.117');assert.equal(f.calls[0][1].light_on,true);
  f.item=null;await f.click(0);await f.click(1);assert.equal(f.calls.length,1);
});
test('offline stale and busy tiles cannot invoke any actions even before a redraw',async()=>{
  for(const reason of ['offline','stale','busy']){
    const f=fixture();if(reason==='offline')f.item.connection.online=false;if(reason==='stale')f.item.connection.stale=true;if(reason==='busy')f.busy=true;
    for(let index=0;index<4;index++)await f.click(index);
    assert.equal(f.calls.length,0);f.view.update();assert.ok(f.view.element.children.every(button=>button.disabled));
  }
});
test('paused printer shows resume and light state remains accessible after rename',async()=>{
  const f=fixture(),buttons=f.view.element.children;
  f.item.status='paused';f.item.controls={pause:false,resume:true,stop:true};f.item.light_on=true;f.item.printer.name='Новый';f.view.update();
  assert.match(buttons[1].attrs['aria-label'],/Продолжить печать · Новый/);assert.equal(buttons[1].disabled,false);
  assert.equal(buttons[0].attrs['aria-pressed'],'true');assert.match(buttons[0].title,/Выключить подсветку/);
  await f.click(1);assert.equal(f.calls[0][1].status,'paused');
});
test('idle printer allows only known lighting and never opens print actions',async()=>{
  const f=fixture();f.item.status='idle';f.item.print.file=null;f.item.controls={pause:false,resume:false,stop:false};f.view.update();
  const buttons=f.view.element.children;assert.equal(buttons[0].disabled,false);assert.ok(buttons.slice(1).every(button=>button.disabled));
  f.item.light_on=null;f.view.update();assert.equal(buttons[0].disabled,true);
});
test('control overlays stop click propagation instead of navigating to a printer',async()=>{
  const f=fixture();let stopped=0;await f.view.element.children[2].handlers.click({stopPropagation(){stopped++;}});
  assert.equal(stopped,1);assert.equal(f.calls[0][0],'stop-confirm');
});

test('fullscreen is available offline and during command work without invoking printer controls',async()=>{
  let expanded=false,count=0;
  const item={printer:{id:'camera',name:'Камера'},connection:{online:false,stale:true}};
  const view=createWallControls({getItem:()=>item,isBusy:()=>true,onError:assert.fail,
    onLight:assert.fail,onPause:assert.fail,onStop:assert.fail,onObjects:assert.fail,
    onFullscreen:()=>{expanded=!expanded;count++;},isFullscreen:()=>expanded});
  const button=view.element.children[4];assert.equal(button.disabled,false);
  assert.ok(view.element.children.slice(0,4).every(button=>button.disabled));
  await button.handlers.click({stopPropagation(){}});view.update();
  assert.equal(count,1);assert.equal(button.attrs['aria-pressed'],'true');
  assert.match(button.attrs['aria-label'],/Выйти из полного экрана/);
});
