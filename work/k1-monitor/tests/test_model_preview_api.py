"""Source and frozen static routing; no live printers or Windows UI."""
from pathlib import Path
import tempfile,sys,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app
from test_monitor import FakeMonitor

class ModelPreviewApiTests(unittest.TestCase):
    def manager(self,root):
        path=root/'config.json';path.write_text('{"printers":[]}',encoding='utf-8')
        return PrinterManager(ConfigStore(path),factory=FakeMonitor)
    def test_vendor_mime_types_and_eval_is_restricted_to_cad_worker(self):
        with tempfile.TemporaryDirectory() as root,TestClient(create_app(self.manager(Path(root))),base_url='http://127.0.0.1:8765') as client:
            html=client.get('/');self.assertIn('model-open',html.text);self.assertNotIn('unsafe-eval',html.headers['content-security-policy'])
            self.assertIn('worker-src',html.headers['content-security-policy'])
            worker=client.get('/static/model-preview-worker.js');self.assertIn('unsafe-eval',worker.headers['content-security-policy'])
            wasm=client.get('/static/vendor/model-preview/occt-import-js.wasm')
            self.assertEqual(wasm.status_code,200);self.assertTrue(wasm.headers['content-type'].startswith('application/wasm'))
            self.assertEqual(wasm.content[:4],b'\x00asm')
    def test_frozen_exe_loads_separate_replaceable_vendor_folder(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);vendor=root/'ModelPreview';vendor.mkdir();(vendor/'fixture.js').write_text('/* replaceable */')
            with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(root/'K1Monitor.exe')):
                app=create_app(self.manager(root))
            with TestClient(app,base_url='http://127.0.0.1:8765') as client:
                self.assertEqual(client.get('/static/vendor/model-preview/fixture.js').text,'/* replaceable */')
