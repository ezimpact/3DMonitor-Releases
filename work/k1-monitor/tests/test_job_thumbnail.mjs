import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setupJobThumbnail} from '../k1monitor/static/job-thumbnail.js';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const item=(id,file='part.gcode')=>({printer:{id,ip:'192.168.4.104'},print:{file},connection:{sessions:1,stale:false}});
function fixture(){
  const requests=[],revoked=[],pending=[];let clock=100;
  const image={hidden:true,removeAttribute(){delete this.src;}},placeholder={hidden:false};
  const environment={AbortController,performance:{now:()=>clock},URL:{createObjectURL:()=> 'blob:'+requests.length,revokeObjectURL:url=>revoked.push(url)},fetch:(url,options)=>{requests.push({url,options});return new Promise(resolve=>pending.push(resolve));}};
  const thumbnail=setupJobThumbnail({image,placeholder,environment});
  return {thumbnail,image,placeholder,requests,revoked,pending,setClock:value=>clock=value};
}
test('switching jobs cancels the old image request and ignores its late response',async()=>{
  const f=fixture();f.thumbnail.update(item('one'),true);f.thumbnail.update(item('two','новое.gcode'),true);
  assert.equal(f.requests[0].options.signal.aborted,true);
  f.pending[0]({ok:true,blob:async()=>({})});await flush();assert.equal(f.image.src,undefined);
  f.pending[1]({ok:true,blob:async()=>({})});await flush();f.image.onload();assert.equal(f.image.hidden,false);assert.equal(f.placeholder.hidden,true);
  f.thumbnail.dispose();assert.equal(f.revoked.length,1);assert.equal(f.image.src,undefined);
});
test('missing thumbnail stays a placeholder and retries only after thirty seconds',async()=>{
  const f=fixture();f.thumbnail.update(item('one'),true);f.pending[0]({ok:false});await flush();
  assert.equal(f.placeholder.textContent,'Нет миниатюры');f.thumbnail.update(item('one'),true);assert.equal(f.requests.length,1);
  f.setClock(30100);f.thumbnail.update(item('one'),true);assert.equal(f.requests.length,2);f.thumbnail.dispose();
});
test('empty, offline and stale jobs never request an image',()=>{
  const f=fixture();f.thumbnail.update(item('one',null),true);f.thumbnail.update(item('one'),false);f.thumbnail.update({...item('one'),connection:{sessions:1,stale:true}},true);assert.equal(f.requests.length,0);
});
