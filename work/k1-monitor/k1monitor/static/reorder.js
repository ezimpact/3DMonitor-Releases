// Reorder with a pointer or keyboard without removing live video elements from the DOM.
export function normalizeOrder(saved, available) {
  const valid=new Set(available), result=[];
  for(const id of [...saved,...available])if(valid.has(id)&&!result.includes(id))result.push(id);
  return result;
}

export function swapOrder(order, first, second) {
  const result=[...order], a=result.indexOf(first), b=result.indexOf(second);
  if(a>=0&&b>=0)[result[a],result[b]]=[result[b],result[a]];
  return result;
}

export function installReorder({container,itemSelector,handleSelector,idKey,getOrder,swap}) {
  const doc=container.ownerDocument;
  let drag=null;
  const itemFor=target=>target?.closest?.(itemSelector);
  const handleFor=target=>target?.closest?.(handleSelector);
  function clear() {
    if(!drag)return;
    const old=drag;drag=null;
    old.item.classList.remove('reorder-source');old.target?.classList.remove('reorder-target');
    old.ghost?.remove();
    if(old.handle.hasPointerCapture?.(old.pointerId))old.handle.releasePointerCapture(old.pointerId);
  }
  container.addEventListener('pointerdown',event=>{
    const handle=handleFor(event.target),item=itemFor(handle);
    if(!handle||!item||!container.contains(item)||event.button!==0||event.isPrimary===false)return;
    clear();event.preventDefault();handle.focus({preventScroll:true});
    drag={handle,item,id:item.dataset[idKey],pointerId:event.pointerId,x:event.clientX,y:event.clientY,active:false,target:null};
    handle.setPointerCapture?.(event.pointerId);
  });
  container.addEventListener('pointermove',event=>{
    if(!drag||drag.pointerId!==event.pointerId)return;
    if(!drag.active&&Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<6)return;
    event.preventDefault();
    if(!drag.active){
      drag.active=true;drag.item.classList.add('reorder-source');
      drag.ghost=doc.createElement('div');drag.ghost.className='reorder-ghost';drag.ghost.textContent=drag.handle.dataset.label??'';doc.body.append(drag.ghost);
    }
    drag.ghost.style.left=event.clientX+12+'px';drag.ghost.style.top=event.clientY+12+'px';
    const hit=itemFor(doc.elementFromPoint(event.clientX,event.clientY));
    const target=hit&&container.contains(hit)&&hit!==drag.item?hit:null;
    if(drag.target!==target){drag.target?.classList.remove('reorder-target');drag.target=target;target?.classList.add('reorder-target');}
  });
  container.addEventListener('pointerup',event=>{
    if(!drag||drag.pointerId!==event.pointerId)return;
    const {active,id,item,handle}=drag;
    const hit=itemFor(doc.elementFromPoint(event.clientX,event.clientY));
    const target=hit&&container.contains(hit)&&hit!==item?hit:null;
    clear();
    if(active&&target&&container.contains(target)){event.preventDefault();swap(id,target.dataset[idKey]);handle.focus({preventScroll:true});}
  });
  for(const name of ['pointercancel','lostpointercapture'])container.addEventListener(name,event=>{if(drag?.pointerId===event.pointerId)clear();});
  container.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&drag){event.preventDefault();clear();return;}
    const handle=handleFor(event.target),item=itemFor(handle);
    if(!handle||!item||!container.contains(item)||event.altKey||event.ctrlKey||event.metaKey)return;
    const order=getOrder(),index=order.indexOf(item.dataset[idKey]);
    const step={ArrowUp:-1,ArrowLeft:-1,ArrowDown:1,ArrowRight:1}[event.key];
    let destination=step===undefined?event.key==='Home'?0:event.key==='End'?order.length-1:undefined:index+step;
    if(destination===undefined)return;
    event.preventDefault();
    if(index>=0&&destination>=0&&destination<order.length&&index!==destination){swap(order[index],order[destination]);handle.focus({preventScroll:true});}
  });
  // Handles are siblings of the normal printer/camera buttons; a drag never opens a printer.
  container.addEventListener('click',event=>{if(handleFor(event.target)){event.preventDefault();event.stopPropagation();}});
  return {cancel:clear};
}
