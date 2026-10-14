import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setupToolbar} from '../k1monitor/static/toolbar.js';

function fixture(){
 class Element{
  constructor(){this.attrs={};this.style={};this.isConnected=true;this.rect={right:140,bottom:540,top:510,width:200,height:100};}
  setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k]??null;}removeAttribute(k){delete this.attrs[k];}
  getBoundingClientRect(){return this.rect;}closest(){return this;}contains(other){return other===this;}
 }
 const elements=new Map(),handlers={},timers=new Map();let serial=0;
 const doc={body:{append(el){elements.set(el.id,el);}},documentElement:{clientWidth:390,clientHeight:600},
  getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
  createElement(){return new Element();},addEventListener(type,fn){handlers[type]=fn;}};
 const environment={setTimeout(fn,delay){const id=++serial;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},addEventListener(){}};
 setupToolbar({document:doc,environment});const control=new Element();control.setAttribute('data-tooltip','Monitor description');
 return {doc,handlers,timers,control,tip:doc.getElementById('toolbar-tooltip'),show(){const entry=[...timers.values()][0];entry.fn();}};
}
test('monitoring icon uses 2000ms hover delay while existing toolbar keeps 3000ms',()=>{
 const f=fixture();f.control.setAttribute('data-tooltip-delay','2000');
 f.handlers.pointerover({target:f.control});assert.equal([...f.timers.values()][0].delay,2000);
 assert.equal(f.tip.hidden,true);f.show();assert.equal(f.tip.textContent,'Monitor description');assert.equal(f.tip.hidden,false);
 f.handlers.pointerover({target:f.doc.getElementById('about-open')});assert.equal([...f.timers.values()][0].delay,3000);
});
test('leaving before timeout cancels description and Escape removes only tooltip ARIA id',()=>{
 const f=fixture();f.control.setAttribute('aria-describedby','existing-note');f.handlers.pointerover({target:f.control});
 f.handlers.pointerout({relatedTarget:null});assert.equal(f.timers.size,0);assert.equal(f.tip.hidden,true);
 f.handlers.pointerover({target:f.control});f.show();assert.equal(f.control.getAttribute('aria-describedby'),'existing-note toolbar-tooltip');
 f.handlers.keydown({key:'Escape'});assert.equal(f.tip.hidden,true);assert.equal(f.control.getAttribute('aria-describedby'),'existing-note');
});
test('keyboard focus also shows description and bottom-edge tooltip remains inside viewport',()=>{
 const f=fixture();f.control.setAttribute('data-tooltip-delay','2000');f.handlers.focusin({target:f.control});f.show();
 assert.equal(f.tip.style.left,'8px');assert.equal(f.tip.style.top,'400px');
 f.handlers.focusout();assert.equal(f.tip.hidden,true);
});
test('invalid custom delay falls back to established toolbar delay',()=>{
 for(const delay of ['bad','-1','10001']){
  const f=fixture();f.control.setAttribute('data-tooltip-delay',delay);f.handlers.pointerover({target:f.control});
  assert.equal([...f.timers.values()][0].delay,3000);
 }
});
