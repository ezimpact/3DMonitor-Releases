import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app
from test_monitor import FakeMonitor

addon = Path(__file__).resolve().parents[1] / 'companion' / 'camera-saver-addon'
if not addon.exists(): addon = Path(__file__).resolve().parents[2] / 'camera-saver-addon'
sys.path.insert(0, str(addon))
from shared_registry import RegistryReader


class SharedRegistryTests(unittest.TestCase):
    def test_beta_protocols_without_camera_are_skipped_and_credentials_are_never_shared(self):
        for protocol,ip in [('octoprint','192.168.254.250'),('prusalink','192.168.254.251')]:
            item=self.store.add(ip,protocol,protocol=protocol)
            self.assertIsNone(self.reader.get(item['id']))
            self.store.update(item['id'],camera_path='/webcam/?action=stream')
            camera=self.reader.get(item['id']);self.assertEqual(camera['protocol'],protocol)
            self.assertEqual(set(camera),{'id','name','ip','protocol','camera_path'})
    def test_moonraker_without_camera_is_skipped_and_optional_camera_stays_on_printer(self):
        initial=self.saver.read_bytes();item=self.store.add('192.168.4.102','V400',protocol='moonraker')
        self.assertIsNone(self.reader.get(item['id']))
        self.store.update(item['id'],camera_path='http://192.168.4.102:8080/?action=stream')
        camera=self.reader.get(item['id']);self.assertEqual(camera['protocol'],'moonraker');self.assertEqual(camera['camera_path'],'http://192.168.4.102:8080/?action=stream')
        self.assertEqual(self.saver.read_bytes(),initial)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'monitor.json'; self.registry = self.root / 'printers.json'
        self.original = {'printers': [{'id': 'one', 'name': 'K1 Max', 'ip': '192.168.4.104'}]}
        self.config.write_text(json.dumps(self.original))
        self.store = ConfigStore(self.config, self.registry)
        self.saver = self.root / 'saver.json'
        self.preferences = {'version': 2, 'wait_minutes': 7, 'registry_path': str(self.registry), 'disabled_printers': []}
        self.saver.write_text(json.dumps(self.preferences))
        self.reader = RegistryReader(self.saver)

    def test_master_migration_and_saver_reads_without_running_monitor(self):
        self.assertEqual(json.loads((self.root / 'config.pre-shared.backup.json').read_text()), self.original)
        saved = json.loads(self.config.read_text()); self.assertNotIn('printers', saved)
        self.assertEqual(saved['registry_path'], str(self.registry))
        self.assertEqual(self.reader.snapshot()['printers'][0]['id'], 'one')
        self.assertEqual(ConfigStore(self.config).printers, self.store.printers)

    def test_add_edit_delete_sync_without_writing_saver_settings(self):
        initial = self.saver.read_bytes()
        added = self.store.add('192.168.4.105', 'K1C')
        self.assertEqual(len(self.reader.snapshot()['printers']), 2)
        self.store.update('one', name='Шпуля', ip='192.168.4.106')
        entry = self.reader.get('one'); self.assertEqual(entry, {'id': 'one', 'name': 'Шпуля', 'ip': '192.168.4.106'})
        self.store.remove(added['id']); self.assertIsNone(self.reader.get(added['id']))
        self.assertEqual(self.saver.read_bytes(), initial)

    def test_disabled_ids_survive_name_and_ip_changes_new_printers_enabled(self):
        self.preferences['disabled_printers'] = ['one']; self.saver.write_text(json.dumps(self.preferences))
        self.store.update('one', name='Переименован', ip='192.168.4.110')
        added = self.store.add('192.168.4.111', 'Новое устройство')
        self.assertEqual([p['id'] for p in self.reader.snapshot()['printers']], [added['id']])

    def test_invalid_or_temporarily_missing_registry_keeps_last_good_then_recovers(self):
        original = self.registry.read_bytes(); last = self.reader.snapshot()['printers']
        for broken in ['{', json.dumps({'printers': [{'id':'bad','name':'Bad','ip':'8.8.8.8'}]})]:
            self.registry.write_text(broken)
            result = self.reader.snapshot(); self.assertEqual(result['printers'], last); self.assertTrue(result['registry_error'])
        self.registry.unlink(); self.assertEqual(self.reader.snapshot()['printers'], last)
        self.registry.write_bytes(original); self.assertIsNone(self.reader.snapshot()['registry_error'])

    def test_existing_master_wins_over_stale_release_seed(self):
        self.store.update('one', name='Текущая база')
        other = self.root / 'old-release.json'; other.write_text(json.dumps({**self.original, 'registry_path': str(self.registry)}))
        self.assertEqual(ConfigStore(other).printers[0]['name'], 'Текущая база')

    def test_missing_pointer_fails_instead_of_erasing_registry(self):
        self.registry.unlink()
        with self.assertRaises(ValueError): ConfigStore(self.config)
        self.assertFalse(self.registry.exists())

    def test_failed_validation_or_disk_write_preserves_complete_previous_records(self):
        self.store.add('192.168.4.105', 'K1C'); before = self.registry.read_bytes()
        for value in ['192.168.4.105', '8.8.8.8', 'printer.local']:
            with self.assertRaises(ValueError): self.store.update('one', name='Should not save', ip=value)
            self.assertEqual(self.registry.read_bytes(), before)
        with patch('k1monitor.config.os.replace', side_effect=PermissionError('busy')):
            with self.assertRaises(PermissionError): self.store.update('one', ip='192.168.4.106')
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual(self.store.printers[0]['ip'], '192.168.4.104')

    def test_edit_api_replaces_only_changed_connection_and_invalidates_old_session(self):
        self.store.add('192.168.4.105', 'K1C')
        manager = PrinterManager(self.store, factory=FakeMonitor); app = create_app(manager)
        old, other = list(manager.monitors.values())
        with TestClient(app, base_url='http://127.0.0.1:8765') as client:
            headers = {'X-K1-Token': app.state.csrf_token}
            old_session = old.state.connection_count
            result = client.patch('/api/v1/printers/one', json={'name':'Новый адрес','ip':'192.168.4.106'}, headers=headers)
            self.assertEqual(result.status_code,200); self.assertEqual(result.json()['id'], 'one')
            replacement = manager.get('one'); self.assertIsNot(replacement,old); self.assertTrue(old.stopped)
            self.assertEqual(replacement.state.connection_count,old_session+1)
            self.assertIs(list(manager.monitors.values())[1],other); self.assertFalse(other.stopped)
            self.assertEqual(self.reader.get('one')['ip'], '192.168.4.106')
            self.assertEqual(client.patch('/api/v1/printers/one',json={'ip':'192.168.4.105'},headers=headers).status_code,409)
            self.assertEqual(client.patch('/api/v1/printers/one',json={},headers=headers).status_code,422)
            self.assertIs(manager.get('one'),replacement)
            client.patch('/api/v1/printers/one',json={'name':'Only rename'},headers=headers)
            self.assertIs(manager.get('one'),replacement)
