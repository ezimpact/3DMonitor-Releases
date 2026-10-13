"""Beta adapters against simulated HTTP servers. No start_print or hardware calls."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from k1monitor.http_printers import OctoPrintMonitor,PrusaLinkMonitor,octo_command
from k1monitor.credentials import CredentialsStore,validate_credentials
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app
from k1monitor.file_transfers import FileTransfers
from test_monitor import FakeMonitor

CONTENT=b'; mock transfer only\nG90\n'

class Fixture:
    def __init__(self,protocol='octoprint',state=None,credentials=None):
        self.prusa=protocol=='prusalink';self.calls=[];self.state=state or ('PRINTING' if self.prusa else 'Printing')
        self.id=12;self.elapsed=40;self.temperature=True;self.uploaded=False;self.fail=False;self.on_read=lambda:None
        adapter=PrusaLinkMonitor if self.prusa else OctoPrintMonitor
        self.m=adapter('192.168.254.250','mock',credentials=credentials,poll_seconds=.01)
    def handler(self,req):
        self.calls.append(req)
        if req.method!='GET':
            if self.fail:raise httpx.ReadTimeout('mock missing response',request=req)
            if req.url.path.endswith('/pause') or not self.prusa and req.url.path=='/api/job' and json.loads(req.content).get('action')=='pause':
                self.state='PAUSED' if self.prusa else 'Paused'
            self.uploaded=True
            return httpx.Response(204)
        path=req.url.path
        if path=='/api/version':return httpx.Response(200,json={'text':'PrusaLink' if self.prusa else 'OctoPrint 1.11','server':'mock','capabilities':{'upload-by-put':True}})
        if path=='/api/v1/storage':return httpx.Response(200,json={'storage_list':[{'path':'/usb','available':True,'read_only':False}]})
        if path=='/api/v1/status':
            self.on_read()
            return httpx.Response(200,json={'printer':{'state':self.state,'temp_nozzle':205,'target_nozzle':210,'temp_bed':59,'target_bed':60,'speed':100}})
        if path=='/api/v1/job':return httpx.Response(200,json={'id':self.id,'state':self.state,'file':{'path':'/usb/ONE.GCO','name':'ONE.GCO','display_name':'One.gcode'},'progress':25,'time_printing':self.elapsed,'time_remaining':120})
        if path=='/api/job':
            self.on_read()
            return httpx.Response(200,json={'state':self.state,'job':{'file':{'path':'one.gcode','name':'one.gcode'}},'progress':{'completion':25,'printTime':self.elapsed,'printTimeLeft':120}})
        if path=='/api/printer':return httpx.Response(200,json={'temperature':{'tool0':{'actual':205,'target':210},'bed':{'actual':59,'target':60}} if self.temperature else {}})
        if path.startswith('/api/files/local'):
            files=[{'type':'folder','path':'nested','children':[{'name':'one.gcode','size':len(CONTENT),'date':1,'refs':{'download':'/downloads/files/local/nested/one.gcode'}}]},{'type':'folder','path':'empty','children':[]}]
            if self.uploaded:files.append({'name':'new.gcode','size':len(CONTENT),'date':2})
            return httpx.Response(200,json={'files':files})
        if path=='/api/v1/files/usb':
            files=[{'type':'PRINT_FILE','name':'ONE.GCO','display_name':'One.gcode','size':len(CONTENT),'m_timestamp':1,'refs':{'download':'/api/files/usb/ONE.GCO/raw','thumbnail':'/api/thumb.png'}}]
            if self.uploaded:files.append({'type':'PRINT_FILE','name':'new.gcode','size':len(CONTENT),'m_timestamp':2})
            return httpx.Response(200,json={'children':files})
        if path=='/api/thumb.png':return httpx.Response(200,content=b'\x89PNG\r\n\x1a\nmock')
        if path.startswith('/downloads/') or path.endswith('/raw'):return httpx.Response(200,content=CONTENT,headers={'content-type':'text/plain'})
        return httpx.Response(404)
    def client(self,**kw):return httpx.AsyncClient(transport=httpx.MockTransport(self.handler),**kw)
    async def prepare(self):
        self.m._client=self.client(**self.m.http_options());await self.m._initialize();self.m.state.begin_session();self.m._ws=self.m
        await self.m._refresh();return self.m
    async def close(self):await self.m._client.aclose()

class HTTPAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self,**kwargs):
        f=Fixture(**kwargs);await f.prepare();self.addAsyncCleanup(f.close);return f
    async def test_octo_status_and_no_unsupported_automation(self):
        f=await self.fixture();s=f.m.snapshot()
        self.assertEqual(s['status'],'printing');self.assertEqual(s['print']['progress_percent'],25)
        self.assertEqual(s['temperatures']['nozzle_target'],210);self.assertTrue(s['controls']['pause']);self.assertTrue(s['controls']['speed'])
        self.assertFalse(s['camera']['present']);self.assertFalse(s['auto_unload']['supported']);self.assertFalse(s['unload_eligible'])
        self.assertFalse(s['controls']['exclude']);self.assertFalse(s['capabilities']['calibration'])
        self.assertTrue(all(r.method=='GET' for r in f.calls))
    async def test_octo_idle_selected_file_does_not_mean_preparing(self):
        f=await self.fixture(state='Operational');s=f.m.snapshot();self.assertEqual(s['status'],'idle');self.assertFalse(s['print']['file']);self.assertTrue(s['controls']['start'])
    async def test_octo_missing_heaters_clear_old_values(self):
        f=await self.fixture();f.temperature=False;await f.m._refresh();s=f.m.snapshot()
        self.assertIsNone(s['temperatures']['nozzle']);self.assertFalse(s['controls']['nozzle']);self.assertFalse(s['controls']['bed'])
    async def test_octo_offline_unknown_transitions_disable_controls(self):
        f=await self.fixture()
        for state in ('Offline','Offline after error','Unknown','Cancelling','Pausing','Printing from SD'):
            f.state=state;await f.m._refresh();self.assertFalse(any(f.m.snapshot()['controls'].values()),state)
    async def test_prusa_status_short_file_and_actual_job_id(self):
        f=await self.fixture(protocol='prusalink');s=f.m.snapshot()
        self.assertEqual(s['print']['file'],'usb/ONE.GCO');self.assertEqual(s['print']['job_id'],'12')
        self.assertTrue(s['controls']['pause']);self.assertFalse(s['controls']['nozzle']);self.assertFalse(s['controls']['speed'])
        self.assertEqual(s['print']['remaining_seconds'],120)
    async def test_prusa_busy_and_error_disable_controls(self):
        f=await self.fixture(protocol='prusalink')
        for state in ('BUSY','ATTENTION','ERROR','UNKNOWN'):
            f.state=state;await f.m._refresh();self.assertFalse(any(f.m.snapshot()['controls'].values()),state)
    async def test_prusa_no_job_is_not_a_pause_target(self):
        f=await self.fixture(protocol='prusalink');f.id=None;await f.m._refresh()
        self.assertFalse(f.m.snapshot()['controls']['pause'])
    async def test_prusa_idle_204_and_finished_job_can_offer_start(self):
        f=await self.fixture(protocol='prusalink',state='FINISHED');self.assertTrue(f.m.snapshot()['controls']['start'])
        original=f.handler
        def handler(req):
            if req.url.path=='/api/v1/job':return httpx.Response(204)
            return original(req)
        await f.m._client.aclose();f.m._client=httpx.AsyncClient(transport=httpx.MockTransport(handler));f.state='IDLE';await f.m._refresh()
        self.assertTrue(f.m.snapshot()['controls']['start']);self.assertIsNone(f.m.snapshot()['print']['job_id'])
    async def test_job_change_before_mutation_aborts_both_protocols(self):
        for protocol in ('octoprint','prusalink'):
            f=await self.fixture(protocol=protocol);s=f.m.snapshot()
            def change():
                f.id+=1;f.elapsed=1
            f.on_read=change
            result=await f.m.control('pause','mock-pause-change',1,s['print']['file'],expected_job=s['print']['job_id'])
            self.assertEqual(result['phase'],'unconfirmed');self.assertTrue(all(r.method=='GET' for r in f.calls))
    async def test_mock_pause_uses_explicit_action_or_pinned_prusa_id_once(self):
        for protocol in ('octoprint','prusalink'):
            f=await self.fixture(protocol=protocol);s=f.m.snapshot()
            result=await f.m.control('pause','mock-pause-once',1,s['print']['file'],expected_job=s['print']['job_id'])
            self.assertEqual(result['phase'],'confirmed');await f.m.control('pause','mock-pause-once',1,s['print']['file'],expected_job=s['print']['job_id'])
            writes=[r for r in f.calls if r.method!='GET'];self.assertEqual(len(writes),1)
            if f.prusa:self.assertEqual((writes[0].method,writes[0].url.path),('PUT','/api/v1/job/12/pause'))
            else:self.assertEqual(json.loads(writes[0].content),{'command':'pause','action':'pause'})
    async def test_missing_or_stale_job_is_rejected_without_query(self):
        f=await self.fixture();s=f.m.snapshot();count=len(f.calls)
        for job in (None,'old'):
            with self.assertRaises(ValueError):await f.m.control('pause','mock-stale',1,s['print']['file'],expected_job=job)
        self.assertEqual(len(f.calls),count)
    async def test_lost_reply_does_not_repeat_mutation(self):
        f=await self.fixture();f.fail=True;s=f.m.snapshot()
        for _ in range(2):self.assertEqual((await f.m.control('pause','mock-lost',1,s['print']['file'],expected_job=s['print']['job_id']))['phase'],'unconfirmed')
        self.assertEqual(len([r for r in f.calls if r.method!='GET']),1)
    async def test_external_pause_before_send_does_not_ack_or_send_second_pause(self):
        f=await self.fixture();s=f.m.snapshot();f.on_read=lambda:setattr(f,'state','Paused')
        result=await f.m.control('pause','mock-external',1,s['print']['file'],expected_job=s['print']['job_id'])
        self.assertEqual(result['phase'],'unconfirmed');self.assertTrue(all(r.method=='GET' for r in f.calls))
    async def test_files_nested_octo_and_prusa_shortname_thumbnail(self):
        for protocol in ('octoprint','prusalink'):
            f=await self.fixture(protocol=protocol);catalog=await f.m.list_files(refresh=True);entry=catalog.files[0]
            self.assertEqual(entry['path'],'usb/ONE.GCO' if f.prusa else 'nested/one.gcode');self.assertTrue(f.m.download_url(entry['path']).startswith(f.m.base_url))
            self.assertNotIn('_download',catalog.public()[0])
            self.assertEqual(catalog.public()[0]['relative_path'],entry['path'])
            self.assertIn('usb' if f.prusa else 'empty',[item['name'] for item in catalog.directory_entries('')])
            if f.prusa:self.assertEqual((await f.m.thumbnail(entry['path']))[1],'image/png')
            else:
                with self.assertRaises(ValueError):await f.m.thumbnail(entry['path'])
    async def test_cross_protocol_file_transfer_and_repeat_suppression(self):
        for to_prusa in (True,False):
            source=await self.fixture(protocol='octoprint' if to_prusa else 'prusalink',credentials={'mode':'apikey','api_key':'source-key'})
            target=await self.fixture(protocol='prusalink' if to_prusa else 'octoprint',state='IDLE' if to_prusa else 'Operational',credentials={'mode':'apikey','api_key':'target-key'})
            class Manager:
                def get(self,key):return source.m if key=='source' else target.m
                def entry(self,key):return {'name':key}
            def route(req):
                # Separate credentials remain bound to the selected client.
                expected='source-key' if '/raw' in req.url.path or req.url.path.startswith('/downloads/') else 'target-key'
                self.assertEqual(req.headers['X-Api-Key'],expected)
                return source.handler(req) if expected=='source-key' else target.handler(req)
            transfers=FileTransfers(Manager(),client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(route),**kw))
            catalog=await source.m.list_files();entry=catalog.files[0]
            spool,size,digest,name=await transfers.download('source',entry['id'],catalog.revision,1,source.m.state.host)
            try:
                for _ in range(2):self.assertEqual((await transfers.upload('target','new.gcode',spool,size,digest,1,target.m.state.host,'mock-transfer'))['phase'],'complete')
            finally:spool.close()
            writes=[r for r in target.calls if r.method!='GET'];self.assertEqual(len(writes),1);request=writes[0]
            if to_prusa:
                self.assertEqual(request.method,'PUT');self.assertEqual(request.content,CONTENT);self.assertEqual(request.headers['Print-After-Upload'],'?0');self.assertEqual(request.headers['Overwrite'],'?0')
            else:
                self.assertEqual(request.method,'POST');self.assertIn(b'name="print"\r\n\r\nfalse',request.content);self.assertIn(b'name="select"\r\n\r\nfalse',request.content)
    async def test_digest_upload_rewinds_after_expired_nonce(self):
        m=PrusaLinkMonitor('192.168.254.250','mock',credentials={'mode':'digest','username':'maker','password':'private-password'});m.storage='/usb';m.storages=[{'path':'/usb'}];m.put_upload=True
        puts=[];reads=0
        def challenge(nonce):return httpx.Response(401,headers={'WWW-Authenticate':f'Digest realm="Prusa", nonce="{nonce}", qop="auth", algorithm=MD5'})
        def handler(req):
            nonlocal reads
            if req.method=='GET':
                reads+=1
                if 'Authorization' not in req.headers:return challenge('first')
                return httpx.Response(200,json={})
            puts.append(req.content)
            if len(puts)==1:return challenge('expired')
            self.assertIn('nonce="expired"',req.headers['Authorization']);self.assertNotIn('private-password',req.headers['Authorization']);return httpx.Response(201)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),**m.http_options()) as client:await m.upload_file(client,'new.gcode',io.BytesIO(CONTENT),len(CONTENT))
        self.assertEqual(puts,[CONTENT,CONTENT]);self.assertEqual(reads,2)
    async def test_legacy_prusa_upload_never_selects_or_starts(self):
        f=await self.fixture(protocol='prusalink');f.m.put_upload=False
        await f.m.upload_file(f.m._client,'new.gcode',io.BytesIO(CONTENT),len(CONTENT))
        req=f.calls[-1];self.assertEqual(req.url.path,'/api/files/usb');self.assertIn(b'name="print"\r\n\r\nfalse',req.content)
    async def test_readonly_storage_refuses_upload(self):
        f=await self.fixture(protocol='prusalink');f.m.storages=[{'path':'/usb','read_only':True}];count=len(f.calls)
        with self.assertRaises(ValueError):await f.m.upload_file(f.m._client,'new.gcode',io.BytesIO(CONTENT),len(CONTENT))
        self.assertEqual(len(f.calls),count)
    async def test_upload_cannot_overwrite_actual_name_hidden_by_display_alias(self):
        f=await self.fixture();await f.m.list_files();f.m.files.files[0]['name']='Display alias.gcode'
        async def catalog(*,refresh=False):return f.m.files
        f.m.list_files=catalog
        class Manager:
            def get(self,key):return f.m
        transfers=FileTransfers(Manager(),client_factory=f.client)
        with self.assertRaises(ValueError):await transfers.upload('one','one.gcode',io.BytesIO(CONTENT),len(CONTENT),hashlib.sha256(CONTENT).hexdigest(),1,f.m.state.host,'mock-overwrite')
        self.assertTrue(all(r.method=='GET' for r in f.calls))
    async def test_authenticated_monitor_start_and_stop_are_get_only(self):
        f=Fixture(credentials={'mode':'apikey','api_key':'private-key'});real=httpx.AsyncClient
        with patch('k1monitor.http_printers.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(f.handler),**kw)):
            await f.m.start()
            try:
                for _ in range(100):
                    if f.m.snapshot()['connection']['online']:break
                    await asyncio.sleep(.01)
                self.assertTrue(f.m.snapshot()['connection']['online'])
            finally:await f.m.stop()
        self.assertTrue(all(r.method=='GET' for r in f.calls));self.assertTrue(all(r.headers.get('X-Api-Key')=='private-key' for r in f.calls));self.assertIsNone(f.m._client)
        self.assertNotIn('private-key',json.dumps(f.m.snapshot()))
    async def test_scan_http_requires_identity_and_never_sends_commands(self):
        from k1monitor.scanner import probe_printer
        real=httpx.AsyncClient
        for identity in ('OctoPrint 1.11','PrusaLink','Unrelated server'):
            calls=[]
            def handler(req):
                calls.append(req);return httpx.Response(200,json={'text':identity}) if req.url.path=='/api/version' else httpx.Response(404)
            with patch('k1monitor.scanner.probe_creality',return_value=None),patch('k1monitor.scanner.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
                result=await probe_printer('192.168.254.250')
            self.assertEqual(result is None,identity=='Unrelated server');self.assertTrue(all(r.method=='GET' for r in calls))

class ValidationTests(unittest.TestCase):
    def test_command_translation_only_no_launch_execution(self):
        self.assertEqual(octo_command({'opGcodeFile':'printprt:nested/one.gcode','enableSelfTest':0}),('POST','/api/files/local/nested/one.gcode',{'command':'select','print':True}))
        m=PrusaLinkMonitor('192.168.254.250','mock');m._job_id=99
        self.assertEqual(m.translate({'opGcodeFile':'printprt:usb/ONE.GCO','enableSelfTest':0}),('POST','/api/v1/files/usb/ONE.GCO',None))
        self.assertEqual(m.translate({'stop':1}),('DELETE','/api/v1/job/99',None));self.assertEqual(m.translate({'pause':0}),('PUT','/api/v1/job/99/resume',None))
        for monitor in (m,OctoPrintMonitor('192.168.254.250','mock')):
            with self.assertRaises(ValueError):monitor.translate({'opGcodeFile':'printprt:one.gcode','enableSelfTest':1})
            with self.assertRaises(ValueError):monitor.translate({'lightSw':1})
    def test_octo_temperature_and_speed_bounds(self):
        self.assertEqual(octo_command({'nozzleTempControl':240})[2],{'command':'target','targets':{'tool0':240}})
        self.assertEqual(octo_command({'bedTempControl':{'val':60}})[2],{'command':'target','target':60})
        self.assertEqual(octo_command({'setFeedratePct':50})[2],{'command':'feedrate','factor':50})
        for params in ({'setFeedratePct':10},{'nozzleTempControl':True},{'bedTempControl':{'val':101}}):
            with self.assertRaises(ValueError):octo_command(params)
    def test_file_urls_reject_traversal_and_external_auth_destinations(self):
        m=OctoPrintMonitor('192.168.254.250','mock')
        for value in ('http://127.0.0.1/a','http://192.168.254.250:8080/a','//evil/a','/a/%2e%2e/secret','/a/../secret','/a/%5cb','/a/%0d'):
            with self.assertRaises(ValueError):m.safe_url(value)
        self.assertEqual(m.safe_url('/downloads/a.gcode'),m.base_url+'/downloads/a.gcode')
    def test_credentials_validation_and_private_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'printer-credentials.json';store=CredentialsStore(path);self.assertFalse(path.exists())
            store.set('one',{'mode':'apikey','api_key':'secret'});original=path.read_bytes();store.set('one',store.get('one'));self.assertEqual(path.read_bytes(),original)
            self.assertEqual(CredentialsStore(path).get('one')['api_key'],'secret');store.set('one',{'mode':'none'});self.assertEqual(store.get('one'),{'mode':'none'})
        for value in ({'mode':'apikey'},{'mode':'digest','username':'user'},{'mode':'apikey','api_key':'line\nsecret'}):
            with self.assertRaises(ValueError):validate_credentials(value)

class RegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_credentials_retained_on_rename_cleared_on_address_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json';path.write_text(json.dumps({'printers':[]}));manager=PrinterManager(ConfigStore(path),factory=FakeMonitor)
            entry=await manager.add('192.168.254.250','Octo',protocol='octoprint',credentials={'mode':'apikey','api_key':'secret'})
            self.assertEqual(entry['api_port'],80);self.assertNotIn('secret',json.dumps(manager.snapshot()));self.assertNotIn('secret',manager.config.path.read_text())
            await manager.rename(entry['id'],'New');self.assertEqual(manager.credentials.get(entry['id'])['api_key'],'secret')
            await manager.update(entry['id'],api_port=5000);self.assertEqual(manager.credentials.get(entry['id']),{'mode':'none'})
            await manager.update(entry['id'],credentials={'mode':'digest','username':'user','password':'secret'},protocol='prusalink');self.assertEqual(manager.get(entry['id']).protocol,'prusalink')
            await manager.remove(entry['id']);self.assertEqual(manager.credentials.entries,{})
    async def test_secret_write_failure_keeps_registry_and_live_monitor(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json';path.write_text(json.dumps({'printers':[]}));manager=PrinterManager(ConfigStore(path),factory=FakeMonitor)
            entry=await manager.add('192.168.254.250','Prusa',protocol='prusalink',credentials={'mode':'apikey','api_key':'secret'});monitor=manager.get(entry['id'])
            with patch('k1monitor.credentials.atomic_json',side_effect=OSError('mock access denied')):
                with self.assertRaises(OSError):await manager.remove(entry['id'])
                with self.assertRaises(OSError):await manager.update(entry['id'],ip='192.168.254.251')
            self.assertIs(manager.get(entry['id']),monitor);self.assertEqual(manager.entry(entry['id'])['ip'],'192.168.254.250')

class APITests(unittest.TestCase):
    def test_credentials_never_echo_in_api_or_validation_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json';path.write_text(json.dumps({'printers':[]}));manager=PrinterManager(ConfigStore(path),factory=FakeMonitor);app=create_app(manager)
            # No lifespan: adding a mock address does not open network sessions.
            client=TestClient(app,base_url='http://127.0.0.1:8765');headers={'X-K1-Token':app.state.csrf_token}
            response=client.post('/api/v1/printers',json={'ip':'192.168.254.250','name':'Octo','protocol':'octoprint','credentials':{'mode':'apikey','api_key':'private-secret'}},headers=headers)
            self.assertEqual(response.status_code,201);self.assertNotIn('private-secret',response.text);self.assertNotIn('private-secret',client.get('/api/v1/status').text)
            invalid=client.post('/api/v1/printers',json={'ip':'private-secret','name':'','credentials':{'mode':'invalid-private-secret','password':'private-secret'}},headers=headers)
            self.assertEqual(invalid.status_code,422);self.assertNotIn('private-secret',invalid.text)
