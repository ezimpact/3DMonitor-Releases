import {test} from 'node:test';
import assert from 'node:assert/strict';
import {groupPlan,targetUnchanged,setupWallGroup} from '../k1monitor/static/wall-group.js';
const item=(id,changes={})=>({printer:{id,name:id,ip:'192.168.254.'+id},connection:{online:true,stale:false,sessions:1},print:{file:'job.gcode',job_id:'job-1'},status:'printing',light_on:false,controls:{pause:true,stop:true},...changes});
test('group actions include only camera-wall members and pause never resumes paused jobs',()=>{
 const printers=[item('1'),item('2',{status:'paused'}),item('3'),item('4',{connection:{online:false}})];
 const plan=groupPlan(printers,new Set(['1','2','4']),'pause');assert.deepEqual(plan.targets.map(p=>p.printer.id),['1']);assert.deepEqual(plan.skipped,['2','4']);
 const light=groupPlan([item('1',{light_on:true}),item('2')],new Set(['1','2']),'light');assert.equal(light.value,true);
 assert.equal(groupPlan([item('1',{light_on:true})],new Set(['1']),'light').value,false);
});
test('group snapshots reject changed IP, session, job and offline state',()=>{
 const saved=item('1');assert.equal(targetUnchanged(saved,item('1'),'stop'),true);
 for(const current of [item('1',{printer:{...saved.printer,ip:'192.168.254.99'}}),item('1',{connection:{online:true,sessions:2}}),item('1',{print:{file:'job.gcode',job_id:'job-2'}}),item('1',{connection:{online:false}})])assert.equal(targetUnchanged(saved,current,'stop'),false);
});
function fixture(){
 const nodes=new Map();globalThis.document={getElementById(id){if(!nodes.has(id))nodes.set(id,{handlers:{},setAttribute(){},addEventListener(name,fn){this.handlers[name]=fn;},showModal(){this.open=true;},close(){this.open=false;}});return nodes.get(id);}};
 const printers=[item('1'),item('2'),item('3')],ids=new Set(['1','2']),calls=[];
 const ui=setupWallGroup({getPrinters:()=>printers,getIds:()=>ids,isBusy:()=>false,api:async(path,method,body)=>{calls.push({path,method,body});return {message:'mock only'};}});
 return {printers,ids,calls,ui,$:id=>document.getElementById(id)};
}
test('confirmation sends only mocked commands to pinned visible printers',async()=>{
 const f=fixture();f.$('wall-all-stop').handlers.click();assert.equal(f.calls.length,0);assert.match(f.$('wall-group-targets').textContent,/1/);
 await f.$('wall-group-submit').handlers.click();assert.equal(f.calls.length,2);assert.deepEqual(f.calls.map(c=>c.path),['/printers/1/control','/printers/2/control']);
 assert.equal(f.calls[0].body.expected_ip,'192.168.254.1');assert.equal(f.calls[0].body.expected_job,'job-1');assert.equal(f.calls[0].body.action,'stop');
});
test('changing camera membership invalidates an open group confirmation',async()=>{
 const f=fixture();f.$('wall-all-pause').handlers.click();f.ids.delete('2');await f.$('wall-group-submit').handlers.click();assert.equal(f.calls.length,0);assert.match(f.$('wall-group-results').textContent,/Состав камер изменился/);
});
test('changed job is skipped while other confirmed targets retain identity',async()=>{
 const f=fixture();f.$('wall-all-light').handlers.click();f.printers[0].print.job_id='new-job';await f.$('wall-group-submit').handlers.click();assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/printers/2/settings-command');assert.equal(f.calls[0].body.value,true);assert.match(f.$('wall-group-results').textContent,/задание изменилось/);
});
