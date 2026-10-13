import assert from 'node:assert/strict';
import {test} from 'node:test';
import {CameraPlayer} from '../k1monitor/static/camera-player.js';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const printer=id=>({printer:{id},camera:{webrtc:true}});

test('changing camera settings at the same IP releases the old stream and removing it stops retries',async()=>{
 const view=fixture();view.player.desired=true;
 const entry={printer:{id:'v400',ip:'192.168.4.102',protocol:'moonraker'},camera:{present:true,webrtc:false,path:'/first'}};
 view.player.update(entry,true);const generation=view.player.generation;
 view.player.update({...entry,camera:{...entry.camera,path:'/second'}},true);assert.ok(view.player.generation>generation);
 view.player.update({...entry,camera:{present:false,webrtc:false,path:''}},true);
 assert.equal(view.image.src,undefined);assert.equal(view.player.phase,'off');assert.equal(view.timers.size,0);view.player.dispose();
});

test('printer without a camera never opens video or relay connections or schedules retries',async()=>{
  for(const relay of [false,true]){
    const view=fixture({relay});view.player.desired=true;
    for(let i=0;i<3;i++)view.player.update({printer:{id:'v400',ip:'192.168.4.102'},camera:{present:false,webrtc:false}},true);
    await view.advance(20000);assert.equal(view.peers.length,0);assert.equal(view.sockets.length,0);assert.equal(view.timers.size,0);assert.equal(view.player.phase,'off');view.player.dispose();
  }
});

test('changing IP with the same printer id cancels the old connection and ignores its late answer',async()=>{
  const old=deferred();let calls=0;
  const view=fixture({negotiate:()=>++calls===1?old.promise:Promise.resolve({type:'answer',sdp:'new-address'})});
  view.player.desired=true;view.player.update({...printer('one'),printer:{id:'one',ip:'192.168.4.104'}},true);await flush();
  view.player.update({...printer('one'),printer:{id:'one',ip:'192.168.4.105'}},true);await flush();
  assert.equal(view.peers[0].closed,true);assert.equal(view.peers.length,2);
  old.resolve({type:'answer',sdp:'old-address'});await flush();
  assert.equal(view.peers[0].answer,undefined);assert.equal(view.peers[1].answer.sdp,'new-address');view.player.dispose();
});
function fixture({negotiate=async()=>({type:'answer',sdp:'answer'}),ice='complete',relay=false,autoRetry=true}={}) {
  const peers=[],timers=new Map(),frames=new Map(),stopped=[],sockets=[],revoked=[];
  let nextId=0,clock=100;
  class Peer {
    constructor(){this.iceGatheringState=ice;this.connectionState='new';this.closed=false;peers.push(this);}
    addTransceiver(){}
    async createOffer(){return {type:'offer',sdp:'offer'};}
    async setLocalDescription(offer){this.localDescription=offer;}
    async setRemoteDescription(answer){this.answer=answer;}
    close(){this.closed=true;}
  }
  class Socket {
    constructor(url){this.url=url;this.closed=false;sockets.push(this);}
    close(){this.closed=true;}
  }
  const video={hidden:true,srcObject:null,videoWidth:1920,play:async()=>{},requestVideoFrameCallback:callback=>{const id=++nextId;frames.set(id,callback);return id;},cancelVideoFrameCallback:id=>frames.delete(id)};
  const image={hidden:true,naturalWidth:640,naturalHeight:360,removeAttribute(name){delete this[name];}};
  const environment={RTCPeerConnection:Peer,AbortController,MediaStream:class {constructor(tracks){this.tracks=tracks;}getTracks(){return this.tracks;}},performance:{now:()=>clock},document:{hidden:false},setTimeout:(callback,delay)=>{const id=++nextId;timers.set(id,{callback,delay,at:clock+delay});return id;},clearTimeout:id=>timers.delete(id)};
  Object.assign(environment,{WebSocket:Socket,location:{protocol:'https:',host:'pc.tail12345.ts.net'},URL:{createObjectURL:()=> 'blob:'+ (++nextId),revokeObjectURL:url=>revoked.push(url)}});
  const player=new CameraPlayer({video,image,placeholder:{hidden:false},message:{textContent:''},negotiate,environment,relay,autoRetry});
  const attach=()=>{const track={stop:()=>stopped.push(track)};peers.at(-1).ontrack({track,streams:[]});video.onloadeddata();return track;};
  const advance=async milliseconds=>{
    const target=clock+milliseconds;
    for(;;){const next=[...timers].filter(([,timer])=>timer.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;timers.delete(next[0]);clock=next[1].at;next[1].callback();await flush();}
    clock=target;await flush();
  };
  const fail=()=>{peers.at(-1).connectionState='failed';peers.at(-1).onconnectionstatechange();};
  return {player,video,image,peers,timers,frames,stopped,sockets,revoked,attach,advance,fail,setClock:value=>{clock=value;}};
}

test('remote camera uses the authenticated server socket, releases image URLs and ignores late frames',async()=>{
  const view=fixture({relay:true});view.player.desired=true;view.player.update(printer('one'),true);
  assert.equal(view.peers.length,0);
  assert.equal(view.sockets[0].url,'wss://pc.tail12345.ts.net/api/v1/printers/one/camera/relay');
  const late=view.sockets[0].onmessage;
  view.sockets[0].onmessage({data:{size:2000}});view.image.onload();
  assert.equal(view.player.phase,'live');assert.equal(view.player.mode,'relay');
  view.sockets[0].onmessage({data:{size:2100}});view.image.onload();
  assert.equal(view.revoked.length,1);
  view.player.dispose();assert.equal(view.sockets[0].closed,true);assert.equal(view.revoked.length,2);
  late({data:{size:2200}});assert.equal(view.image.src,undefined);assert.equal(view.player.phase,'off');
});

test('remote camera retries at five-second intervals, exhausts three attempts and resets on a decoded frame',async()=>{
  const view=fixture({relay:true});view.player.desired=true;view.player.update(printer('one'),true);
  for(let attempt=0;attempt<4;attempt++){
    view.sockets.at(-1).onclose();
    if(attempt<3){await view.advance(4999);assert.equal(view.sockets.length,attempt+1);await view.advance(1);}
  }
  assert.equal(view.player.phase,'error');await view.advance(15000);assert.equal(view.sockets.length,4);
  view.player.start();view.sockets.at(-1).onmessage({data:{size:2000}});view.image.onload();
  assert.equal(view.player.retryAttempts,0);view.sockets.at(-1).onclose();await view.advance(5000);
  assert.equal(view.sockets.length,6);view.player.dispose();
});

test('a frozen remote image fails and a worker failure does not run its own retry cycle',async()=>{
  const view=fixture({relay:true});view.player.desired=true;view.player.update(printer('one'),true);
  view.sockets[0].onmessage({data:{size:2000}});view.image.onload();await view.advance(24000);
  assert.equal(view.player.phase,'retrying');view.player.dispose();
  const worker=fixture({autoRetry:false});worker.player.desired=true;worker.player.update(printer('one'),true);await flush();worker.fail();
  assert.equal(worker.player.phase,'error');assert.equal(worker.timers.size,0);worker.player.dispose();
});

test('removing one live camera releases its resources and leaves the other stream running',async()=>{
  const first=fixture(),second=fixture();
  for(const [view,id] of [[first,'one'],[second,'two']]){view.player.desired=true;view.player.update(printer(id),true);}
  await flush();
  first.attach();second.attach();
  const retainedPeer=second.player.peer,retainedStream=second.video.srcObject;
  first.player.dispose();
  assert.equal(first.peers[0].closed,true);
  assert.equal(first.stopped.length,1);
  assert.equal(first.video.srcObject,null);
  assert.equal(first.frames.size,0);
  assert.equal(first.timers.size,0);
  second.player.update(printer('two'),true);
  assert.equal(second.player.peer,retainedPeer);
  assert.equal(second.video.srcObject,retainedStream);
  assert.equal(second.player.phase,'live');
  assert.equal(second.stopped.length,0);
  second.player.dispose();
});

test('late signaling answers cannot revive a removed camera',async()=>{
  const answer=deferred();let signal;
  const view=fixture({negotiate:(_id,_sdp,cancel)=>{signal=cancel;return answer.promise;}});
  view.player.desired=true;view.player.update(printer('one'),true);await flush();
  view.player.dispose();
  assert.equal(signal.aborted,true);
  answer.resolve({type:'answer',sdp:'late'});await flush();
  assert.equal(view.peers[0].answer,undefined);
  assert.equal(view.player.peer,null);
  assert.equal(view.image.src,undefined);
  assert.equal(view.player.phase,'off');
});

test('canceling during ICE gathering clears the deadline and never opens the MJPEG fallback',async()=>{
  let requests=0;
  const view=fixture({ice:'gathering',negotiate:async()=>{requests++;}});
  view.player.desired=true;view.player.update(printer('one'),true);await flush();
  assert.equal(view.timers.size,1);
  view.player.stop();await flush();
  assert.equal(requests,0);
  assert.equal(view.timers.size,0);
  assert.equal(view.image.src,undefined);
  assert.equal(view.peers[0].closed,true);
});

test('an offline selected camera reconnects when the printer returns, but a removed one does not',async()=>{
  const view=fixture();view.player.desired=true;view.player.update(printer('one'),true);await flush();view.attach();
  view.player.update(printer('one'),false);
  assert.equal(view.player.phase,'retrying');assert.equal(view.stopped.length,1);
  view.player.update(printer('one'),true);await view.advance(5000);
  assert.equal(view.peers.length,2);assert.equal(view.player.phase,'connecting');
  view.player.dispose();view.player.update(printer('one'),false);view.player.update(printer('one'),true);await flush();
  assert.equal(view.peers.length,2);
});

test('switching printers cancels the old offer and only installs the new answer',async()=>{
  const old=deferred(),seen=[];
  const view=fixture({negotiate:async(id)=>{seen.push(id);return id==='one'?old.promise:{type:'answer',sdp:'new'};}});
  view.player.desired=true;view.player.update(printer('one'),true);await flush();
  view.player.update(printer('two'),true);await flush();
  old.resolve({type:'answer',sdp:'old'});await flush();
  assert.deepEqual(seen,['one','two']);assert.equal(view.peers[0].closed,true);
  assert.equal(view.peers[0].answer,undefined);assert.equal(view.peers[1].answer.sdp,'new');
  view.player.dispose();
});

test('status refreshes do not restart the five-second retry delay',async()=>{
  const view=fixture();view.player.desired=true;view.player.update(printer('one'),true);await flush();view.attach();
  view.player.peer.connectionState='failed';view.player.peer.onconnectionstatechange();
  assert.equal(view.player.phase,'retrying');assert.equal(view.stopped.length,1);
  for(let index=0;index<5;index++)view.player.update(printer('one'),true);
  assert.equal(view.peers.length,1);
  await view.advance(4999);assert.equal(view.peers.length,1);
  await view.advance(1);assert.equal(view.peers.length,2);assert.equal(view.player.phase,'connecting');
  view.player.dispose();
});

test('three failed automatic attempts stop until a manual retry, with five seconds between failures and retries',async()=>{
  const view=fixture();view.player.desired=true;view.player.update(printer('one'),true);await flush();view.attach();view.fail();
  for(let attempt=1;attempt<=3;attempt++){
    assert.equal(view.player.phase,'retrying');assert.match(view.player.message.textContent,new RegExp(`${attempt}/3`));
    await view.advance(4999);assert.equal(view.peers.length,attempt);
    await view.advance(1);assert.equal(view.peers.length,attempt+1);assert.equal(view.player.retryAttempts,attempt);
    view.fail();
  }
  assert.equal(view.player.phase,'error');assert.equal(view.player.retryExhausted,true);assert.match(view.player.message.textContent,/Повторить/);
  assert.equal(view.timers.size,0);
  for(let count=0;count<10;count++)view.player.update(printer('one'),true);
  view.player.update(printer('one'),false);view.player.update(printer('one'),true);
  await view.advance(60000);assert.equal(view.peers.length,4);
  await view.player.start();assert.equal(view.peers.length,5);assert.equal(view.player.retryAttempts,0);
  view.attach();assert.equal(view.player.phase,'live');view.player.dispose();
});

test('successful video recovery resets the budget for the next outage',async()=>{
  const view=fixture();view.player.desired=true;view.player.update(printer('one'),true);await flush();view.attach();view.fail();
  await view.advance(5000);view.fail();await view.advance(5000);
  assert.equal(view.player.retryAttempts,2);view.attach();assert.equal(view.player.retryAttempts,0);
  view.fail();assert.match(view.player.message.textContent,/1\/3/);await view.advance(5000);assert.equal(view.player.retryAttempts,1);view.player.dispose();
});

test('removing a camera cancels its pending automatic retry',async()=>{
  const view=fixture();view.player.desired=true;view.player.update(printer('one'),true);await flush();view.fail();
  assert.equal(view.timers.size,1);view.player.dispose();await view.advance(60000);
  assert.equal(view.peers.length,1);assert.equal(view.player.phase,'off');assert.equal(view.timers.size,0);
});

test('retries stop after three attempts while offline and are not replenished by telemetry polling',async()=>{
  const view=fixture();view.player.desired=true;view.player.update(printer('one'),false);
  for(let attempt=0;attempt<3;attempt++){for(let count=0;count<5;count++)view.player.update(printer('one'),false);await view.advance(5000);}
  assert.equal(view.player.phase,'error');assert.equal(view.player.retryAttempts,3);assert.equal(view.peers.length,0);
  view.player.update(printer('one'),true);await view.advance(60000);assert.equal(view.peers.length,0);
  await view.player.start();assert.equal(view.peers.length,1);view.player.dispose();
});

test('a late answer from a failed connection cannot cancel a newer retry',async()=>{
  const old=deferred();let requests=0;
  const view=fixture({negotiate:()=>++requests===1?old.promise:Promise.resolve({type:'answer',sdp:'new'})});
  view.player.desired=true;view.player.update(printer('one'),true);await flush();view.fail();await view.advance(5000);
  old.resolve({type:'answer',sdp:'old'});await flush();
  assert.equal(view.peers[0].answer,undefined);assert.equal(view.peers[1].answer.sdp,'new');assert.equal(view.player.retryAttempts,1);
  view.player.dispose();
});

test('legacy MJPEG cameras use the same limited retry budget and a fresh stream request',async()=>{
  const view=fixture();view.player.desired=true;view.player.update({printer:{id:'old'},camera:{webrtc:false}},true);
  const firstUrl=view.image.src;view.image.onerror();
  for(let attempt=1;attempt<=3;attempt++){await view.advance(5000);assert.notEqual(view.image.src,firstUrl);assert.equal(view.player.retryAttempts,attempt);view.image.onerror();}
  assert.equal(view.player.phase,'error');assert.equal(view.timers.size,0);view.player.dispose();
});

test('a frozen WebRTC picture enters the automatic retry cycle',async()=>{
  const view=fixture();view.player.desired=true;view.player.update(printer('one'),true);await flush();view.attach();
  await view.advance(24000);assert.equal(view.player.phase,'retrying');assert.match(view.player.message.textContent,/Нет свежего видео/);
  await view.advance(5000);assert.equal(view.peers.length,2);view.player.dispose();
});
