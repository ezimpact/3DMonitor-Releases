import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.remote import COOKIE, PAIR_SECONDS, SESSION_SECONDS, RemoteAccess
from k1monitor.server import create_app
from test_monitor import FakeMonitor

HOST = 'pc.tail12345.ts.net'
IDENTITY = 'owner@example.com'
PROXY = {'Tailscale-User-Login': IDENTITY}


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.path = Path(self.temp.name) / 'remote.json'
        self.access = RemoteAccess(self.path, clock=lambda: self.now)
        self.access.configure(HOST)

    def test_one_time_code_and_hashed_session_survive_restart(self):
        code = self.access.pair_code()['code']
        token = self.access.pair(code, 'Телефон', IDENTITY)
        self.assertTrue(self.access.authorize(token, IDENTITY))
        self.assertFalse(self.access.authorize(token, 'another@example.com'))
        with self.assertRaises(ValueError): self.access.pair(code, 'Reuse', IDENTITY)
        saved = self.path.read_text()
        self.assertNotIn(token, saved)
        self.assertNotIn('code', json.loads(saved))
        restored = RemoteAccess(self.path, clock=lambda: self.now)
        self.assertTrue(restored.authorize(token, IDENTITY))
        self.assertIsNone(restored.status()['code'])
        self.assertNotIn('hash', restored.status()['devices'][0])

    def test_pairing_expires_and_five_failed_attempts_require_new_code(self):
        code = self.access.pair_code()['code']
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            with self.assertRaises(ValueError): self.access.pair(wrong, 'Phone', IDENTITY)
        with self.assertRaises(ValueError): self.access.pair(code, 'Phone', IDENTITY)
        code = self.access.pair_code()['code']
        self.now += PAIR_SECONDS
        with self.assertRaises(ValueError): self.access.pair(code, 'Phone', IDENTITY)

    def test_expiry_revocation_and_new_tailnet_invalidate_sessions(self):
        token = self.access.pair(self.access.pair_code()['code'], 'Phone', IDENTITY)
        self.now += SESSION_SECONDS
        self.assertFalse(self.access.authorize(token, IDENTITY))
        token = self.access.pair(self.access.pair_code()['code'], 'Phone 2', IDENTITY)
        self.access.revoke(self.access.status()['devices'][0]['id'])
        self.assertFalse(self.access.authorize(token, IDENTITY))
        token = self.access.pair(self.access.pair_code()['code'], 'Phone 3', IDENTITY)
        self.access.configure('new.tail23456.ts.net')
        self.assertFalse(self.access.authorize(token, IDENTITY))


class RecordingMonitor(FakeMonitor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []

    async def control(self, *args):
        self.calls.append(args)
        return {'phase': 'confirmed'}


class RemoteServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'config.json'
        path.write_text(json.dumps({'printer_ip':'192.168.4.104'}))
        self.manager = PrinterManager(ConfigStore(path), factory=RecordingMonitor)
        self.access = RemoteAccess(Path(self.temp.name) / 'remote.json')
        self.access.configure(HOST)
        self.app = create_app(self.manager, self.access)
        self.csrf = {'X-K1-Token': self.app.state.csrf_token}

    def client(self, peer='127.0.0.1'):
        return TestClient(self.app, base_url='https://' + HOST, headers=PROXY, client=(peer, 50000))

    def pair(self, client):
        code = self.access.pair_code()['code']
        response = client.post('/api/v1/auth/pair', json={'code':code,'name':'Android'}, headers=self.csrf)
        self.assertEqual(response.status_code, 200)
        return response

    def test_unauthorized_remote_cannot_read_or_control_printers(self):
        with self.client() as client:
            self.assertIn('pair-form', client.get('/').text)
            self.assertNotIn('printer-content', client.get('/').text)
            self.assertEqual(client.get('/api/v1/status').status_code, 401)
            self.assertEqual(client.get('/health').status_code, 401)
            self.assertEqual(client.get('/openapi.json').status_code, 401)
            body={'action':'stop','request_id':'test-12345678','expected_session':1,'expected_file':'/usr/data/job.gcode'}
            self.assertEqual(client.post('/api/v1/printers/k1max/control',json=body,headers=self.csrf).status_code,401)
            self.assertEqual(self.manager.get('k1max').calls, [])
            self.assertEqual(client.get('/api/v1/remote/status').status_code,403)

    def test_proxy_identity_and_local_peer_are_required(self):
        with self.client(peer='192.168.4.50') as client:
            self.assertEqual(client.get('/').status_code, 403)
        with self.client() as client:
            self.assertEqual(client.get('/',headers={'Tailscale-User-Login':''}).status_code,403)
            self.assertEqual(client.get('/',headers={'Host':'foreign.ts.net'}).status_code,403)
            self.assertEqual(client.get('/',headers={'Host':HOST+':1234'}).status_code,403)
            self.pair(client)
            self.assertEqual(client.get('/api/v1/status',headers={'Tailscale-User-Login':'other@example.com'}).status_code,401)

    def test_pairing_requires_csrf_and_same_origin_then_issues_secure_cookie(self):
        with self.client() as client:
            code = self.access.pair_code()['code']
            body = {'code':code,'name':'Android'}
            self.assertEqual(client.post('/api/v1/auth/pair',json=body).status_code,403)
            self.assertEqual(client.post('/api/v1/auth/pair',json=body,headers={**self.csrf,'Origin':'https://evil.example'}).status_code,403)
            response = client.post('/api/v1/auth/pair',json=body,headers={**self.csrf,'Origin':'https://'+HOST})
            self.assertEqual(response.status_code,200)
            cookie=response.headers['set-cookie']
            for flag in ['HttpOnly','Secure','SameSite=lax','Path=/','expires=','Max-Age='+str(SESSION_SECONDS)]:
                self.assertIn(flag,cookie)
            self.assertEqual(client.get('/api/v1/status').status_code,200)
            self.assertIn('content="remote"',client.get('/').text)
            self.assertEqual(client.post('/api/v1/remote/pair-code',headers=self.csrf).status_code,403)

    def test_external_link_without_initial_cookie_restores_existing_session_on_same_origin_check(self):
        with self.client() as client:
            self.pair(client)
            saved=self.access.path.read_bytes()
            expires=self.access.devices[0]['expires']
            # Model an old Strict cookie withheld on the external link, then
            # sent by the pairing page's same-origin fetch.
            landing=client.get('/',headers={'Cookie':''})
            self.assertIn('id="pair-form" hidden',landing.text)
            self.assertNotIn('set-cookie',landing.headers)
            response=client.get('/api/v1/auth/session')
            self.assertEqual(response.json(),{'paired':True})
            for flag in ['SameSite=lax','Secure','HttpOnly','expires=','Path=/']:
                self.assertIn(flag,response.headers['set-cookie'])
            self.assertEqual(self.access.devices[0]['expires'],expires)
            self.assertEqual(self.access.path.read_bytes(),saved)
            self.assertIn('printer-content',client.get('/').text)

    def test_session_check_survives_server_restart_and_still_checks_identity_and_revocation(self):
        with self.client() as client:
            self.pair(client)
            cookie=client.cookies.get(COOKIE)
        self.access=RemoteAccess(self.access.path)
        self.app=create_app(self.manager,self.access)
        with self.client() as client:
            client.cookies.set(COOKIE,cookie,domain=HOST,path='/')
            self.assertEqual(client.get('/api/v1/auth/session').json(),{'paired':True})
            wrong=client.get('/api/v1/auth/session',headers={'Tailscale-User-Login':'other@example.com'})
            self.assertEqual(wrong.json(),{'paired':False})
            self.assertNotIn('set-cookie',wrong.headers)
            self.access.revoke(self.access.devices[0]['id'])
            revoked=client.get('/api/v1/auth/session')
            self.assertEqual(revoked.json(),{'paired':False})
            self.assertNotIn('set-cookie',revoked.headers)

    def test_lax_link_cookie_does_not_allow_foreign_origin_print_commands(self):
        with self.client() as client:
            self.pair(client)
            body={'action':'stop','request_id':'test-12345678','expected_session':1,'expected_file':'/usr/data/job.gcode'}
            response=client.post('/api/v1/printers/k1max/control',json=body,
                headers={**self.csrf,'Origin':'https://external.example'})
            self.assertEqual(response.status_code,403)
            self.assertEqual(self.manager.get('k1max').calls,[])

    def test_paired_control_keeps_exact_job_guard_and_camera_is_not_advertised_as_relay(self):
        with self.client() as client:
            self.pair(client)
            body={'action':'pause','request_id':'test-12345678','expected_session':1,'expected_file':'/usr/data/Шпуля.gcode'}
            self.assertEqual(client.post('/api/v1/printers/k1max/control',json=body).status_code,403)
            self.assertEqual(client.post('/api/v1/printers/k1max/control',json=body,headers={**self.csrf,'Origin':'https://'+HOST}).status_code,202)
            self.assertEqual(self.manager.get('k1max').calls,[('pause','test-12345678',1,'/usr/data/Шпуля.gcode')])
            self.assertEqual(client.post('/api/v1/printers/k1max/camera/offer',json={'sdp':'test'},headers=self.csrf).status_code,409)
            self.assertEqual(client.get('/api/v1/printers/k1max/camera/mjpeg').status_code,409)

    def test_websocket_auth_origin_and_revocation_on_active_connection(self):
        url='wss://'+HOST+'/api/v1/events'
        with self.client() as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(url,headers={'Origin':'https://'+HOST}): pass
            self.pair(client)
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(url,headers={'Origin':'https://evil.example'}): pass
            with client.websocket_connect(url,headers={'Origin':'https://'+HOST}) as ws:
                self.assertEqual(ws.receive_json()['printers'][0]['status'],'printing')
                self.access.revoke(self.access.status()['devices'][0]['id'])
                ws.send_text('refresh')
                with self.assertRaises(WebSocketDisconnect): ws.receive_json()
            self.assertEqual(client.get('/api/v1/status').status_code,401)

    def test_local_dashboard_and_logout_work_with_remote_enabled(self):
        with self.client() as client:
            self.pair(client)
            self.assertEqual(client.get('http://127.0.0.1:8765/api/v1/status').status_code,200)
            self.assertIn('content="local"',client.get('http://127.0.0.1:8765/').text)
            self.assertEqual(client.post('/api/v1/auth/logout',headers=self.csrf).status_code,200)
            self.assertEqual(client.get('/api/v1/status').status_code,401)
            self.assertIsNone(client.cookies.get(COOKIE))
