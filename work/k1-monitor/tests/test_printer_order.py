import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.printer_order import PrinterOrder
from k1monitor.server import create_app
from k1monitor.remote import RemoteAccess
from k1monitor.activity import ActivityService
from test_monitor import FakeMonitor
from test_activity_notifications import sample
import test_status_history as bot_fixtures


class OrderStorageTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.entries=[{'id':pid,'name':pid,'ip':f'192.168.254.{i+10}'} for i,pid in enumerate(['last','first','middle'])]
        self.path=self.root/'config.json';self.path.write_text(json.dumps({'printers':self.entries}))
        self.manager=PrinterManager(ConfigStore(self.path),factory=FakeMonitor)
        self.client=TestClient(create_app(self.manager,csrf_token='fixture'),base_url='http://127.0.0.1')
    def test_order_persists_without_rewriting_registry_or_replacing_monitors(self):
        before=self.path.read_bytes();monitors=dict(self.manager.monitors)
        response=self.client.put('/api/v1/printer-order',json={'ids':['first','middle','last']},headers={'X-K1-Token':'fixture'})
        self.assertEqual(response.status_code,200);self.assertEqual(self.path.read_bytes(),before)
        restored=PrinterManager(ConfigStore(self.path),factory=FakeMonitor)
        self.assertEqual(restored.snapshot()['printer_order'],['first','middle','last']);self.assertEqual(self.manager.monitors,monitors)
    def test_duplicate_missing_unknown_and_stale_ids_rejected_without_writes(self):
        for ids in [['last','last','middle'],['last','first'],['last','first','unknown']]:
            self.assertEqual(self.client.put('/api/v1/printer-order',json={'ids':ids},headers={'X-K1-Token':'fixture'}).status_code,409)
        self.assertFalse(self.manager.order_store.path.exists())
    def test_order_update_requires_csrf_and_same_origin(self):
        body={'ids':['first','middle','last']}
        self.assertEqual(self.client.put('/api/v1/printer-order',json=body).status_code,403)
        self.assertEqual(self.client.put('/api/v1/printer-order',json=body,headers={'X-K1-Token':'fixture','Origin':'https://evil.example'}).status_code,403)
    def test_remote_browser_cannot_override_main_pc_order(self):
        remote=RemoteAccess(self.root/'remote.json');remote.configure('pc.tail12345.ts.net')
        client=TestClient(create_app(self.manager,remote=remote,csrf_token='fixture'),base_url='https://pc.tail12345.ts.net')
        response=client.put('/api/v1/printer-order',json={'ids':['first','middle','last']},headers={'X-K1-Token':'fixture','Tailscale-User-Login':'owner@example.com'})
        self.assertEqual(response.status_code,403);self.assertFalse(self.manager.order_store.path.exists())
    def test_added_removed_printers_normalize_without_rewriting_saved_order(self):
        store=self.manager.order_store;store.save(['first','middle','last'],self.entries);before=store.path.read_bytes()
        current=[self.entries[0],self.entries[1],{'id':'new'}]
        self.assertEqual(store.current(current),['first','last','new']);self.assertEqual(store.path.read_bytes(),before)
    def test_failed_persistence_keeps_previous_order(self):
        store=self.manager.order_store;store.save(['first','middle','last'],self.entries)
        with patch('k1monitor.printer_order.atomic_json',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):store.save(['last','middle','first'],self.entries)
        self.assertEqual(store.current(self.entries),['first','middle','last'])


class TelegramSidebarOrderTests(unittest.IsolatedAsyncioTestCase):
    setUp=bot_fixtures.BotStatusTests.setUp
    request=bot_fixtures.BotStatusTests.request
    async def test_status_delivers_first_sidebar_printer_before_last_registry_printer(self):
        items=[]
        for pid in ['last','first','middle']:
            item=sample('idle');item['printer']={'id':pid,'name':pid,'ip':'192.168.254.10'};item['camera']={'present':False};items.append(item)
        snapshot={'printers':items,'printer_order':['first','middle','last']}
        manager=type('Manager',(),{'snapshot':lambda _:snapshot})();ActivityService(manager,self.bot)
        await self.bot.reply_status(7,'fixture')
        self.assertEqual([body['text'].split(' — ')[0] for method,body in self.sent],['[1/3] first','[2/3] middle','[3/3] last'])
        self.assertEqual([item['printer']['id'] for item in items],['last','first','middle'])
