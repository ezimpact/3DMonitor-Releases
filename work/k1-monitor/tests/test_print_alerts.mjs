import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PrintErrorTracker,errorLabel,setupPrintAlerts} from '../k1monitor/static/print-alerts.js';
const printer=(id,code=0,status='printing')=>({printer:{id,name:id},connection:{online:true,stale:false},state_code:status==='printing'?1:0,status,print:{file:'part.gcode'},error:{active:code!==0,code,key:String(code),message:''}});
test('errors on unselected printers emit once, rearm after clearing, and notice changed codes',()=>{
  const tracker=new PrintErrorTracker();tracker.update([printer('one'),printer('two')]);
  assert.equal(tracker.update([printer('one'),printer('two',202)])[0].printer.id,'two');
  assert.equal(tracker.update([printer('one'),printer('two',202)]).length,0);
  assert.equal(tracker.update([printer('one'),printer('two',203)]).length,1);
  tracker.update([printer('two')]);assert.equal(tracker.update([printer('two',203)]).length,1);
});
test('an error that changes printing into an error state is still a print alarm',()=>{
  const tracker=new PrintErrorTracker();tracker.update([printer('one')]);
  assert.equal(tracker.update([printer('one',2839,'error')]).length,1);
  assert.equal(tracker.update([printer('one',2839,'error')]).length,0);
});
test('an existing idle error becomes a print alarm when a job begins',()=>{
  const tracker=new PrintErrorTracker();assert.equal(tracker.update([printer('one',202,'idle')]).length,0);
  assert.equal(tracker.update([printer('one',202)]).length,1);assert.equal(tracker.update([printer('one',202)]).length,0);
});
test('offline, stale, idle errors and frozen server data never cause print alarms',()=>{
  const tracker=new PrintErrorTracker();tracker.update([printer('one')]);
  const error=printer('one',202);
  assert.equal(tracker.update([{...error,connection:{online:false,stale:true}}]).length,0);
  assert.equal(tracker.update([{...error,connection:{online:true,stale:true}}]).length,0);
  assert.equal(tracker.update([error],false).length,0);
  assert.equal(tracker.update([printer('idle',202,'idle')]).length,0);
  assert.equal(tracker.update([error]).length,1);
});
test('codes remain readable without duplicating the numeric key and deleted printers are forgotten',()=>{
  assert.equal(errorLabel(printer('one',202)),'202');
  assert.equal(errorLabel({...printer('one',202),error:{code:202,key:'key202'}}),'202 / key202');
  const tracker=new PrintErrorTracker();tracker.update([printer('one',202)]);tracker.update([]);assert.equal(tracker.printers.size,0);
});
test('sound defaults on, survives autoplay blocking, unlocks on a gesture and can be muted',async()=>{
  const elements=new Map(),handlers={},started=[],storage=new Map();let gestureAllowed=false;
  const get=id=>{if(!elements.has(id))elements.set(id,{handlers:{},textContent:'',setAttribute(){},addEventListener(event,fn){this.handlers[event]=fn;},replaceChildren(){},append(){}});return elements.get(id);};
  class Audio {constructor(){this.state='suspended';this.currentTime=0;}async resume(){if(gestureAllowed)this.state='running';}createOscillator(){return {frequency:{},connect(){},start:()=>started.push(1),stop(){}};}createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){}},connect(){}};}}
  const alerts=setupPrintAlerts({environment:{document:{getElementById:get,createElement:()=>({}),addEventListener:(event,fn)=>handlers[event]=fn},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},AudioContext:Audio}});
  alerts.update([printer('one',202)],true);assert.equal(started.length,0);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(started.length,0);assert.equal(storage.get('k1-error-sound'),undefined);
  gestureAllowed=true;handlers.pointerdown({target:{closest:()=>null}});await new Promise(resolve=>setImmediate(resolve));assert.equal(started.length,3);
  alerts.update([printer('one',202)],true);assert.equal(started.length,3);
  get('sound-toggle').handlers.click();alerts.update([printer('one',203)],true);assert.equal(started.length,3);assert.equal(storage.get('k1-error-sound'),'false');
});
