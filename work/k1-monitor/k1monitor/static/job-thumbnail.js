import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.102.1';
export function setupJobThumbnail({image,placeholder,apiBase='/api/v1',environment=globalThis}){
  const env=environment;let key='',controller=null,url=null,generation=0,retryAt=0,busy=false;
  function clear(){controller?.abort();controller=null;if(url)env.URL.revokeObjectURL(url);url=null;image.removeAttribute('src');image.hidden=true;placeholder.hidden=false;}
  function update(item,online){
    const file=item?.print.file;
    const next=file?JSON.stringify([item.printer.id,item.printer.ip,item.connection.sessions,file]):'';
    if(next!==key){generation++;clear();key=next;busy=false;retryAt=0;placeholder.textContent=file?t('Загрузка…'):t('Нет задания');}
    if(!file||!online||item.connection.stale||busy||url||env.performance.now()<retryAt)return;
    const gen=generation;busy=true;controller=new env.AbortController();
    const query=new URLSearchParams({file,session:item.connection.sessions});
    env.fetch(`${apiBase}/printers/${encodeURIComponent(item.printer.id)}/thumbnail?${query}`,{signal:controller.signal}).then(async reply=>{
      if(!reply.ok)throw new Error('No thumbnail');const blob=await reply.blob();
      if(gen!==generation)return;
      url=env.URL.createObjectURL(blob);image.onload=()=>{if(gen!==generation)return;image.hidden=false;placeholder.hidden=true;};
      image.onerror=()=>{if(gen!==generation)return;if(url)env.URL.revokeObjectURL(url);url=null;image.hidden=true;placeholder.hidden=false;placeholder.textContent=t('Нет миниатюры');retryAt=env.performance.now()+30000;};
      image.src=url;
    }).catch(()=>{if(gen===generation){placeholder.textContent=t('Нет миниатюры');retryAt=env.performance.now()+30000;}}).finally(()=>{if(gen===generation)busy=false;});
  }
  return {update,dispose(){generation++;clear();key='';busy=false;}};
}
