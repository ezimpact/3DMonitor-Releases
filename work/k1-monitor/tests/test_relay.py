import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.relay import CameraRelay, MAX_FRAME
from k1monitor.remote import RemoteAccess, COOKIE
from k1monitor.server import create_app
from test_monitor import FakeMonitor

JPEG = b'\xff\xd8' + b'test-frame-bytes' + b'\xff\xd9'


class Browser:
    def __init__(self): self.running=False; self.starts=0; self.stops=0
    def start(self, url):
        if not self.running: self.starts+=1; self.running=True
    def stop(self):
        if self.running: self.stops+=1; self.running=False


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'config.json'
        path.write_text(json.dumps({'printers':[{'id':'p'+str(i),'name':'Test','ip':'192.168.4.'+str(100+i)} for i in range(7)]}))
        self.manager=PrinterManager(ConfigStore(path),factory=FakeMonitor)
        self.now=100;self.browser=Browser()
        self.relay=CameraRelay(self.manager,self.browser,'http://127.0.0.1:8766/',clock=lambda:self.now)

    async def test_shared_camera_has_bounded_latest_frame_queues_and_stale_generation_is_rejected(self):
        one=await self.relay.subscribe('p0');two=await self.relay.subscribe('p0')
        generation=self.relay.sources['p0'].generation
        self.relay.frame('p0',generation,JPEG)
        self.now+=.2
        newer=JPEG[:-2]+b'new'+JPEG[-2:]
        self.relay.frame('p0',generation,newer)
        self.assertEqual(one.qsize(),1);self.assertEqual(two.qsize(),1)
        self.assertEqual(await one.get(),newer);self.assertEqual(await two.get(),newer)
        self.assertEqual(self.browser.starts,1)
        self.relay.fail('p0','lost video')
        await self.relay.subscribe('p0')
        self.assertNotEqual(generation,self.relay.sources['p0'].generation)
        with self.assertRaises(KeyError): self.relay.frame('p0',generation,JPEG)

    async def test_last_viewer_releases_camera_after_grace_without_stopping_other_camera(self):
        one=await self.relay.subscribe('p0');two=await self.relay.subscribe('p1')
        self.relay.unsubscribe('p0',one);self.now+=3.1;self.relay.sweep()
        self.assertNotIn('p0',self.relay.sources);self.assertIn('p1',self.relay.sources)
        self.assertTrue(self.browser.running)
        self.relay.unsubscribe('p1',two);self.now+=3.1;self.relay.sweep()
        self.assertFalse(self.browser.running)

    async def test_missing_first_frame_freeze_and_removal_are_reported(self):
        queue=await self.relay.subscribe('p0')
        self.now+=26;self.relay.sweep()
        self.assertEqual((await queue.get())['type'],'error')
        queue=await self.relay.subscribe('p0')
        self.relay.frame('p0',self.relay.sources['p0'].generation,JPEG)
        await queue.get();self.now+=13;self.relay.sweep()
        self.assertIn('кадры',(await queue.get())['message'])
        queue=await self.relay.subscribe('p1');del self.manager.monitors['p1'];self.relay.sweep()
        self.assertIn('удалён',(await queue.get())['message'])

    async def test_camera_limit_bad_jpeg_and_size_limit_do_not_grow_buffers(self):
        for i in range(6): await self.relay.subscribe('p'+str(i))
        with self.assertRaises(ValueError): await self.relay.subscribe('p6')
        generation=self.relay.sources['p0'].generation
        for data in [b'bad',b'\xff\xd8'+b'x'*MAX_FRAME+b'\xff\xd9']:
            with self.assertRaises(ValueError): self.relay.frame('p0',generation,data)
        self.assertIsNone(self.relay.sources['p0'].frame)

    async def test_changed_ip_rejects_old_frames_and_only_restarts_that_camera(self):
        queue = await self.relay.subscribe('p0'); await self.relay.subscribe('p1')
        old_generation = self.relay.sources['p0'].generation
        retained = self.relay.sources['p1']
        await self.manager.update('p0', ip='192.168.4.120')
        with self.assertRaises(KeyError): self.relay.frame('p0', old_generation, JPEG)
        self.relay.sweep(); self.assertIn('IP', (await queue.get())['message'])
        await self.relay.subscribe('p0')
        self.assertNotEqual(self.relay.sources['p0'].generation, old_generation)
        self.assertEqual(self.relay.sources['p0'].ip, '192.168.4.120')
        self.assertIs(self.relay.sources['p1'], retained)


class RelayApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'config.json';path.write_text(json.dumps({'printer_ip':'192.168.4.104'}))
        self.manager=PrinterManager(ConfigStore(path),factory=FakeMonitor)
        self.browser=Browser();self.relay=CameraRelay(self.manager,self.browser,'http://127.0.0.1:8766/')
        self.remote=RemoteAccess(Path(self.temp.name)/'remote.json');self.remote.configure('pc.tail12345.ts.net')
        self.app=create_app(self.manager,self.remote,self.relay)

    def test_worker_frames_require_secret_active_generation_and_body_limit(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8766',client=('127.0.0.1',50000)) as client:
            self.assertEqual(client.post('/internal/camera-relay/k1max/frame',content=JPEG).status_code,403)
            with client.websocket_connect('ws://127.0.0.1:8766/api/v1/printers/k1max/camera/relay') as viewer:
                headers={'X-K1-Worker':self.relay.key,'X-K1-Generation':self.relay.sources['k1max'].generation}
                self.assertEqual(client.post('/internal/camera-relay/k1max/frame',content=JPEG,headers=headers).status_code,200)
                self.assertEqual(viewer.receive_bytes(),JPEG)
                self.assertEqual(client.post('/internal/camera-relay/k1max/frame',content=JPEG,headers={**headers,'X-K1-Generation':'old'}).status_code,404)
                self.assertEqual(client.post('/internal/camera-relay/k1max/frame',content=b'x'*(MAX_FRAME+1),headers=headers).status_code,413)

    def test_unauthenticated_viewer_and_remote_worker_access_are_rejected(self):
        with TestClient(self.app,base_url='https://pc.tail12345.ts.net',headers={'Tailscale-User-Login':'owner'},client=('127.0.0.1',50000)) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('wss://pc.tail12345.ts.net/api/v1/printers/k1max/camera/relay'): pass
            self.assertEqual(client.post('/internal/camera-relay/k1max/frame',content=JPEG,headers={'X-K1-Worker':self.relay.key}).status_code,403)
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('wss://pc.tail12345.ts.net/internal/camera-relay/worker',subprotocols=['k1-worker.'+self.relay.key]): pass
            self.assertFalse(self.browser.running)

    def test_demo_ui_and_api_are_removed(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8766',client=('127.0.0.1',50000)) as client:
            self.assertNotIn('demo-open',client.get('/').text)
            self.assertEqual(client.get('/api/demo/v1/status').status_code,404)
            self.assertEqual(client.get('/demo',follow_redirects=False).headers['location'],'/')
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('ws://127.0.0.1:8766/api/demo/v1/printers/demo-1/camera/relay'): pass

    def test_worker_control_and_active_viewer_revocation(self):
        token=self.remote.pair(self.remote.pair_code()['code'],'test','owner')
        with TestClient(self.app,base_url='http://127.0.0.1:8766',client=('127.0.0.1',50000)) as client:
            remote_headers={'Host':'pc.tail12345.ts.net','Tailscale-User-Login':'owner','Cookie':COOKIE+'='+token,'Origin':'https://pc.tail12345.ts.net'}
            with client.websocket_connect('ws://127.0.0.1:8766/api/v1/printers/k1max/camera/relay',headers=remote_headers) as viewer:
                with client.websocket_connect('ws://127.0.0.1:8766/internal/camera-relay/worker',subprotocols=['k1-worker.'+self.relay.key]) as worker:
                    config=worker.receive_json();self.assertEqual(config['sources'][0]['id'],'k1max')
                    self.remote.revoke(self.remote.status()['devices'][0]['id'])
                    with self.assertRaises(WebSocketDisconnect): viewer.receive_json()
                    worker.send_json({'type':'heartbeat'})
