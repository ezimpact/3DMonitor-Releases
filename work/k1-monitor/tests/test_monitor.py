import asyncio
import json
import unittest
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

from k1monitor.client import PrinterMonitor
from k1monitor.server import create_app
from k1monitor.telemetry import Telemetry
from k1monitor.config import ConfigStore, local_ip, scan_network
from k1monitor.manager import PrinterManager


class TelemetryTests(unittest.TestCase):
    def make_state(self):
        state = Telemetry('192.168.4.104', 'configured-version')
        state.begin_session()
        return state

    def test_partial_updates_numbers_and_zero_progress(self):
        state = self.make_state()
        state.ingest({'state':1,'nozzleTemp':'250.000000','targetNozzleTemp':250,'printProgress':0,'dProgress':30,'err':{'errcode':0}}, now=100)
        state.ingest({'bedTemp0':'79.000000'}, now=101)
        snapshot = state.snapshot(now=102)
        self.assertEqual(snapshot['temperatures']['nozzle'], 250)
        self.assertEqual(snapshot['temperatures']['bed'], 79)
        self.assertEqual(snapshot['print']['progress_percent'], 0)
        self.assertEqual(snapshot['status'], 'printing')
        self.assertTrue(snapshot['connection']['online'])

    def test_invalid_values_are_not_invented_as_zero(self):
        state = self.make_state()
        state.ingest({'nozzleTemp':'NaN','bedTemp0':'bad','printProgress':101,'layer':True}, now=100)
        snapshot = state.snapshot(now=101)
        self.assertIsNone(snapshot['temperatures']['nozzle'])
        self.assertIsNone(snapshot['temperatures']['bed'])
        self.assertIsNone(snapshot['print']['progress_percent'])
        self.assertIsNone(snapshot['print']['layer'])

    def test_stale_and_disconnect_never_appear_live(self):
        state = self.make_state()
        state.ingest({'state':1,'nozzleTemp':'250'}, now=100)
        self.assertFalse(state.snapshot(now=116)['connection']['online'])
        self.assertEqual(state.snapshot(now=116)['status'], 'unknown')
        state.disconnected('test disconnect')
        self.assertFalse(state.snapshot(now=101)['connection']['online'])
        self.assertEqual(state.snapshot(now=101)['temperatures']['nozzle'], 250)

    def test_new_session_does_not_mix_old_job_and_new_partial_update(self):
        state = self.make_state()
        state.ingest({'state':1,'printProgress':70,'printFileName':'previous.gcode'}, now=100)
        state.begin_session()
        self.assertFalse(state.snapshot(now=101)['connection']['online'])
        state.ingest({'nozzleTemp':'25'}, now=102)
        snapshot = state.snapshot(now=103)
        self.assertIsNone(snapshot['print']['file'])
        self.assertIsNone(snapshot['print']['progress_percent'])
        self.assertEqual(snapshot['status'], 'unknown')

    def test_heartbeat_and_unknown_frames_are_not_telemetry(self):
        state = self.make_state()
        for payload in [{'ModeCode':'heart_beat'}, {'unknown':1}, [], 'ok']:
            self.assertFalse(state.ingest(payload, now=100))
        self.assertFalse(state.snapshot(now=101)['connection']['online'])

    def test_heartbeat_keeps_link_online_without_falsifying_freshness(self):
        state=self.make_state()
        state.heartbeat(now=100)
        self.assertFalse(state.snapshot(now=101)['connection']['online'])
        state.ingest({'state':0},now=101)
        state.heartbeat(now=120)
        result=state.snapshot(now=121)
        self.assertTrue(result['connection']['online'])
        self.assertTrue(result['connection']['stale'])
        self.assertEqual(result['connection']['age_seconds'],20)
        self.assertEqual(result['status'],'idle')

    def test_firmware_is_confirmed_from_the_printer(self):
        state = self.make_state()
        state.ingest({'modelVersion':'DWIN hw ver:CR4SU200382C13;DWIN sw ver:V1.0.0.25.20251015S;'}, now=100)
        self.assertEqual(state.snapshot(now=101)['printer']['firmware'], '1.0.0.25.20251015S')
        self.assertTrue(state.snapshot(now=101)['printer']['firmware_confirmed'])


class FakeMonitor(PrinterMonitor):
    def __init__(self, host='192.168.4.104', firmware='test', **kwargs):
        super().__init__(host, firmware, **kwargs)
        self.started = False
        self.stopped = False
    async def start(self):
        self.started = True
        self.state.begin_session()
        self.state.ingest({'state':1,'nozzleTemp':'250','printProgress':25})
    async def stop(self):
        self.stopped = True
class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config.json'
        self.path.write_text(json.dumps({'printer_ip':'192.168.4.104'}))
        self.manager = PrinterManager(ConfigStore(self.path), factory=FakeMonitor)
        self.app = create_app(self.manager)
        self.headers = {'X-K1-Token': self.app.state.csrf_token}

    def test_dashboard_api_and_lifecycle(self):
        monitor = self.manager.get('k1max')
        with TestClient(self.app, base_url='http://127.0.0.1:8765') as client:
            self.assertTrue(monitor.started)
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.get('/static/ui.js').status_code, 200)
            self.assertEqual(client.get('/static/ui.css').status_code, 200)
            response = client.get('/api/v1/status')
            self.assertEqual(response.json()['printers'][0]['print']['progress_percent'], 25)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertNotEqual(client.post('/api/v1/control', json={'command':'pause'}).status_code, 200)
        self.assertTrue(monitor.stopped)

    def test_foreign_host_is_rejected(self):
        with TestClient(self.app, base_url='http://127.0.0.1:8765') as client:
            self.assertEqual(client.get('/api/v1/status', headers={'host':'foreign.example'}).status_code, 403)

    def test_websocket_updates_and_origin_check(self):
        with TestClient(self.app, base_url='http://127.0.0.1:8765') as client:
            with client.websocket_connect('ws://127.0.0.1:8765/api/v1/events', headers={'origin':'http://127.0.0.1:8765'}) as ws:
                self.assertEqual(ws.receive_json()['printers'][0]['status'], 'printing')
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('ws://127.0.0.1:8765/api/v1/events', headers={'origin':'https://foreign.example'}):
                    pass

    def test_mutations_require_token_and_reject_foreign_origin(self):
        with TestClient(self.app, base_url='http://127.0.0.1:8765') as client:
            for headers in [{}, {**self.headers, 'Origin':'https://foreign.example'}]:
                self.assertEqual(client.post('/api/v1/printers',json={'ip':'192.168.4.105','name':'Test'},headers=headers).status_code,403)
            self.assertEqual(len(self.manager.config.printers), 1)

    def test_add_rename_remove_and_reload_persistence(self):
        with TestClient(self.app, base_url='http://127.0.0.1:8765') as client:
            add = client.post('/api/v1/printers', json={'ip':'192.168.4.105','name':'K1C'}, headers=self.headers)
            self.assertEqual(add.status_code,201)
            printer_id = add.json()['id']
            self.assertEqual(client.post('/api/v1/printers', json={'ip':'192.168.4.105','name':'Duplicate'}, headers=self.headers).status_code,409)
            rename = client.patch('/api/v1/printers/'+printer_id, json={'name':'Мастерская <script>'},headers=self.headers)
            self.assertEqual(rename.status_code,200)
            loaded = ConfigStore(self.path)
            self.assertEqual(loaded.printers[1]['name'],'Мастерская <script>')
            self.assertEqual(client.delete('/api/v1/printers/'+printer_id,headers=self.headers).status_code,200)
            self.assertEqual(len(ConfigStore(self.path).printers),1)

    def test_invalid_ip_unknown_printer_and_unavailable_control(self):
        with TestClient(self.app, base_url='http://127.0.0.1:8765') as client:
            for ip in ['127.0.0.1','8.8.8.8','printer.local','http://192.168.4.104','169.254.1.1']:
                self.assertEqual(client.post('/api/v1/printers',json={'ip':ip,'name':'bad'},headers=self.headers).status_code,409)
            self.assertEqual(client.patch('/api/v1/printers/missing',json={'name':'Test'},headers=self.headers).status_code,404)
            body={'action':'stop','request_id':'test-12345678','expected_session':1,'expected_file':None}
            self.assertEqual(client.post('/api/v1/printers/k1max/control',json=body,headers=self.headers).status_code,409)
            self.assertEqual(client.post('/api/v1/scan',json={'network':'192.168.0.0/16'},headers=self.headers).status_code,409)


class ConfigTests(unittest.TestCase):
    def test_legacy_config_is_backed_up_and_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            original={'printer_ip':'192.168.4.104','firmware':'known'}
            path.write_text(json.dumps(original))
            store=ConfigStore(path)
            self.assertEqual(store.printers[0]['firmware'],'known')
            self.assertEqual(json.loads(path.with_suffix('.v0.1.backup.json').read_text()),original)
            self.assertEqual(json.loads(path.read_text())['schema_version'],2)

    def test_scan_limits_and_lan_validation(self):
        self.assertEqual(str(scan_network('192.168.4.104/24')),'192.168.4.0/24')
        for cidr in ['0.0.0.0/0','192.168.0.0/16','127.0.0.0/24','192.168.4.104/32']:
            with self.assertRaises(ValueError): scan_network(cidr)
        self.assertEqual(local_ip(' 192.168.4.104 '),'192.168.4.104')


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_controls_are_sent_once_and_confirmed_by_new_telemetry(self):
        commands = []
        state = {'state':1,'printFileName':'test.gcode','printProgress':42}

        async def handler(ws):
            async for raw in ws:
                message=json.loads(raw)
                if message['method']=='set':
                    commands.append(message)
                    params=message['params']
                    state['state']=4 if params.get('stop')==1 else 5 if params['pause']==1 else 1
                await ws.send(json.dumps(state))

        async with serve(handler,'127.0.0.1',0,subprotocols=['wsslicer']) as server:
            monitor=PrinterMonitor('127.0.0.1','test',port=server.sockets[0].getsockname()[1],poll_seconds=.1)
            await monitor.start()
            try:
                async with asyncio.timeout(3):
                    while not monitor.snapshot()['connection']['online']: await asyncio.sleep(.02)
                with self.assertRaises(ValueError):
                    await monitor.control('stop','wrong-job',1,'different.gcode')
                for index,action in enumerate(['pause','resume','stop']):
                    request_id='command-'+str(index)
                    result=await monitor.control(action,request_id,1,'test.gcode')
                    async with asyncio.timeout(3):
                        while monitor.snapshot()['command']['phase']=='pending': await asyncio.sleep(.02)
                    self.assertEqual(monitor.snapshot()['command']['phase'],'confirmed')
                    self.assertEqual((await monitor.control(action,request_id,1,'test.gcode'))['id'],result['id'])
                self.assertEqual(commands,[{'method':'set','params':{'pause':1}},{'method':'set','params':{'pause':0}},{'method':'set','params':{'stop':1}}])
                self.assertEqual(monitor.snapshot()['status'],'stopped')
                with self.assertRaises(ValueError): await monitor.control('pause','inactive-command',1,'test.gcode')
            finally: await monitor.stop()

    async def test_temperature_updates_do_not_confirm_and_pending_commands_are_blocked(self):
        class Socket:
            async def send(self, message): pass
        monitor=PrinterMonitor('192.168.4.104','test')
        monitor.state.begin_session()
        monitor.state.ingest({'state':1,'printFileName':'job.gcode'})
        monitor._ws=Socket()
        await monitor.control('pause','command-123',1,'job.gcode')
        monitor.state.ingest({'nozzleTemp':'250'})
        self.assertEqual(monitor.snapshot()['command']['phase'],'pending')
        with self.assertRaises(ValueError): await monitor.control('stop','command-456',1,'job.gcode')
        monitor._command['_sent']-=13
        self.assertEqual(monitor.snapshot()['command']['phase'],'unconfirmed')
        monitor.state.disconnected()
        with self.assertRaises(ValueError): await monitor.control('stop','command-789',1,'job.gcode')

    async def test_real_socket_heartbeat_reconnect_and_read_only_requests(self):
        sessions = []
        sent_by_client = []
        heartbeat_acked = asyncio.Event()

        async def handler(ws):
            sessions.append(ws)
            index = len(sessions)
            request = await ws.recv()
            sent_by_client.append(request)
            if index == 1:
                await ws.send('not json')
                await ws.send(json.dumps({'state':1,'nozzleTemp':'250','printProgress':0}))
                await ws.send(json.dumps({'bedTemp0':'79'}))
                await ws.send(json.dumps({'ModeCode':'heart_beat'}))
            else:
                await ws.send(json.dumps({'nozzleTemp':'248'}))
            async for message in ws:
                sent_by_client.append(message)
                if message == 'ok':
                    heartbeat_acked.set()

        async with serve(handler, '127.0.0.1', 0, subprotocols=['wsslicer']) as server:
            port = server.sockets[0].getsockname()[1]
            monitor = PrinterMonitor('127.0.0.1','test',port=port,poll_seconds=0.1,stale_after=1,retry_min=0.05)
            await monitor.start()
            try:
                await asyncio.wait_for(heartbeat_acked.wait(), timeout=3)
                self.assertEqual(monitor.snapshot()['temperatures']['bed'], 79)
                self.assertEqual(monitor.snapshot()['print']['progress_percent'], 0)
                await sessions[0].close()
                async with asyncio.timeout(3):
                    while len(sessions) < 2 or monitor.snapshot()['temperatures']['nozzle'] != 248:
                        await asyncio.sleep(0.02)
                self.assertIsNone(monitor.snapshot()['print']['progress_percent'])
                for message in sent_by_client:
                    if message != 'ok':
                        parsed=json.loads(message)
                        self.assertEqual(parsed['method'],'get')
                        self.assertIn(parsed['params'],[{'ReqPrinterPara':1},{'reqGcodeFile':1}])
            finally:
                await monitor.stop()
                self.assertFalse(monitor.snapshot()['connection']['online'])


if __name__ == '__main__':
    unittest.main()
