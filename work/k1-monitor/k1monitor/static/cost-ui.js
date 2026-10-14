import {t,tm,locale,language} from './i18n.js?v=0.103.3';

export function formatMoney(value,currency){
 if(typeof value!=='number'||!Number.isFinite(value))return '—';
 return new Intl.NumberFormat(locale,{style:'currency',currency,minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
}

export function setupCosts({api,remote,getSelected,getPrinters}){
 const $=id=>document.getElementById(id);let edited=null,saving=false,generation=0,pending=null;
 const fields={price_per_kg:'cost-price',mean_power_w:'cost-power',electricity_rate:'cost-electricity',machine_hour_rate:'cost-hour-rate',labor_hour_rate:'cost-labor-rate',manual_work_cost:'cost-manual-work',extra_material_cost:'cost-materials',failure_percent:'cost-failure',extra_cost:'cost-extra',markup_percent:'cost-markup'};
 const emptyPricing=()=>({currency:language==='ru'?'RUB':'USD',price_per_kg:null,mean_power_w:0,electricity_rate:0,machine_hour_rate:0,labor_hour_rate:0,manual_work_cost:0,extra_material_cost:0,failure_percent:0,extra_cost:0,markup_percent:0});
 function error(message=''){$('cost-error').textContent=t(message);$('cost-error').hidden=!message;}
 function pricing(){const value={currency:$('cost-currency').value};for(const [key,id] of Object.entries(fields))value[key]=$(id).value.trim()===''?(key==='price_per_kg'?null:0):Number($(id).value);return value;}
 function update(){
  const item=getSelected(),data=item?.costing,job=data?.job;
  $('cost-panel').hidden=!data;
  const active=!!item?.filament?.job?.active;
  $('job-material-cost').textContent=active&&job?formatMoney(job.plastic_cost,job.currency):'—';
  $('job-cost-note').textContent=active&&job?tm`Оценка по G-code: ${job.filament_g.toLocaleString(locale)} г. Цена зафиксирована при расчёте: ${formatMoney(job.price_per_kg,job.currency)} / кг.`:data?.pricing.price_per_kg==null?t('Укажите цену килограмма пластика в калькуляторе и сохраните тарифы.'):active?t('Стоимость появится после анализа G-code.'):t('Стоимость пластика появится при следующей печати.');
 }
 function open(){
  const item=getSelected(),saved=item?.costing?.pricing||emptyPricing(),p=item?.printer;
  edited=p?{id:p.id,endpoint:[p.protocol||'',p.ip,p.api_port||''].join('|')}:null;
  $('cost-printer').textContent=p?tm`Тарифы принтера: ${p.name}`:t('Ручной расчёт');
  $('cost-currency').value=saved.currency;
  for(const [key,id] of Object.entries(fields))$(id).value=saved[key]??'';
  const grams=item?.filament?.job?.active?item.filament.job.total_g:null;
  $('cost-grams').value=typeof grams==='number'?Math.round(grams*100)/100:'';
  const elapsed=item?.print?.elapsed_seconds,remaining=item?.print?.remaining_seconds;
  const minutes=typeof elapsed==='number'&&typeof remaining==='number'&&grams!=null?Math.ceil((elapsed+remaining)/60):0;
  $('cost-hours').value=Math.floor(minutes/60);$('cost-minutes').value=minutes%60;
  $('cost-labor-hours').value=0;$('cost-labor-minutes').value=0;
  $('cost-save').hidden=!edited||remote;
  $('cost-save').disabled=saving;$('cost-result').hidden=true;$('cost-saved').hidden=true;error();
  $('cost-dialog').showModal();
 }
 $('cost-open').addEventListener('click',open);$('job-cost-open').addEventListener('click',open);
 $('cost-currency').addEventListener('change',()=>{
  for(const id of ['cost-price','cost-electricity','cost-hour-rate','cost-labor-rate','cost-manual-work','cost-materials','cost-extra'])$(id).value='';
  $('cost-saved').hidden=true;error('Валюта изменена. Введите цены заново: автоматического пересчёта курса нет.');
 });
 $('cost-form').addEventListener('input',()=>{generation++;$('cost-result').hidden=true;$('cost-saved').hidden=true;});
 $('cost-form').addEventListener('submit',async event=>{
  event.preventDefault();if(pending)return;error();const current=++generation,controller=new AbortController();pending=controller;
  $('cost-calculate').disabled=true;
  try{
   const values={...pricing(),filament_g:Number($('cost-grams').value),hours:Number($('cost-hours').value)+Number($('cost-minutes').value)/60,labor_hours:Number($('cost-labor-hours').value)+Number($('cost-labor-minutes').value)/60};
   if(values.price_per_kg==null)throw Error('Укажите цену килограмма пластика');
   const result=await api('/cost/calculate','POST',values,controller.signal);
   if(current!==generation||!$('cost-dialog').open)return;
   for(const [key,id] of Object.entries({plastic_cost:'cost-plastic-result',electricity_cost:'cost-electricity-result',machine_cost:'cost-machine-result',labor_cost:'cost-labor-result',manual_work_cost:'cost-manual-result',extra_material_cost:'cost-materials-result',failure_reserve:'cost-failure-result',extra_cost:'cost-extra-result',total_cost:'cost-total-result',markup_cost:'cost-markup-result',sale_price:'cost-sale-result'}))$(id).textContent=formatMoney(result[key],result.currency);
   $('cost-energy-result').textContent=result.energy_kwh.toLocaleString(locale,{maximumFractionDigits:3})+t(' кВт⋅ч');
   $('cost-result').hidden=false;
  }catch(exc){if(!controller.signal.aborted&&current===generation)error(exc.message);}
  finally{if(pending===controller){pending=null;$('cost-calculate').disabled=false;}}
 });
 $('cost-dialog').addEventListener('close',()=>{generation++;pending?.abort();});
 $('cost-save').addEventListener('click',async()=>{
  if(!edited||saving||remote)return;const target=edited;error();saving=true;$('cost-save').disabled=true;
  try{
   const saved=await api('/printers/'+encodeURIComponent(target.id)+'/pricing','PUT',{expected_endpoint:target.endpoint,...pricing()});
   const item=getPrinters().find(p=>p.printer.id===target.id);
   if(item&&[item.printer.protocol||'',item.printer.ip,item.printer.api_port||''].join('|')===target.endpoint)item.costing=saved;
   if($('cost-dialog').open&&edited===target){$('cost-saved').textContent=t('Тарифы сохранены. Уже рассчитанные задания сохраняют прежнюю цену.');$('cost-saved').hidden=false;}
   update();
  }catch(exc){if(edited===target)error(exc.message);}
  finally{saving=false;$('cost-save').disabled=false;}
 });
 return {update};
}
