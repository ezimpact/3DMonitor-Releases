import {t,tm} from './i18n.js?v=0.101';
export function droppedFileKind(file){
 if(/\.(stl|step|stp)$/i.test(file.name))return 'model';
 if(/\.(gcode|gco)$/i.test(file.name))return 'gcode';
 return null;
}
export function setupFileDrop({openModel,uploadGcode,getSelected,onError,document:doc=document}){
 const overlay=doc.createElement('div');overlay.id='file-drop-overlay';overlay.className='file-drop-overlay';overlay.hidden=true;overlay.setAttribute('role','status');doc.body.append(overlay);
 let depth=0;
 const hasFiles=event=>Array.from(event.dataTransfer?.types??[]).includes('Files');
 function reset(){depth=0;overlay.hidden=true;}
 function show(){const selected=getSelected();overlay.textContent=t('Отпустите файл: STL / STEP / STP — предпросмотр')+'\n'+(selected?tm`G-code — загрузить на ${selected.printer.name}`:t('Для загрузки G-code выберите подключённый принтер'));overlay.hidden=false;}
 doc.addEventListener('dragenter',event=>{if(!hasFiles(event))return;event.preventDefault();depth++;show();});
 doc.addEventListener('dragover',event=>{if(!hasFiles(event))return;event.preventDefault();event.dataTransfer.dropEffect='copy';show();});
 doc.addEventListener('dragleave',event=>{if(!hasFiles(event)&&!depth)return;depth=Math.max(0,depth-1);if(!depth)reset();});
 doc.addEventListener('dragend',reset);
 doc.addEventListener('drop',async event=>{
  if(!hasFiles(event))return;event.preventDefault();reset();
  try{
   const files=Array.from(event.dataTransfer.files??[]);
   if(files.length!==1)throw new Error(t('Перетащите один файл за раз'));
   const file=files[0],kind=droppedFileKind(file);
   if(!kind)throw new Error(t('Поддерживаются STL, STEP, STP и G-code'));
   if(kind==='model'){await openModel(file);return;}
   const selected=getSelected();
   if(!selected||!selected.connection.online||selected.connection.stale)throw new Error(t('Для загрузки G-code выберите подключённый принтер'));
   await uploadGcode(file,structuredClone(selected));
  }catch(error){onError(t(error.message));}
 });
}
