import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setupConnectionFields,defaultPort} from '../k1monitor/static/connection-ui.js';
function fixture(){
  const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{value:'',handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}});return elements.get(id);};
  globalThis.document={getElementById:get};const $=id=>get('test-'+id);$('protocol').value='creality';
  return {$,ui:setupConnectionFields('test')};
}
test('protocol selection exposes beta authorization and correct port',()=>{
  const {$,ui}=fixture();ui.fields(true);assert.equal($('port').value,9999);assert.equal($('auth-options').hidden,true);
  $('protocol').value='octoprint';$('protocol').handlers.change();assert.equal($('port').value,80);assert.equal($('beta-note').hidden,false);assert.equal($('unload-fields').hidden,true);assert.equal($('digest-option').disabled,true);
  $('protocol').value='prusalink';$('protocol').handlers.change();assert.equal($('digest-option').disabled,false);
  $('protocol').value='moonraker';$('protocol').handlers.change();assert.equal($('port').value,7125);assert.equal($('unload-fields').hidden,false);assert.equal($('auth-options').hidden,true);
  assert.equal(defaultPort('octoprint'),80);
});
test('blank key retains existing secret; new login requires a key',()=>{
  const {$,ui}=fixture();$('protocol').value='octoprint';ui.fields(true);$('auth-mode').value='apikey';assert.throws(()=>ui.options(),/API/);
  ui.load({configured:true,mode:'apikey'});assert.equal(ui.options().credentials,undefined);assert.equal($('api-key').value,'');
  $('api-key').value='new-secret';assert.deepEqual(ui.options().credentials,{mode:'apikey',api_key:'new-secret'});
  $('auth-mode').value='none';assert.deepEqual(ui.options().credentials,{mode:'none'});
});
test('Digest preserves unchanged login, requires password for renamed login',()=>{
  const {$,ui}=fixture();$('protocol').value='prusalink';ui.load({configured:true,mode:'digest',username:'maker'});assert.equal(ui.options().credentials,undefined);
  $('username').value='different';assert.throws(()=>ui.options(),/пароль/);$('password').value='new-password';assert.deepEqual(ui.options().credentials,{mode:'digest',username:'different',password:'new-password'});
});
test('switching protocol discards secret fields and existing authorization context',()=>{
  const {$,ui}=fixture();ui.load({configured:true,mode:'apikey'});$('api-key').value='old-key';$('password').value='old-password';$('protocol').value='prusalink';$('protocol').handlers.change();
  assert.equal($('api-key').value,'');assert.equal($('password').value,'');$('auth-mode').value='apikey';assert.throws(()=>ui.options(),/API/);
});
