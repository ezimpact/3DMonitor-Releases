import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.102.1';
import {CameraPlayer} from './camera-player.js?v=0.102.1';
const key=location.hash.slice(1),players=new Map();
let socket;
function dispose(entry){entry.player.dispose();clearInterval(entry.timer);entry.container.remove();}
function sync(sources){
  const available=new Map(sources.map(source=>[source.id,source]));
  for(const [id,entry] of players){if(!available.has(id)||available.get(id).generation!==entry.source.generation){dispose(entry);players.delete(id);}}
  for(const source of sources){
    if(players.has(source.id))continue;
    const container=document.createElement('div'),video=document.createElement('video'),image=document.createElement('img'),canvas=document.createElement('canvas'),placeholder=document.createElement('div'),message=document.createElement('p');
    video.autoplay=true;video.muted=true;video.playsInline=true;video.hidden=true;image.hidden=true;
    container.append(video,image,canvas,placeholder,message);document.getElementById('sources').append(container);
    const headers={'Content-Type':'application/json','X-K1-Worker':key,'X-K1-Generation':source.generation};
    const player=new CameraPlayer({video,image,placeholder,message,autoRetry:false,
      negotiate:async(id,sdp,signal)=>{
        const response=await fetch('/internal/camera-relay/'+encodeURIComponent(id)+'/offer',{method:'POST',headers,body:JSON.stringify({sdp}),signal});
        if(!response.ok)throw new Error('Camera signaling failed');
        return response.json();
      },
      onState:(phase,instance)=>{
        if(phase==='error'&&socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'error',id:source.id,generation:source.generation,message:instance.message.textContent}));
      }});
    const entry={source,container,player,timer:null,busy:false};
    players.set(source.id,entry);
    entry.timer=setInterval(async()=>{
      if(entry.busy||player.phase!=='live')return;
      const input=player.mode==='webrtc'?video:image;
      const width=input.videoWidth||input.naturalWidth,height=input.videoHeight||input.naturalHeight;
      if(!width||!height)return;
      entry.busy=true;
      try{
        const scale=Math.min(1,640/width,640/height);
        canvas.width=Math.round(width*scale);canvas.height=Math.round(height*scale);
        canvas.getContext('2d').drawImage(input,0,0,canvas.width,canvas.height);
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.72));
        if(!blob||players.get(source.id)!==entry)return;
        const response=await fetch('/internal/camera-relay/'+encodeURIComponent(source.id)+'/frame',{method:'POST',headers:{'Content-Type':'image/jpeg','X-K1-Worker':key,'X-K1-Generation':source.generation},body:blob,signal:AbortSignal.timeout(5000)});
        if(!response.ok&&response.status!==404)throw new Error('Frame upload failed');
      }catch(error){
        if(players.get(source.id)===entry&&socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'error',id:source.id,generation:source.generation,message:t('Не удалось передать кадр на ПК')}));
      }finally{entry.busy=false;}
    },200);
    player.desired=true;player.update({printer:{id:source.id},camera:source.camera},true);
  }
}
function connect(){
  if(!key)return;
  socket=new WebSocket('ws://'+location.host+'/internal/camera-relay/worker',['k1-worker.'+key]);
  socket.onmessage=event=>{const message=JSON.parse(event.data);if(message.type==='sources')sync(message.sources);};
  socket.onclose=()=>{for(const entry of players.values())dispose(entry);players.clear();setTimeout(connect,2000);};
}
setInterval(()=>{if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'heartbeat'}));},2000);
connect();
