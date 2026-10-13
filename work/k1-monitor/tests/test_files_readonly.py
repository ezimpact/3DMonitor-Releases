"""File read/thumbnail tests only. No start-print calls, even on a fake."""
import asyncio
import unittest
import tempfile,json
from pathlib import Path
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient
from k1monitor.files import parse_listing,FileCatalog,thumbnail_path,safe_file_path,Thumbnails
from k1monitor.telemetry import Telemetry
from test_monitor import FakeMonitor
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app

class FileParsingTests(unittest.TestCase):
    def test_stock_listing_keeps_unicode_names_exact_paths_and_metadata(self):
        result=parse_listing({'retGcodeFileInfo':{'totalNum':1,'fileInfo':'/usr/data/printer_data/gcodes:Шпуля 1.gcode:123:0.2:1791122358:456:/tmp/creality/local_gcode/humbnail/Шпуля 1.png;'}})
        self.assertEqual(result[0]['name'],'Шпуля 1.gcode')
        self.assertEqual(result[0]['path'],'/usr/data/printer_data/gcodes/Шпуля 1.gcode')
        self.assertEqual(result[0]['size_bytes'],123)
        self.assertTrue(result[0]['thumbnail'].startswith('/downloads/humbnail/%D0'))
    def test_new_listing_and_empty_list_replace_previous_data(self):
        catalog=FileCatalog();catalog.ingest({'retGcodeFileInfo2':[{'name':'test.gcode','path':'/usr/data/printer_data/gcodes/test.gcode','file_size':123,'create_time':1,'thumbnail':'/tmp/creality/local_gcode/humbnail/test.png'}]})
        old=catalog.revision;self.assertEqual(len(catalog.public()),1)
        catalog.ingest({'retGcodeFileInfo':{'totalNum':0,'fileInfo':''}})
        self.assertEqual(catalog.public(),[]);self.assertNotEqual(catalog.revision,old)
    def test_invalid_catalog_paths_are_not_exposed(self):
        for path in ('/tmp/test.gcode','/usr/data/printer_data/gcodes/../test.gcode','/usr/data/printer_data/gcodes/x\n.gcode','/usr/data/printer_data/gcodes/test.3mf','/usr/data/printer_data/gcodes//x.gcode'):
            self.assertFalse(safe_file_path(path))
        result=parse_listing({'retGcodeFileInfo2':[{'path':'/tmp/test.gcode'}]})
        self.assertEqual(result,[])
        self.assertIsNone(thumbnail_path('http://remote.test/humbnail/../../private.png'))
        self.assertIsNone(thumbnail_path('/tmp/humbnail/x.svg'))
    def test_repeated_file_has_stable_id_but_changed_metadata_changes_revision(self):
        catalog=FileCatalog();entry={'path':'/usr/data/printer_data/gcodes/one.gcode','file_size':1,'create_time':1}
        catalog.ingest({'retGcodeFileInfo2':[entry]});key=catalog.files[0]['id'];revision=catalog.revision
        catalog.ingest({'retGcodeFileInfo2':[{**entry,'file_size':2}]})
        self.assertEqual(catalog.files[0]['id'],key);self.assertNotEqual(catalog.revision,revision)
        catalog.reset();self.assertEqual(catalog.files,[]);self.assertEqual(catalog.updated,0)

class ErrorTelemetryTests(unittest.TestCase):
    def test_numeric_error_object_key_and_message_are_preserved(self):
        state=Telemetry('192.168.4.104','test');state.begin_session()
        state.ingest({'state':1,'err':2839},now=100)
        self.assertEqual(state.snapshot(now=101)['error_code'],2839);self.assertEqual(state.snapshot(now=101)['status'],'error')
        state.ingest({'err':{'errcode':0,'key':202,'value':'{"msg":"Ошибка нагрева"}'}},now=102)
        error=state.snapshot(now=103)['error'];self.assertEqual(error['key'],'202');self.assertEqual(error['message'],'Ошибка нагрева');self.assertTrue(error['active'])
        state.ingest({'err':{'errcode':0,'key':0}},now=104)
        self.assertFalse(state.snapshot(now=105)['error']['active']);self.assertEqual(state.snapshot(now=105)['status'],'printing')
    def test_missing_or_invalid_errors_are_not_fabricated(self):
        state=Telemetry('192.168.4.104','test');state.begin_session()
        for error in (None,True,{},'invalid'):
            state.ingest({'state':1,'err':error},now=100)
            self.assertFalse(state.snapshot(now=101)['error']['active'])

class ReadOnlyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'config.json';path.write_text(json.dumps({'printer_ip':'192.168.4.104'}))
        self.manager=PrinterManager(ConfigStore(path),factory=FakeMonitor);self.app=create_app(self.manager)
    def test_file_read_uses_get_and_thumbnail_stays_bound_to_job(self):
        monitor=self.manager.get('k1max')
        class ReadSocket:
            async def send(self,raw):
                import json
                message=json.loads(raw)
                self_test.assertEqual(message,{'method':'get','params':{'reqGcodeFile':1}})
                monitor.files.ingest({'retGcodeFileInfo':{'fileInfo':'/usr/data/printer_data/gcodes:one.gcode:123:0.2:1:456:/tmp/humbnail/one.png;'}})
        self_test=self;monitor._ws=ReadSocket()
        with TestClient(self.app,base_url='http://127.0.0.1:8765') as client:
            monitor.state.ingest({'printFileName':'/usr/data/printer_data/gcodes/one.gcode'})
            files=client.get('/api/v1/printers/k1max/files').json()
            self.assertEqual(files['files'][0]['name'],'one.gcode');self.assertFalse(files['can_start'])
            params={'file':monitor.state.data['printFileName'],'session':1}
            with patch.object(Thumbnails,'get',new=AsyncMock(return_value=(b'\x89PNG\r\n\x1a\n','image/png'))) as proxy:
                reply=client.get('/api/v1/printers/k1max/thumbnail',params=params)
                self.assertEqual(reply.status_code,200);self.assertEqual(reply.headers['content-type'],'image/png')
                self.assertEqual(proxy.call_args.args,('192.168.4.104','/downloads/humbnail/one.png'))
                self.assertEqual(client.get('/api/v1/printers/k1max/thumbnail',params={**params,'session':2}).status_code,409)
                self.assertEqual(proxy.await_count,1)

