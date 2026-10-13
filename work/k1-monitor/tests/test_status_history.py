"""Read-only history preparation, Telegram commands and camera frames; no print calls."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient
from k1monitor.activity import ActivityStore,ActivityService,endpoint
from k1monitor.notifications import Notifications,TelegramError,telegram_request
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app
from k1monitor.files import FileCatalog
from k1monitor.relay import CameraRelay
from test_monitor import FakeMonitor
from test_activity_notifications import sample
from test_relay import Browser,JPEG

class HistoryReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        (root/'config.json').write_text(json.dumps({'printers':[{'id':'one','name':'One','ip':'192.168.254.10'}]}))
        self.manager=PrinterManager(ConfigStore(root/'config.json'),factory=FakeMonitor);self.monitor=self.manager.get('one')
        self.store=ActivityStore(root/'history.sqlite3');self.activity=ActivityService(self.manager,Notifications(root/'notifications.json',self.store))
        self.snap=sample();self.snap['controls']={'start':True};self.snap['connection']['sessions']=1
        self.monitor.snapshot=lambda:self.snap;self.monitor.state.connection_count=1
        catalog=FileCatalog();catalog.ingest({'retGcodeFileInfo2':[{'path':'/usr/data/printer_data/gcodes/folder/one.gcode','file_size':123,'create_time':1},{'path':'/usr/data/printer_data/gcodes/other/one.gcode','file_size':456,'create_time':1}]})
        self.monitor.list_files=AsyncMock(return_value=catalog)
        self.record={'id':'job','printer_id':'one','endpoint':endpoint(self.manager.entry('one')),'file':'folder/one.gcode','started':1700000000,'updated':1700000100,'status':'completed'}
        self.store.save(self.record);self.client=TestClient(create_app(self.manager,activity=self.activity),base_url='http://127.0.0.1')
        self.url='/api/v1/printers/one/history/job/file'
    def test_exact_folder_catalog_and_existing_confirmation_binding(self):
        response=self.client.get(self.url);self.assertEqual(response.status_code,200)
        data=response.json();file=next(f for f in data['catalog']['files'] if f['id']==data['file_id']);self.assertEqual(file['relative_path'],'folder/one.gcode');self.assertEqual(data['catalog']['session'],1)
        self.assertEqual(self.monitor.list_files.await_count,1)
    def test_deleted_file_does_not_fall_back_to_same_basename(self):
        self.store.save({**self.record,'file':'missing/one.gcode'});self.assertEqual(self.client.get(self.url).status_code,404)
    def test_cross_printer_record_missing_and_old_endpoint(self):
        self.store.save({**self.record,'printer_id':'another'});self.assertEqual(self.client.get(self.url).status_code,404)
        self.store.save({**self.record,'endpoint':'old'});self.assertEqual(self.client.get(self.url).status_code,409);self.assertEqual(self.monitor.list_files.await_count,0)
    def test_busy_and_offline_never_read_catalog(self):
        self.snap['controls']['start']=False;self.assertEqual(self.client.get(self.url).status_code,409)
        self.snap['controls']['start']=True;self.snap['connection']['online']=False;self.assertEqual(self.client.get(self.url).status_code,409);self.assertEqual(self.monitor.list_files.await_count,0)
    def test_reconnect_during_catalog_fetch_rejected(self):
        async def fetch(**kwargs):self.monitor.state.connection_count+=1;return FileCatalog()
        self.monitor.list_files=fetch;self.assertEqual(self.client.get(self.url).status_code,409)
    def test_octoprint_unknown_storage_rejected(self):
        self.manager.entry('one')['protocol']='octoprint';self.store.save({**self.record,'endpoint':endpoint(self.manager.entry('one'))})
        self.assertEqual(self.client.get(self.url).status_code,409)
        self.store.save({**self.record,'endpoint':endpoint(self.manager.entry('one')),'file_origin':'local'});self.assertEqual(self.client.get(self.url).status_code,200)

class BotStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name);self.now=1700000000;self.sent=[];self.updates=[]
        self.store=ActivityStore(root/'history.sqlite3');self.bot=Notifications(root/'notifications.json',self.store,request=self.request,clock=lambda:self.now)
        self.bot.token='fixture';self.bot.data.update(telegram=True,chats=[{'id':7,'name':'Me'}],bot_username='fixture_bot')
        self.bot.status_provider=lambda:[{'printer_id':'one','endpoint':'test','name':'K1','text':'K1 — Печатает\nФайл: тест.gcode\nНачало: 10:00\nПримерное окончание: 11:00','camera':True},{'printer_id':'two','endpoint':'test','name':'V400','text':'V400 — Ожидает\nКамера: нет или не настроена','camera':False}]
        self.bot.photo_provider=AsyncMock(return_value=JPEG)
        self.bot.status_retry_delay=0
    def request(self,token,method,body):
        if method=='getUpdates':return [u for u in self.updates if u['update_id']>=body['offset']]
        self.sent.append((method,body));return {'message_id':1}
    def update(self,command='статус',chat=7,type='private',date=None,id=1):
        return {'update_id':id,'message':{'text':command,'date':self.now if date is None else date,'chat':{'id':chat,'type':type}}}
    async def test_status_all_printers_text_then_camera_and_restart_offset(self):
        self.updates=[self.update()];await self.bot.poll_commands_once()
        self.assertEqual([method for method,body in self.sent],['sendPhoto','sendMessage']);self.assertIn('V400',self.sent[1][1]['text']);self.assertEqual(self.sent[0][1]['photo'],JPEG);self.assertIn('Начало',self.sent[0][1]['caption']);self.assertTrue(self.sent[0][1]['caption'].startswith('[1/2]'));self.assertTrue(self.sent[1][1]['text'].startswith('[2/2]'))
        self.assertEqual(json.loads(self.bot.path.read_text())['update_offset'],2)
        self.now+=60;await self.bot.poll_commands_once();self.assertEqual(len(self.sent),2)
    async def test_unpaired_group_stale_unknown_ignored_no_capture(self):
        self.updates=[self.update(chat=8,id=1),self.update(type='group',id=2),self.update(date=self.now-121,id=3),self.update(command='печать',id=4)]
        await self.bot.poll_commands_once();self.assertEqual(self.sent,[]);self.bot.photo_provider.assert_not_awaited()
    async def test_disabled_telegram_and_pairing_do_not_consume_updates(self):
        self.bot.data['telegram']=False;self.updates=[self.update()];await self.bot.poll_commands_once();self.assertEqual(self.sent,[]);self.assertNotIn('update_offset',self.bot.data)
        self.bot.data['telegram']=True;self.bot.pair_task=asyncio.create_task(asyncio.sleep(99));await self.bot.poll_commands_once();self.assertNotIn('update_offset',self.bot.data);await self.bot.stop()
    async def test_command_alias_and_rate_limit(self):
        self.updates=[self.update('/status@fixture_bot')];await self.bot.poll_commands_once();self.updates.append(self.update('/status',id=2));await self.bot.poll_commands_once();self.assertEqual(len(self.sent),2)
        self.now+=31;self.updates.append(self.update(' СТАТУС ',id=3));await self.bot.poll_commands_once();self.assertEqual(len(self.sent),4)
    async def test_unavailable_photo_keeps_text_and_other_printers(self):
        self.bot.photo_provider.side_effect=TimeoutError();self.updates=[self.update()];await self.bot.poll_commands_once();self.assertEqual([m for m,b in self.sent],['sendMessage','sendMessage']);self.assertIn('3 попыток',self.sent[0][1]['text']);self.assertIn('Начало',self.sent[0][1]['text']);self.assertIn('V400',self.sent[1][1]['text']);self.assertEqual(self.bot.photo_provider.await_count,3)
    async def test_chat_removed_while_capturing_receives_no_photo(self):
        async def capture(*args):self.bot.data['chats']=[];return JPEG
        self.bot.photo_provider=capture;self.updates=[self.update()];await self.bot.poll_commands_once();self.assertEqual(len(self.sent),0);self.assertEqual(self.bot.status_chats,set())
    async def test_status_large_list_chunks_without_truncation(self):
        self.bot.status_provider=lambda:[{'name':str(i),'text':str(i)+'x'*1500,'camera':False} for i in range(9)]
        await self.bot.reply_status(7,'fixture');self.assertEqual(len(self.sent),9);self.assertTrue(all(len(body['text'])<4096 for _,body in self.sent));self.assertIn('8xxx',self.sent[-1][1]['text'])
    async def test_reports_pause_eta_and_offline_no_false_forecast(self):
        items=[sample('printing',elapsed=120),sample('paused',pid='two',elapsed=180),sample('printing',pid='three',online=False),sample('idle',pid='four')]
        manager=type('Manager',(),{'snapshot':lambda _: {'printers':items}})();service=ActivityService(manager,self.bot)
        reports=service.status_reports();self.assertIn('Примерное окончание:',reports[0]['text']);self.assertIn('изменится',reports[1]['text']);self.assertIn('окончание: неизвестно',reports[2]['text']);self.assertIn('\n—\n',reports[3]['text'])
    async def test_relay_snapshot_cancellation_releases_own_subscription(self):
        path=Path(self.temp.name)/'config.json';path.write_text(json.dumps({'printers':[{'id':'one','name':'One','ip':'192.168.254.10'}]}))
        relay=CameraRelay(PrinterManager(ConfigStore(path),factory=FakeMonitor),Browser(),'http://127.0.0.1:8778/')
        task=asyncio.create_task(relay.snapshot('one'));await asyncio.sleep(0);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(relay.sources['one'].viewers);self.assertIsNotNone(relay.sources['one'].idle_since)
    async def test_relay_snapshot_uses_shared_frame_keeps_existing_viewer(self):
        path=Path(self.temp.name)/'config.json';path.write_text(json.dumps({'printers':[{'id':'one','name':'One','ip':'192.168.254.10'}]}))
        relay=CameraRelay(PrinterManager(ConfigStore(path),factory=FakeMonitor),Browser(),'http://127.0.0.1:8778/');queue=await relay.subscribe('one');relay.frame('one',relay.sources['one'].generation,JPEG)
        self.assertEqual(await relay.snapshot('one'),JPEG);self.assertEqual(relay.sources['one'].viewers,{queue})
    async def test_photo_request_multipart_tls_keeps_binary_and_caption(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'{"ok":true,"result":{}}'
        with patch('k1monitor.notifications.build_opener') as opener:
            opener.return_value.open.return_value=Response();telegram_request('fixture','sendPhoto',{'chat_id':7,'photo':JPEG,'caption':'Снимок'})
            request=opener.return_value.open.call_args.args[0];self.assertIn(JPEG,request.data);self.assertIn('Снимок'.encode(),request.data);self.assertIn('multipart/form-data',request.headers['Content-type'])
