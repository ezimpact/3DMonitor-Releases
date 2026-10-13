import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.document={querySelector:()=>({content:'en'})};
const {t,tm,m,trMarkup,locale,setupLanguage}=await import('../k1monitor/static/i18n.js?language-test');
test('English phrases and numeric placeholders keep raw printer and file names intact',()=>{
 assert.equal(locale,'en-US');assert.equal(t('На паузе'),'Paused');
 assert.equal(tm`Загружаем «${'Ошибка'}» на ${'Принтер'}…`,'Uploading “Ошибка” to Принтер…');
 assert.equal(t('Выгрузка через 5 с'),'Unload in 5 s');
 assert.equal(t('Ошибка прошивки XYZ'),'Ошибка прошивки XYZ');
});
test('markup translations only touch literal segments and retain escaped dynamic content',()=>{
 assert.equal(m`<strong>Название</strong><span>${'Сопло'}</span>`,'<strong>Name</strong><span>Сопло</span>');
 assert.equal(trMarkup('<button aria-label="Закрыть">Закрыть</button>'),'<button aria-label="Close">Close</button>');
});
test('generated browser catalog matches canonical JSON and retains every placeholder',()=>{
 const base=new URL('../k1monitor/static/locales/',import.meta.url),catalog=JSON.parse(readFileSync(new URL('en.json',base),'utf8'));
 const compiled=readFileSync(new URL('en.js',base),'utf8').split('export default ')[1].replace(/;\s*$/,'');
 assert.deepEqual(JSON.parse(compiled),catalog);
 for(const [key,value] of Object.entries(catalog))assert.deepEqual([...key.matchAll(/\{\d+\}/g)].map(m=>m[0]).sort(),[...value.matchAll(/\{\d+\}/g)].map(m=>m[0]).sort(),key);
});
test('remote language selection persists only browser preference without writing host settings',async()=>{
 const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{handlers:{},hidden:false,value:'',addEventListener(type,fn){this.handlers[type]=fn;},showModal(){this.open=true;}});return elements.get(id);};
 globalThis.document={getElementById:get,cookie:''};let reloaded=false;
 globalThis.location={protocol:'https:',reload(){reloaded=true;}};
 const writes=[];setupLanguage({remote:true,api:async(...args)=>writes.push(args)});
 get('language-open').handlers.click();assert.equal(get('language-choice').value,'en');
 get('language-choice').value='ru';await get('language-form').handlers.submit({preventDefault(){}});
 assert.deepEqual(writes,[]);assert.match(document.cookie,/3dm-language=ru/);assert.match(document.cookie,/Secure/);assert.equal(reloaded,true);
});
