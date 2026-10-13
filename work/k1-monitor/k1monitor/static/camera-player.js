import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.102.1';
// Each view owns its camera connections. Disposal cancels both signaling and media.
export class CameraPlayer {
  constructor({video, image, placeholder, message, onState, negotiate, relay=false, apiBase='/api/v1', autoRetry=true, environment=globalThis}) {
    Object.assign(this, {video, image, placeholder, message, onState, negotiate});
    this.env=environment;
    this.relay=relay;this.autoRetry=autoRetry;
    this.apiBase=apiBase;
    this.relaySocket=null;this.frameUrl=null;
    this.generation=0;
    this.phase='off';
    this.desired=false;
    this.printer=null;
    this.online=false;
    this.peer=null;
    this.abort=null;
    this.frameCallback=null;
    this.timers=new Set();
    this.lastFrame=0;
    this.disposed=false;
    this.retryAttempts=0;
    this.retryTimer=null;
    this.retryExhausted=false;
  }

  emit(phase, message='') {
    this.phase=phase;
    this.message.textContent=t(message);
    if(this.message.dataset)this.message.dataset.transport=this.relay?'relay':'direct';
    this.placeholder.hidden=phase==='live';
    this.onState?.(phase, this);
  }

  timer(callback, milliseconds) {
    const id=this.env.setTimeout(()=>{this.timers.delete(id);callback();}, milliseconds);
    this.timers.add(id);
    return id;
  }

  release() {
    this.generation++;
    this.abort?.abort(); this.abort=null;
    for(const timer of this.timers)this.env.clearTimeout(timer);
    this.timers.clear();
    this.retryTimer=null;
    if(this.frameCallback!==null)this.video.cancelVideoFrameCallback?.(this.frameCallback);
    this.frameCallback=null;
    this.video.onloadeddata=null;
    this.image.onload=null;this.image.onerror=null;
    if(this.relaySocket){this.relaySocket.onmessage=null;this.relaySocket.onclose=null;this.relaySocket.onerror=null;this.relaySocket.close();}
    this.relaySocket=null;
    if(this.frameUrl)this.env.URL.revokeObjectURL(this.frameUrl);
    this.frameUrl=null;
    if(this.peer){this.peer.ontrack=null;this.peer.onconnectionstatechange=null;this.peer.onicegatheringstatechange=null;this.peer.close();}
    this.peer=null;
    for(const track of this.video.srcObject?.getTracks()??[])track.stop();
    this.video.srcObject=null;this.video.hidden=true;
    this.image.removeAttribute('src');this.image.hidden=true;
    this.lastFrame=0;
  }

  stop() {this.desired=false;this.retryAttempts=0;this.retryExhausted=false;this.release();this.emit('off',t('Камера выключена'));}
  dispose() {this.stop();this.disposed=true;}

  update(printer, online) {
    if(this.disposed)return;
    const changed=this.printer?.printer.id!==printer.printer.id||this.printer?.printer.ip!==printer.printer.ip||this.printer?.printer.protocol!==printer.printer.protocol||['present','webrtc','path'].some(key=>this.printer?.camera?.[key]!==printer.camera?.[key]);
    this.printer=printer;
    this.online=online;
    if(changed){this.release();this.retryAttempts=0;this.retryExhausted=false;}
    if(!this.desired)return;
    if(changed||this.phase==='off'){this.start();return;}
    if(!online&&!['retrying','error'].includes(this.phase))this.fail(t('Нет связи с принтером'));
  }

  async start({automatic=false}={}) {
    if(this.printer?.camera?.present===false){this.release();this.emit('off',t('Камера отсутствует или не настроена'));return;}
    if(this.disposed)return;
    if(!automatic){this.retryAttempts=0;this.retryExhausted=false;}
    this.desired=true;
    this.release();
    if(!this.online){this.fail(t('Нет связи с принтером'));return;}
    const gen=this.generation;
    this.abort=new this.env.AbortController();
    const signal=this.abort.signal;
    this.emit('connecting',automatic?tm`Переподключаем камеру… Попытка ${this.retryAttempts}/3`:t('Подключаем камеру…'));
    if(this.relay){this.startRelay(gen);return;}
    if(!this.printer.camera.webrtc){this.startMjpeg(gen);return;}
    try {
      const pc=new this.env.RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
      this.peer=pc;
      pc.addTransceiver('video',{direction:'sendrecv'});
      pc.ontrack=event=>{
        if(gen!==this.generation)return;
        this.video.srcObject=event.streams[0]??new this.env.MediaStream([event.track]);
        this.video.hidden=false;
        this.video.onloadeddata=()=>this.ready(gen,'webrtc');
        this.video.play().catch(()=>{});
        if(this.video.requestVideoFrameCallback){
          const frame=()=>{if(gen!==this.generation)return;this.ready(gen,'webrtc');this.frameCallback=this.video.requestVideoFrameCallback(frame);};
          this.frameCallback=this.video.requestVideoFrameCallback(frame);
        }
      };
      pc.onconnectionstatechange=()=>{
        if(gen!==this.generation)return;
        if(['failed','disconnected'].includes(pc.connectionState))this.fail(t('Видеосоединение прервано'));
      };
      await pc.setLocalDescription(await pc.createOffer());
      await this.gatherIce(pc,signal);
      if(gen!==this.generation)return;
      const answer=await this.negotiate(this.printer.printer.id,pc.localDescription.sdp,signal);
      if(gen!==this.generation)return;
      await pc.setRemoteDescription(answer);
      if(gen===this.generation)this.watchFrames(gen);
    } catch(error) {
      if(gen!==this.generation||signal.aborted)return;
      this.release();
      this.abort=new this.env.AbortController();
      this.startMjpeg(this.generation);
    }
  }

  startRelay(gen) {
    this.mode='relay';
    try{
      const url=(this.env.location.protocol==='https:'?'wss://':'ws://')+this.env.location.host+this.apiBase+'/printers/'+encodeURIComponent(this.printer.printer.id)+'/camera/relay';
      const socket=new this.env.WebSocket(url);
      this.relaySocket=socket;socket.binaryType='blob';
      socket.onmessage=event=>{
        if(gen!==this.generation)return;
        if(typeof event.data==='string'){
          try{const data=JSON.parse(event.data);if(data.type==='error')this.fail(data.message||t('Камера недоступна'));}catch(_){}
          return;
        }
        if(!event.data.size||event.data.size>524288){this.fail(t('Некорректный кадр камеры'));return;}
        const previous=this.frameUrl;
        this.frameUrl=this.env.URL.createObjectURL(event.data);
        this.image.onload=()=>this.ready(gen,'relay');
        this.image.onerror=()=>{if(gen===this.generation)this.fail(t('Не удалось показать кадр камеры'));};
        this.image.hidden=false;this.image.src=this.frameUrl;
        if(previous)this.env.URL.revokeObjectURL(previous);
      };
      socket.onclose=()=>{if(gen===this.generation)this.fail(t('Передача камеры через ПК прервалась'));};
      socket.onerror=()=>{if(gen===this.generation)this.fail(t('Не удалось подключиться к камере через ПК'));};
      this.watchFrames(gen);
    }catch(error){if(gen===this.generation)this.fail(t('Передача камеры через ПК недоступна'));}
  }

  gatherIce(pc,signal) {
    if(pc.iceGatheringState==='complete')return Promise.resolve();
    return new Promise((resolve,reject)=>{
      let timer;
      const finish=error=>{
        this.env.clearTimeout(timer);this.timers.delete(timer);
        signal.removeEventListener('abort',cancel);
        pc.onicegatheringstatechange=null;
        error?reject(error):resolve();
      };
      const cancel=()=>finish(new Error('Camera canceled'));
      pc.onicegatheringstatechange=()=>{if(pc.iceGatheringState==='complete')finish();};
      timer=this.timer(()=>finish(new Error('ICE timeout')),7000);
      signal.addEventListener('abort',cancel,{once:true});
      if(signal.aborted)cancel();
    });
  }

  startMjpeg(gen) {
    if(gen!==this.generation)return;
    this.mode='mjpeg';
    this.emit('connecting',t('Подключаем камеру MJPEG…'));
    this.image.hidden=false;
    this.image.onload=()=>this.ready(gen,'mjpeg');
    this.image.onerror=()=>{if(gen===this.generation)this.fail(t('Камера недоступна'));};
    this.image.src='/api/v1/printers/'+encodeURIComponent(this.printer.printer.id)+'/camera/mjpeg?v='+gen;
    this.watchFrames(gen);
  }

  ready(gen,mode) {
    if(gen!==this.generation||mode==='webrtc'&&this.video.videoWidth<16)return;
    this.mode=mode;
    this.lastFrame=this.env.performance.now();
    this.retryAttempts=0;this.retryExhausted=false;
    if(this.phase!=='live')this.emit('live');
  }

  watchFrames(gen) {
    const started=this.env.performance.now();
    const check=()=>{
      if(gen!==this.generation)return;
      const now=this.env.performance.now();
      if(!this.env.document.hidden&&now-started>22000&&(!this.lastFrame||['webrtc','relay'].includes(this.mode)&&now-this.lastFrame>10000)){
        this.fail(t('Нет свежего видео'));return;
      }
      this.timer(check,2000);
    };
    this.timer(check,2000);
  }

  fail(message) {
    if(this.disposed||!this.desired||this.retryTimer!==null)return;
    this.release();
    if(!this.autoRetry){this.emit('error',message);return;}
    if(this.retryAttempts>=3){
      this.retryExhausted=true;
      this.emit('error',message+t('. Три попытки не помогли. Нажмите «Повторить».'));
      return;
    }
    const attempt=this.retryAttempts+1,gen=this.generation;
    this.emit('retrying',tm`${message}. Повтор ${attempt}/3 через 5 с.`);
    this.retryTimer=this.timer(()=>{
      this.retryTimer=null;
      if(gen!==this.generation||!this.desired||this.disposed)return;
      this.retryAttempts=attempt;
      this.start({automatic:true});
    },5000);
  }
}
