import assert from 'node:assert/strict';
import {test} from 'node:test';
import {installReorder,normalizeOrder,swapOrder} from '../k1monitor/static/reorder.js';

function fixture() {
  const listeners={},ghosts=[],calls=[];
  let hit=null,order=['a','b','c'];
  const doc={body:{append:node=>ghosts.push(node)},elementFromPoint:()=>hit,createElement:()=>({style:{},remove(){ghosts.splice(ghosts.indexOf(this),1);}})};
  const container={ownerDocument:doc,contains:item=>items.includes(item),addEventListener:(name,callback)=>listeners[name]=callback};
  const items=order.map(id=>{
    const classes=new Set();
    const item={dataset:{id},classList:{add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)},closest:selector=>selector==='.item'?item:null};
    const handle={dataset:{label:id},focused:0,focus(){this.focused++;},closest:selector=>selector==='.handle'?handle:selector==='.item'?item:null};
    item.handle=handle;return item;
  });
  const reorder=installReorder({container,itemSelector:'.item',handleSelector:'.handle',idKey:'id',getOrder:()=>order,swap:(a,b)=>{calls.push([a,b]);order=swapOrder(order,a,b);}});
  const event=(name,extra={})=>listeners[name]({target:items[0].handle,pointerId:1,button:0,clientX:0,clientY:0,preventDefault(){},stopPropagation(){},...extra});
  return {items,calls,ghosts,reorder,event,hit:item=>{hit=item;},order:()=>order};
}

test('drag swaps only on a valid drop and cleans up the drag indicator',()=>{
  const view=fixture();view.event('pointerdown');view.hit(view.items[2]);view.event('pointermove',{clientY:100});
  assert.deepEqual(view.order(),['a','b','c']);assert.equal(view.ghosts.length,1);
  view.event('pointerup',{clientY:100});assert.deepEqual(view.order(),['c','b','a']);
  assert.equal(view.ghosts.length,0);assert.equal(view.items[0].classList.contains('reorder-source'),false);assert.equal(view.items[2].classList.contains('reorder-target'),false);
});

test('dropping outside the list cannot apply the last highlighted target',()=>{
  const view=fixture();view.event('pointerdown');view.hit(view.items[1]);view.event('pointermove',{clientY:50});
  view.hit(null);view.event('pointerup',{clientY:120});assert.equal(view.calls.length,0);assert.equal(view.ghosts.length,0);
});

test('Escape, pointer cancellation and explicit view cancellation leave the saved order alone',()=>{
  for(const cancel of ['escape','pointer','view']){
    const view=fixture();view.event('pointerdown');view.hit(view.items[1]);view.event('pointermove',{clientY:50});
    if(cancel==='escape')view.event('keydown',{key:'Escape'});
    if(cancel==='pointer')view.event('pointercancel');
    if(cancel==='view')view.reorder.cancel();
    view.event('pointerup',{clientY:50});assert.equal(view.calls.length,0);assert.equal(view.ghosts.length,0);
  }
});

test('keyboard swaps retain focus and do not change the separate camera or printer order',()=>{
  const printers=fixture(),cameras=fixture();printers.event('keydown',{key:'End'});
  assert.deepEqual(printers.order(),['c','b','a']);assert.deepEqual(cameras.order(),['a','b','c']);
  assert.equal(printers.items[0].handle.focused,1);
  printers.event('keydown',{key:'ArrowDown'});assert.equal(printers.calls.length,1);
  printers.event('keydown',{key:'Home'});assert.deepEqual(printers.order(),['a','b','c']);
});

test('refreshing the printer list preserves custom order, removes missing IDs and appends new printers',()=>{
  assert.deepEqual(normalizeOrder(['c','a','c','removed'],['a','b','c','new']),['c','a','b','new']);
});
