import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';
import {t,tm,m} from '../k1monitor/static/i18n.js';

const script=readFileSync(new URL('../k1monitor/static/pair.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/,'');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function page({path='/demo',response={ok:true,json:async()=>({paired:true})},error=null}={}){
  const elements=new Map(),calls=[],redirects=[];
  const element=id=>{
    if(!elements.has(id))elements.set(id,{hidden:true,textContent:'',value:'',events:{},addEventListener(type,callback){this.events[type]=callback;}});
    return elements.get(id);
  };
  runInNewContext(script,{
    t,tm,m,
    document:{getElementById:element,querySelector:()=>({content:'csrf'})},
    location:{pathname:path,replace:value=>redirects.push(value)},AbortSignal,
    fetch:async(url,options)=>{calls.push({url,options});if(error)throw error;return response;}
  });
  return {element,calls,redirects};
}

test('saved pairing restores root or demo without showing a code form or creating a device',async()=>{
  for(const path of ['/','/demo']){
    const view=page({path});await flush();
    assert.deepEqual(view.redirects,['/']);
    assert.equal(view.element('pair-form').hidden,true);
    assert.equal(view.calls.length,1);
    assert.equal(view.calls[0].url,'/api/v1/auth/session');
    assert.equal(view.calls[0].options.credentials,'same-origin');
    assert.equal(view.calls[0].options.cache,'no-store');
    assert.equal(view.calls[0].options.method,undefined);
  }
});

test('a new or revoked browser sees the code form only after a successful session check',async()=>{
  const view=page({response:{ok:true,json:async()=>({paired:false})}});await flush();
  assert.equal(view.element('pair-form').hidden,false);
  assert.equal(view.element('pair-check-retry').hidden,true);
  assert.deepEqual(view.redirects,[]);
  assert.match(view.element('pair-hint').textContent,/создайте код/);
});

test('a connection failure offers retry without asking to create another pairing',async()=>{
  const view=page({error:new Error('offline')});await flush();
  assert.equal(view.element('pair-form').hidden,true);
  assert.equal(view.element('pair-check-retry').hidden,false);
  assert.match(view.element('pair-hint').textContent,/связь с ПК/);
  assert.deepEqual(view.redirects,[]);
  await view.element('pair-check-retry').events.click();
  assert.equal(view.calls.length,2);
  assert.equal(view.element('pair-form').hidden,true);
});
