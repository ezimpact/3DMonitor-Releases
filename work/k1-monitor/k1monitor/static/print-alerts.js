import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.102.1';
import {toolbarIcon} from './toolbar-icons.js?v=0.102.1';
import {describePrinterError} from './printer-errors.js?v=0.102.1';
export {describePrinterError} from './printer-errors.js?v=0.102.1';
export function errorLabel(item){
  const error=item.error??{code:item.error_code,key:null,message:'',active:item.error_code!=null&&item.error_code!==0};
  const codes=[error.code,error.key].filter(value=>value!=null&&String(value)!=='0'&&String(value)!=='');
  return [...new Set(codes.map(String))].join(' / ')||t('без кода');
}
export class PrintErrorTracker {
  constructor(){this.printers=new Map();}
  update(printers,online=true){
    const events=[],ids=new Set(printers.map(item=>item.printer.id));
    for(const id of this.printers.keys())if(!ids.has(id))this.printers.delete(id);
    if(!online)return events;
    for(const item of printers){
      if(!item.connection.online||item.connection.stale)continue;
      const id=item.printer.id,previous=this.printers.get(id)??{running:false,error:'',file:null};
      const running=['printing','paused','preparing'].includes(item.status)||[1,5].includes(item.state_code);
      const active=item.error?.active??(item.error_code!=null&&item.error_code!==0);
      const key=active?errorLabel(item):'';
      if(active&&(running||previous.running)&&(previous.error!==key||previous.file!==item.print.file||running&&!previous.running))events.push(item);
      this.printers.set(id,{running:running||item.status==='error'&&previous.running,error:key,file:item.print.file});
    }
    return events;
  }
}
export function setupPrintAlerts({environment=globalThis}={}){
  const env=environment,$=id=>env.document.getElementById(id),tracker=new PrintErrorTracker();
  let context=null,enabled=false,wanted=true,pending=false,bannerKey='';
  try{wanted=env.localStorage.getItem('k1-error-sound')!=='false';}catch(_){}
  function label(){
    const button=$('sound-toggle');button.innerHTML=toolbarIcon(wanted?'sound':'muted');
    button.setAttribute('aria-pressed',String(wanted));button.setAttribute('aria-label',wanted?t('Выключить звук ошибок'):t('Включить звук ошибок'));
    button.setAttribute('data-tooltip',!wanted?t('Звук ошибок выключен. Нажмите, чтобы включить.'):enabled?t('Звук включён: сигнал при новой ошибке во время печати.'):t('Звук включён. Браузер разрешит сигнал после первого клика или нажатия клавиши.'));
    button.setAttribute('data-audio-ready',String(enabled));
  }
  function beep(){
    if(!wanted){pending=false;return;}
    if(!enabled||context?.state!=='running'){pending=true;return;}
    pending=false;const start=context.currentTime;
    for(let i=0;i<3;i++){
      const oscillator=context.createOscillator(),gain=context.createGain(),at=start+i*.3;
      oscillator.frequency.value=i===1?660:880;gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(.12,at+.02);gain.gain.linearRampToValueAtTime(0,at+.18);
      oscillator.connect(gain);gain.connect(context.destination);oscillator.start(at);oscillator.stop(at+.2);
    }
  }
  async function enable(){
    try{
      if(!wanted)return;context??=new (env.AudioContext||env.webkitAudioContext)();await context.resume();enabled=wanted&&context.state==='running';
      label();if(pending&&enabled)beep();
    }catch(_){enabled=false;label();}
  }
  $('sound-toggle').addEventListener('click',()=>{
    wanted=!wanted;enabled=wanted&&context?.state==='running';
    if(!wanted)pending=false;try{env.localStorage.setItem('k1-error-sound',String(wanted));}catch(_){}
    label();if(wanted)enable();
  });
  const gesture=event=>{if(wanted&&!enabled&&!event.target?.closest?.('#sound-toggle'))enable();};
  env.document.addEventListener('pointerdown',gesture);env.document.addEventListener('keydown',gesture);
  label();if(wanted)enable();
  return {update(printers,online){
    const events=tracker.update(printers,online);
    const errors=online?printers.filter(item=>item.connection.online&&!item.connection.stale&&(item.error?.active??(item.error_code!=null&&item.error_code!==0))):[];
    if(!errors.length)pending=false;
    const key=JSON.stringify(errors.map(item=>[item.printer.id,item.printer.name,errorLabel(item),item.error?.message]));
    if(key!==bannerKey){
      bannerKey=key;const banner=$('print-alerts');banner.replaceChildren();banner.hidden=errors.length===0;
      for(const item of errors){const row=env.document.createElement('p'),description=describePrinterError(item);row.textContent=`${item.printer.name}: ${description.summary}`;row.title=description.diagnostic;banner.append(row);}
    }
    if(events.length)beep();return events;
  }};
}
