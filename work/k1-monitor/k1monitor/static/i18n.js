import translations from './locales/en.js?v=0.103.3';
const supported=['ru','en'];
export const language=globalThis.document?.querySelector('meta[name="3dm-language"]')?.content==='en'?'en':'ru';
export const locale=language==='en'?'en-US':'ru-RU';
const escape=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const patterns=Object.entries(translations).filter(([key])=>/\{\d+\}/.test(key)).sort((a,b)=>b[0].length-a[0].length).map(([key,target])=>{
 const slots=[];const parts=key.split(/(\{\d+\})/g).map(part=>{if(/^\{\d+\}$/.test(part)){slots.push(Number(part.slice(1,-1)));return '([\\s\\S]*?)';}return escape(part);});
 return {pattern:new RegExp('^'+parts.join('')+'$'),slots,target};
});
export function t(value,...values){
 if(typeof value!=='string')return value;
 let result=language==='en'?(translations[value]??value):value;
 if(values.length)return result.replace(/\{(\d+)\}/g,(token,index)=>values[index]??token);
 if(language==='en'&&!Object.hasOwn(translations,value))for(const {pattern,slots,target} of patterns){const match=value.match(pattern);if(match){const captured={};slots.forEach((slot,index)=>captured[slot]=match[index+1]);return target.replace(/\{(\d+)\}/g,(_,index)=>captured[index]);}}
 return result;
}
export function tm(strings,...values){return t(strings.reduce((key,part,index)=>key+part+(index<values.length?'{'+index+'}':''),''),...values);}
const markupKeys=Object.keys(translations).filter(key=>/[А-Яа-яЁё]/.test(key)&&!/\{\d+\}/.test(key)).sort((a,b)=>b.length-a.length);
const markupPattern=new RegExp(markupKeys.map(escape).join('|'),'g');
export function trMarkup(source){return language==='en'?source.replace(markupPattern,match=>translations[match]):source;}
// Translate literal markup segments only; interpolated names/files stay intact.
export function m(strings,...values){return strings.reduce((result,part,index)=>result+trMarkup(part)+(index<values.length?values[index]:''),'');}

export function setupLanguage({api,remote=false}){
 const button=document.getElementById('language-open'),dialog=document.getElementById('language-dialog');
 button.addEventListener('click',()=>{document.getElementById('language-choice').value=language;document.getElementById('language-error').hidden=true;dialog.showModal();});
 document.getElementById('language-form').addEventListener('submit',async event=>{
  event.preventDefault();const choice=document.getElementById('language-choice').value;if(!supported.includes(choice))return;
  const save=document.getElementById('language-save'),error=document.getElementById('language-error');save.disabled=true;error.hidden=true;
  try{
   if(!remote)await api('/language','PUT',{language:choice});
   document.cookie='3dm-language='+choice+'; Path=/; Max-Age=31536000; SameSite=Strict'+(location.protocol==='https:'?'; Secure':'');
   location.reload();
  }catch(e){error.textContent=t(e.message);error.hidden=false;save.disabled=false;}
 });
}
