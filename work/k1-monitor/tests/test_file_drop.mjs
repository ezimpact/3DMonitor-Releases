import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setupFileDrop,droppedFileKind} from '../k1monitor/static/file-drop.js';
function fixture(selected=null){
 const handlers={},overlay={hidden:true,setAttribute(){}};
 const document={body:{append(){}},createElement:()=>overlay,addEventListener:(name,fn)=>handlers[name]=fn};
 const calls=[],errors=[];let item=selected;
 setupFileDrop({document,getSelected:()=>item,openModel:async file=>calls.push(['model',file]),uploadGcode:async(file,snapshot)=>calls.push(['upload',file,snapshot]),onError:message=>errors.push(message)});
 const event=(files,types=['Files'])=>({dataTransfer:{files,types},prevented:false,preventDefault(){this.prevented=true;}});
 return {handlers,overlay,calls,errors,event,set selected(value){item=value;}};
}
test('drop recognizes every preview format and case variant',()=>{
 for(const name of ['one.stl','one.STL','one.step','one.STEP','one.stp','one.STP'])assert.equal(droppedFileKind({name}),'model');
 for(const name of ['one.gcode','one.GCODE','one.gco'])assert.equal(droppedFileKind({name}),'gcode');
 assert.equal(droppedFileKind({name:'one.stl.exe'}),null);
});
test('model drop opens preview without any selected printer or upload',async()=>{
 const f=fixture(),file={name:'one.step'},event=f.event([file]);await f.handlers.drop(event);
 assert.equal(event.prevented,true);assert.deepEqual(f.calls,[['model',file]]);assert.deepEqual(f.errors,[]);assert.equal(f.overlay.hidden,true);
});
test('G-code upload pins a copy of the printer selected at drop time',async()=>{
 const first={printer:{id:'one',name:'One',ip:'192.168.254.1'},connection:{online:true,stale:false,sessions:3}};
 const f=fixture(first),file={name:'one.gcode'};await f.handlers.drop(f.event([file]));
 first.printer.id='two';first.connection.sessions=4;
 assert.equal(f.calls[0][0],'upload');assert.equal(f.calls[0][2].printer.id,'one');assert.equal(f.calls[0][2].connection.sessions,3);
});
test('multiple files, unsupported files and an unavailable printer never upload',async()=>{
 for(const selected of [null,{connection:{online:false,stale:false}},{connection:{online:true,stale:true}}]){
  const f=fixture(selected);await f.handlers.drop(f.event([{name:'one.gcode'}]));assert.equal(f.calls.length,0);assert.equal(f.errors.length,1);
 }
 for(const files of [[{name:'one.txt'}],[{name:'one.stl'},{name:'two.gcode'}],[]]){
  const f=fixture();await f.handlers.drop(f.event(files));assert.equal(f.calls.length,0);assert.equal(f.errors.length,1);
 }
});
test('text drags preserve normal browser behavior and nested file drags do not leave an overlay',async()=>{
 const f=fixture(),text=f.event([],['text/plain']);await f.handlers.drop(text);assert.equal(text.prevented,false);
 const event=f.event([{name:'one.stl'}]);f.handlers.dragenter(event);f.handlers.dragenter(event);f.handlers.dragleave(event);assert.equal(f.overlay.hidden,false);f.handlers.dragleave(event);assert.equal(f.overlay.hidden,true);
});
