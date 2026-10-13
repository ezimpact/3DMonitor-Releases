import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.101';
export const defaultAppearance={version:2,accent:'#0872e8',background:'#10161c',panel:'#1a222a',sidebar:'#141b22',shade:70,hasImage:false};
export const appearancePresets={
  forest:{...defaultAppearance,accent:'#a0efc1',background:'#111715',panel:'#1a221f',sidebar:'#141d18'},
  graphite:defaultAppearance,
  light:{...defaultAppearance,accent:'#2467c4',background:'#eef2f7',panel:'#ffffff',sidebar:'#e1e7f0'},
};
const key='k1-appearance-v1';
const color=value=>typeof value==='string'&&/^#[0-9a-f]{6}$/i.test(value);
export function normalizeAppearance(value){
  const source=value&&typeof value==='object'?value:{},result={...defaultAppearance};
  const fields=['accent','background','panel','sidebar'];
  for(const name of fields)if(color(source[name]))result[name]=source[name].toLowerCase();
  const legacy=[['#a0efc1','#111715','#1a221f','#141d18'],['#78b7ff','#11151d','#1c2431','#151b26']];
  if(source.version===1&&legacy.some(palette=>fields.every((name,index)=>typeof source[name]==='string'&&source[name].toLowerCase()===palette[index])))for(const name of fields)result[name]=defaultAppearance[name];
  if(Number.isFinite(source.shade))result.shade=Math.min(95,Math.max(0,Math.round(source.shade)));
  result.hasImage=source.hasImage===true;return result;
}
export function contrastColor(value){
  const rgb=[1,3,5].map(index=>parseInt(value.slice(index,index+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
  const luminance=rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
  return luminance>.179?'#121820':'#f4f7fb';
}
export function validateBackgroundFile(file){
  if(!file||!['image/jpeg','image/png','image/webp'].includes(file.type))throw new Error(t('Выберите изображение JPG, PNG или WebP.'));
  if(file.size>10*1024*1024)throw new Error(t('Изображение должно быть не больше 10 МБ.'));
}
export function applyAppearance(settings,root=document.documentElement){
  const value=normalizeAppearance(settings),style=root.style;
  for(const [variable,name] of Object.entries({'--green':'accent','--bg':'background','--panel':'panel','--sidebar':'sidebar'}))style.setProperty(variable,value[name]);
  const foreground=contrastColor(value.background);
  style.setProperty('--text',foreground);style.setProperty('--on-accent',contrastColor(value.accent));
  style.setProperty('--muted',foreground==='#121820'?'#506174':'#a6b4c3');
  for(const surface of ['panel','sidebar']){const text=contrastColor(value[surface]);style.setProperty('--'+surface+'-text',text);style.setProperty('--'+surface+'-muted',text==='#121820'?'#506174':'#a6b4c3');}
  style.setProperty('--line',`color-mix(in srgb, ${value.panel} 72%, ${contrastColor(value.panel)})`);
  style.setProperty('--background-shade',value.shade/100);style.setProperty('--background-tint',value.background);
  root.style.colorScheme=contrastColor(value.background)==='#121820'?'light':'dark';
}

// Images stay in this browser's IndexedDB. No upload/API request is made.
export function imageStorage(environment=globalThis){
  return async(action,blob)=>{
    if(!environment.indexedDB)throw new Error(t('Этот браузер не поддерживает сохранение фона.'));
    const database=await new Promise((resolve,reject)=>{
      const request=environment.indexedDB.open('k1monitor-appearance',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('images');
      request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(new Error(t('Не удалось открыть хранилище фона.')));
    });
    try{return await new Promise((resolve,reject)=>{
      const transaction=database.transaction('images',action==='get'?'readonly':'readwrite'),store=transaction.objectStore('images');
      const request=action==='get'?store.get('background'):action==='put'?store.put(blob,'background'):store.delete('background');
      let result;request.onsuccess=()=>{result=request.result;};
      transaction.oncomplete=()=>resolve(result);transaction.onerror=transaction.onabort=()=>reject(new Error(t('Не удалось сохранить фон. Проверьте свободное место и разрешения браузера.')));
    });}finally{database.close();}
  };
}
async function prepareImage(file){
  validateBackgroundFile(file);
  let image;try{image=await createImageBitmap(file);}catch(_){throw new Error(t('Не удалось прочитать изображение. Выберите другой файл.'));}
  try{
    if(image.width*image.height>40000000)throw new Error(t('Изображение слишком большое: максимум 40 мегапикселей.'));
    const scale=Math.min(1,1920/image.width,1920/image.height),canvas=document.createElement('canvas');
    canvas.width=Math.max(1,Math.round(image.width*scale));canvas.height=Math.max(1,Math.round(image.height*scale));
    canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);
    return await new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error(t('Не удалось подготовить фон.'))),'image/webp',.88));
  }finally{image.close();}
}
export function setupAppearance(){
  const $=id=>document.getElementById(id),dialog=$('appearance-dialog'),store=imageStorage();
  let current=normalizeAppearance(null),draft,storedImage=null,stagedImage=null,imageChanged=false,objectUrl=null,generation=0,busy=false,imageLoading=false;
  try{current=normalizeAppearance(JSON.parse(localStorage.getItem(key)));}catch(_){}
  function showError(text=''){$('appearance-error').textContent=text;$('appearance-error').hidden=!text;}
  function wallpaper(blob){if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=blob?URL.createObjectURL(blob):null;
    document.body.style.setProperty('--wallpaper',objectUrl?`url("${objectUrl}")`:'none');document.body.classList.toggle('has-wallpaper',!!objectUrl);
  }
  function renderDraft(){applyAppearance(draft);wallpaper(draft.hasImage?(imageChanged?stagedImage:storedImage):null);
    for(const name of ['accent','background','panel','sidebar'])$('appearance-'+name).value=draft[name];
    $('appearance-shade').value=draft.shade;$('appearance-shade-value').textContent=draft.shade+'%';
    $('appearance-remove-image').disabled=!draft.hasImage;$('appearance-image-note').textContent=draft.hasImage?t('Собственный фон выбран. Нажмите «Применить», чтобы сохранить.'):t('Фон не выбран.');
  }
  applyAppearance(current);
  const ready=current.hasImage?store('get').then(blob=>{storedImage=blob??null;wallpaper(storedImage);}).catch(()=>{}):Promise.resolve();
  $('appearance-open').addEventListener('click',async()=>{await ready;draft={...current};stagedImage=null;imageChanged=false;showError();renderDraft();dialog.showModal();});
  for(const name of ['accent','background','panel','sidebar'])$('appearance-'+name).addEventListener('input',event=>{draft[name]=event.target.value;applyAppearance(draft);});
  $('appearance-shade').addEventListener('input',event=>{draft.shade=Number(event.target.value);applyAppearance(draft);$('appearance-shade-value').textContent=draft.shade+'%';});
  document.querySelectorAll('[data-theme-preset]').forEach(button=>button.addEventListener('click',()=>{
    const preset=appearancePresets[button.dataset.themePreset];draft={...preset,hasImage:draft.hasImage,shade:draft.shade};renderDraft();
  }));
  $('appearance-image').addEventListener('change',async event=>{
    const file=event.target.files?.[0],request=++generation;if(!file||busy)return;showError();imageLoading=true;$('appearance-apply').disabled=true;
    try{const blob=await prepareImage(file);if(request!==generation||!dialog.open)return;stagedImage=blob;imageChanged=true;draft.hasImage=true;renderDraft();}
    catch(error){if(request===generation)showError(t(error.message));}finally{event.target.value='';if(request===generation){imageLoading=false;$('appearance-apply').disabled=busy;}}
  });
  function cancelImageLoad(){++generation;imageLoading=false;$('appearance-apply').disabled=busy;}
  $('appearance-remove-image').addEventListener('click',()=>{cancelImageLoad();imageChanged=true;stagedImage=null;draft.hasImage=false;renderDraft();});
  $('appearance-reset').addEventListener('click',()=>{cancelImageLoad();draft={...defaultAppearance};imageChanged=true;stagedImage=null;showError();renderDraft();});
  $('appearance-apply').addEventListener('click',async()=>{
    if(busy||imageLoading)return;busy=true;dialog.classList.add('appearance-busy');$('appearance-apply').disabled=true;showError();
    try{
      const next=normalizeAppearance(draft),image=stagedImage,edited=imageChanged;
      // Check preference storage first; keep previous image if this browser rejects settings.
      localStorage.setItem(key,JSON.stringify(current));
      if(edited)await store(image?'put':'delete',image);
      try{localStorage.setItem(key,JSON.stringify(next));}
      catch(error){if(edited)await store(storedImage?'put':'delete',storedImage);throw error;}
      current=next;if(edited)storedImage=image;
      dialog.close();
    }catch(error){showError(t(error.message)||t('Не удалось сохранить оформление.'));}
    finally{busy=false;dialog.classList.remove('appearance-busy');$('appearance-apply').disabled=false;}
  });
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  dialog.addEventListener('click',event=>{if(busy){event.preventDefault();event.stopImmediatePropagation();}},true);
  dialog.addEventListener('close',()=>{++generation;imageLoading=false;$('appearance-apply').disabled=busy;applyAppearance(current);wallpaper(current.hasImage?storedImage:null);});
  window.addEventListener('pagehide',()=>{if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=null;});
  window.addEventListener('pageshow',event=>{if(event.persisted)wallpaper(current.hasImage?storedImage:null);});
}
