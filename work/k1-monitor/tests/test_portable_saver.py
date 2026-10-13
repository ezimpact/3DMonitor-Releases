import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.remote import RemoteAccess, COOKIE
from k1monitor.server import create_app
from test_monitor import FakeMonitor
from test_shared_registry import RegistryReader


class PortableTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.old=self.root/'old-profile'/'printers.json';self.old.parent.mkdir()
        self.printers=[{'id':'one','name':'Мастерская','ip':'192.168.4.104','firmware':'test'}]
        self.old.write_text(json.dumps({'printers':self.printers}))
        self.install=self.root/'K1 Monitor с пробелами';self.install.mkdir()
        self.path=self.install/'config.json';self.path.write_text(json.dumps({'registry_path':str(self.old)}))
        (self.install/'K1Monitor.exe').write_bytes(b'fixture')

    def test_migration_copies_old_registry_once_and_keeps_only_relative_path(self):
        original=self.old.read_bytes();store=ConfigStore(self.path,portable=True)
        self.assertEqual(store.printers,self.printers);self.assertEqual(self.old.read_bytes(),original)
        saved=json.loads(self.path.read_text());self.assertEqual(saved['registry_path'],'data/printers.json')
        self.assertEqual(store.registry_path,self.install/'data'/'printers.json')
        self.assertTrue((self.install/'config.pre-portable.backup.json').exists())
        store.rename('one','Актуальное имя')
        self.path.write_text(json.dumps({'registry_path':str(self.old)}))
        self.assertEqual(ConfigStore(self.path,portable=True).printers[0]['name'],'Актуальное имя')

    def test_move_whole_folder_and_change_profile_keeps_both_readers_working(self):
        store=ConfigStore(self.path,portable=True)
        component=self.install/'CameraSaver';component.mkdir()
        (component/'CrealityCameraSaver.exe').write_bytes(b'fixture')
        (component/'config.json').write_text(json.dumps({'version':3,'registry_path':'../data/printers.json','disabled_printers':[]}))
        target=self.root/'Другая папка'/'K1Monitor';target.parent.mkdir();shutil.move(str(self.install),str(target))
        self.old.unlink()
        with patch.dict('os.environ',{'LOCALAPPDATA':str(self.root/'new-profile')}):
            moved=ConfigStore(target/'config.json',portable=True)
            reader=RegistryReader(target/'CameraSaver'/'config.json')
            self.assertEqual(reader.snapshot()['printers'][0]['name'],'Мастерская')
            moved.update('one',name='После переноса',ip='192.168.4.110')
            self.assertEqual(reader.get('one')['ip'],'192.168.4.110')
            self.assertEqual(json.loads((target/'config.json').read_text())['registry_path'],'data/printers.json')

    def test_stale_absolute_saver_preference_cannot_redirect_bundled_reader(self):
        store=ConfigStore(self.path,portable=True);component=self.install/'CameraSaver';component.mkdir()
        path=component/'config.json';path.write_text(json.dumps({'registry_path':str(self.root/'nonexistent.json'),'disabled_printers':[]}))
        self.assertEqual(RegistryReader(path).get('one')['ip'],'192.168.4.104')

    def test_missing_old_database_is_reported_without_creating_empty_list(self):
        self.old.unlink()
        with self.assertRaises(ValueError):ConfigStore(self.path,portable=True)
        self.assertFalse((self.install/'data'/'printers.json').exists())


