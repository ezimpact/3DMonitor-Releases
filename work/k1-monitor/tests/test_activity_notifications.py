import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from k1monitor.activity import ActivityStore,ActivityTracker,native_record,ActivityService
from k1monitor.notifications import Notifications,TelegramError,EVENTS,text,secret
from fastapi.testclient import TestClient
from k1monitor.server import create_app
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.remote import RemoteAccess

def sample(status='idle',pid='one',elapsed=0,file='part.gcode',runout=False,error=None,online=True):
    return {'printer':{'id':pid,'name':pid,'ip':'192.168.4.10','protocol':'creality','api_port':9999},'connection':{'online':online,'stale':not online},'status':status,'state_code':{'printing':1,'paused':5,'completed':2,'stopped':4,'failed':3}.get(status,0),'print':{'file':file,'elapsed_seconds':elapsed,'remaining_seconds':3600,'progress_percent':100 if status=='completed' else 25},'filament_runout':runout,'error':error or {'active':False}}

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=ActivityStore(Path(self.temp.name)/'history.sqlite3');self.now=1700000000;self.tracker=ActivityTracker(self.store,lambda:self.now)
    def tick(self,item,seconds=1):self.now+=seconds;return self.tracker.update([item])
    def test_start_pause_resume_complete_once_and_isolated_history(self):
        self.tick(sample());self.assertEqual([e['kind'] for e in self.tick(sample('printing'))],['started'])
        self.assertEqual([e['kind'] for e in self.tick(sample('paused',elapsed=20))],['paused'])
        self.assertEqual([e['kind'] for e in self.tick(sample('printing',elapsed=30))],['resumed'])
        self.assertEqual(self.tick(sample('completed',elapsed=40)),[])
        self.assertEqual([e['kind'] for e in self.tick(sample('completed',elapsed=40))],['completed']);self.assertEqual(self.tick(sample('completed',elapsed=40)),[])
        records=self.store.list('one')['items'];self.assertEqual(len(records),1);self.assertEqual(records[0]['status'],'completed');self.assertEqual(self.store.list('two')['total'],0)
    def test_runout_error_masks_pause_without_duplicate_messages(self):
        self.tick(sample());self.tick(sample('printing'));item=sample('paused',runout=True,error={'active':True,'code':2839});item['status']='error'
        self.assertEqual([e['kind'] for e in self.tick(item)],['paused','runout']);self.assertEqual(self.tick(item),[])
        item['error']={'active':True,'code':999};item['filament_runout']=False
        self.assertEqual([e['kind'] for e in self.tick(item)],['error'])
    def test_offline_delay_active_only_and_reconnect(self):
        self.tick(sample());self.tick(sample(online=False),60);self.assertEqual(self.tick(sample(online=False),60),[])
        self.tick(sample('printing'));self.assertEqual(self.tick(sample('printing',online=False)),[])
        self.assertEqual(self.tick(sample('printing',online=False),29),[])
        self.assertEqual(self.tick(sample('printing',online=False),1),[])
        job_id=self.tracker.running['one']['id']
        self.assertEqual(self.tick(sample('printing',online=False),60),[]);self.assertEqual(self.tick(sample('printing')),[])
        self.assertEqual(self.tracker.running['one']['id'],job_id)
        self.assertEqual(self.store.list('one')['items'][0]['status'],'printing')
    def test_new_idle_error_notifies_once_but_initial_error_is_silent(self):
        item=sample('error',file='',error={'active':True,'code':999,'message':'Ошибка датчика'})
        self.assertEqual(self.tick(item),[])
        self.tick(sample(file=''))
        self.assertEqual([e['kind'] for e in self.tick(item)],['error']);self.assertEqual(self.tick(item),[])
    def test_restart_suppresses_initial_errors_and_finished_replay(self):
        self.tick(sample('printing'));self.tracker.flush();self.tracker=ActivityTracker(self.store,lambda:self.now)
        self.assertEqual(self.tick(sample('completed')),[]);self.assertEqual(self.tick(sample('completed')),[])
        self.assertEqual(self.store.list('one')['items'][0]['status'],'completed')
        self.tracker=ActivityTracker(self.store,lambda:self.now);self.assertEqual(self.tick(sample('paused',runout=True,error={'active':True,'code':2839})),[])
    def test_restart_continues_running_record_without_duplicate_start(self):
        self.tick(sample('printing',elapsed=120));self.tracker.flush();self.tracker=ActivityTracker(self.store,lambda:self.now)
        self.assertEqual(self.tick(sample('printing',elapsed=130)),[]);self.assertEqual(self.store.list('one')['total'],1)
        self.tick(sample('completed',elapsed=150));self.assertEqual([e['kind'] for e in self.tick(sample('completed',elapsed=150))],['completed'])
    def test_file_change_and_idle_do_not_guess_success(self):
        self.tick(sample());self.tick(sample('printing',file='first.gcode'));self.tick(sample('printing',file='second.gcode'))
        self.tick(sample('idle'));self.tick(sample('idle'))
        records=self.store.list('one')['items'];self.assertEqual(len(records),2);self.assertTrue(all(r['status']=='unknown' for r in records))
    def test_cancel_and_failed_are_not_success(self):
        for state,event in [('stopped','cancelled'),('failed','failed')]:
            self.tick(sample());self.tick(sample('printing'));self.tick(sample(state));self.assertEqual([e['kind'] for e in self.tick(sample(state))],[event])
    def test_native_import_idempotent_and_invalid_dates_rejected(self):
        p=sample()['printer'];row={'id':10,'filename':'part.gcode','starttime':self.now,'usagetime':45,'printfinish':1}
        record=native_record(p,row,'creality');self.store.imported(record);self.store.imported(record)
        self.assertEqual(self.store.list('one')['total'],1);self.assertEqual(self.store.list('one')['items'][0]['source'],'printer')
        self.assertIsNone(native_record(p,{**row,'starttime':0},'creality'))
        self.assertEqual(native_record(p,{**row,'printfinish':0},'creality')['status'],'unknown')
    def test_merge_local_and_native_by_exact_path_and_start(self):
        self.tick(sample('printing'));self.tick(sample('completed'));self.tick(sample('completed'))
        first=self.store.list('one')['items'][0]
        row={'id':22,'filename':'part.gcode','starttime':first['started'],'usagetime':45,'printfinish':1}
        self.store.imported(native_record(sample()['printer'],row,'creality'));self.assertEqual(self.store.list('one')['total'],1)
        row['id']=23;row['filename']='folder/part.gcode';self.store.imported(native_record(sample()['printer'],row,'creality'));self.assertEqual(self.store.list('one')['total'],2)
    def test_native_moonraker_maps_completed_failed_and_skips_live(self):
        p=sample()['printer'];row={'job_id':'abc','filename':'part.gcode','start_time':self.now,'end_time':self.now+60,'print_duration':50,'status':'completed'}
        self.assertEqual(native_record(p,row,'moonraker')['elapsed'],50)
        self.assertEqual(native_record(p,{**row,'status':'error'},'moonraker')['status'],'failed')
        self.assertIsNone(native_record(p,{**row,'status':'in_progress'},'moonraker'))
    def test_windows_secret_roundtrip(self):self.assertEqual(secret(secret('sample secret'),True),'sample secret')
    def test_octoprint_last_print_import_is_partial_and_idempotent(self):
        row={'path':'folder/file.gcode','prints':{'last':{'date':self.now,'printTime':50,'success':True}}}
        record=native_record(sample()['printer'],row,'octoprint');self.assertIsNone(record['started']);self.assertEqual(record['last_printed'],self.now)
        self.store.imported(record);self.store.imported(record);self.assertEqual(self.store.list('one')['total'],1)
        self.assertIsNone(native_record(sample()['printer'],{'path':'x'},'octoprint'))

class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.now=1700000000;self.store=ActivityStore(Path(self.temp.name)/'history.sqlite3');self.sent=[];self.native=[]
        self.notifications=Notifications(Path(self.temp.name)/'notifications.json',self.store,lambda *args:self.native.append(args),self.request,lambda:self.now)
    def request(self,token,method,body):
        self.sent.append((method,body))
        return {'username':'fixture_bot'} if method=='getMe' else {'url':''} if method=='getWebhookInfo' else [] if method=='getUpdates' else {'message_id':1}
    def event(self,kind='started'):return {'id':'unique','created':self.now,'kind':kind,'name':'K1C','file':'part.gcode','remaining':3600}
    async def test_preferences_protect_and_redact_token(self):
        token='123456789:'+('a'*30);result=await self.notifications.configure(True,False,list(EVENTS),token)
        self.assertNotIn(token,json.dumps(result));self.assertTrue(result['has_token']);self.assertNotIn('token',result)
        loaded=Notifications(self.notifications.path,self.store);self.assertEqual(loaded.token,token)
    async def test_removed_connection_events_migrate_without_changing_other_settings(self):
        await self.notifications.configure(False,True,['completed','paused'],'123456789:'+('a'*30))
        self.notifications.data.update(events=['completed','offline','paused','online'],chats=[{'id':7,'name':'Me'}],update_offset=20);self.notifications.save()
        before=json.loads(self.notifications.path.read_text());loaded=Notifications(self.notifications.path,self.store)
        after=json.loads(self.notifications.path.read_text());self.assertEqual(after,{**before,'events':['completed','paused']});self.assertEqual(loaded.public()['events'],['completed','paused']);self.assertNotIn('offline',EVENTS);self.assertNotIn('online',EVENTS);self.assertIn('filament_low',EVENTS);self.assertIn('temperature_warning',EVENTS)
    async def test_removed_queued_events_never_reach_either_channel_even_with_stale_preferences(self):
        self.notifications.token='fixture';self.notifications.data.update(telegram=True,chats=[{'id':7}],events=['offline','online'])
        for kind in ['offline','online']:
            event={**self.event(kind),'id':kind,'recipients':[7]};self.notifications.enqueue(event)
            self.store.enqueue(event,True,True) # A queue saved by an older release.
        await self.notifications.dispatch();self.assertEqual(self.sent,[]);self.assertEqual(self.native,[]);self.assertEqual(self.store.pending(self.now),[])
    async def test_cached_old_form_strips_removed_events_but_rejects_unknown_event(self):
        await self.notifications.configure(False,False,['completed','offline','online']);self.assertEqual(self.notifications.public()['events'],['completed'])
        with self.assertRaises(ValueError):await self.notifications.configure(False,False,['anything'])
    async def test_unconfigured_telegram_never_sends_and_events_are_deduplicated(self):
        event=self.event();self.notifications.enqueue(event);self.notifications.enqueue(event);await self.notifications.dispatch();await self.notifications.dispatch()
        self.assertEqual(len(self.native),1);self.assertEqual(self.sent,[])
    async def test_delivery_receipts_avoid_resending_to_successful_chat(self):
        self.notifications.token='fixture';self.notifications.data.update(telegram=True,chats=[{'id':1},{'id':2}]);self.notifications.enqueue(self.event())
        calls=[]
        def request(token,method,body):
            calls.append(body['chat_id'])
            if body['chat_id']==2 and calls.count(2)==1:raise TelegramError('temporary')
            return {}
        self.notifications.request=request;await self.notifications.dispatch();self.now+=120;await self.notifications.dispatch();self.assertEqual(calls,[1,2,2])
    async def test_expired_alerts_do_not_spam_after_restart(self):
        self.notifications.enqueue(self.event());self.now+=901;await self.notifications.dispatch();self.assertEqual(self.native,[])
    async def test_disabled_event_and_channel_do_not_send(self):
        self.notifications.data['events']=[];self.notifications.enqueue(self.event());await self.notifications.dispatch();self.assertEqual(self.native,[])
        self.notifications.data['events']=list(EVENTS);self.notifications.enqueue(self.event());self.notifications.data['windows']=False;await self.notifications.dispatch();self.assertEqual(self.native,[])
    async def test_webhook_bot_is_rejected_without_deleting_webhook(self):
        def request(token,method,body):return {'url':'https://other.example'} if method=='getWebhookInfo' else {'username':'other'}
        self.notifications.request=request
        with self.assertRaisesRegex(ValueError,'webhook'):await self.notifications.configure(True,True,list(EVENTS),'123456789:'+('a'*30))
        self.assertEqual(self.notifications.token,'')
    async def test_pairing_requires_exact_code_and_private_chat(self):
        self.notifications.token='fixture';self.notifications.pairing={'code':'ABCDEFGH','expires':self.now+300}
        def request(token,method,body):
            if method=='getUpdates':return [{'update_id':1,'message':{'text':'wrong','chat':{'id':1,'type':'private'}}},{'update_id':2,'message':{'text':'ABCDEFGH','chat':{'id':2,'type':'group'}}},{'update_id':3,'message':{'text':'/start ABCDEFGH','chat':{'id':3,'type':'private','first_name':'User'}}}]
            self.sent.append((method,body));return {}
        self.notifications.request=request;await self.notifications.poll_pairing(0)
        self.assertEqual(self.notifications.data['chats'],[{'id':3,'name':'User'}]);self.assertIsNone(self.notifications.pairing);self.assertEqual(len(self.sent),1)
    async def test_message_includes_eta_and_pause_qualification(self):
        result=text({**self.event('paused'),'paused':True});self.assertIn('Примерное окончание',result);self.assertIn('прогноз изменится',result)
        self.assertNotIn('Примерное окончание',text(self.event('completed')))
    async def test_octoprint_explicit_finishing_and_cancelled_latch_terminal(self):
        from k1monitor.http_printers import OctoPrintMonitor
        for previous,code in [('Finishing',2),('Cancelling',4)]:
            monitor=OctoPrintMonitor('192.168.254.250','fixture');monitor.raw_state=previous;monitor._last_job=('part.gcode',True)
            async def get(path):return {'state':'Operational','job':{'file':{}},'progress':{}} if path=='/api/job' else {'temperature':{}}
            monitor._get=get
            await monitor._read_status();self.assertEqual(monitor.state.data['state'],code)
            await monitor._read_status();self.assertEqual(monitor.state.data['state'],code)

    async def test_octoprint_missed_finishing_requires_new_successful_file_history(self):
        from k1monitor.http_printers import OctoPrintMonitor
        for new_date,success,expected in [(200,True,2),(100,True,0),(200,False,0),(None,True,0)]:
            monitor=OctoPrintMonitor('192.168.254.250','fixture');phase='Printing';date=100;result=True;calls=[]
            async def get(path):
                calls.append(path)
                if path=='/api/job':return {'state':phase,'job':{'file':{'path':'folder/part.gcode','origin':'local'}},'progress':{'printTime':30 if phase=='Printing' else None}}
                if path=='/api/printer':return {'temperature':{}}
                return {'prints':{'last':{'date':date,'success':result}}}
            monitor._get=get;await monitor._read_status()
            phase='Operational';date=new_date;result=success
            await monitor._read_status();self.assertEqual(monitor.state.data['state'],expected)
            await monitor._read_status();self.assertEqual(monitor.state.data['state'],expected)
            self.assertEqual(calls.count('/api/files/local/folder/part.gcode'),2)

class ActivityApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        (root/'data').mkdir();(root/'data/printers.json').write_text(json.dumps({'schema_version':1,'printers':[]}))
        (root/'config.json').write_text(json.dumps({'registry_path':'data/printers.json','poll_seconds':5,'stale_after_seconds':15}))
        self.manager=PrinterManager(ConfigStore(root/'config.json'));self.notifications=Notifications(root/'notifications.json',ActivityStore(root/'history.sqlite3'),lambda *args:None,request=lambda *args:{})
        self.activity=ActivityService(self.manager,self.notifications);self.remote=RemoteAccess(root/'remote.json');self.remote.configure('pc.tail123.ts.net')
        self.app=create_app(self.manager,self.remote,activity=self.activity,csrf_token='test-token')
        self.client=TestClient(self.app,base_url='http://127.0.0.1')
    def test_notification_api_requires_csrf_and_never_echoes_secrets(self):
        body={'windows':False,'telegram':False,'events':['completed']}
        self.assertEqual(self.client.put('/api/v1/notifications',json=body).status_code,403)
        reply=self.client.put('/api/v1/notifications',json=body,headers={'x-k1-token':'test-token'});self.assertEqual(reply.status_code,200);self.assertNotIn('token',reply.json())
        reply=self.client.put('/api/v1/notifications',json={**body,'token':'SECRET','events':7},headers={'x-k1-token':'test-token'});self.assertEqual(reply.status_code,422);self.assertNotIn('SECRET',reply.text)
    def test_remote_cannot_read_bot_configuration(self):
        client=TestClient(self.app,base_url='https://pc.tail123.ts.net',client=('127.0.0.1',1234))
        reply=client.get('/api/v1/notifications',headers={'tailscale-user-login':'owner@example.com'});self.assertEqual(reply.status_code,403)
    def test_unknown_printer_history_is_not_exposed(self):self.assertEqual(self.client.get('/api/v1/printers/missing/history').status_code,404)
