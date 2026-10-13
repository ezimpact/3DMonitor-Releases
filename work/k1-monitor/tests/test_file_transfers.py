"""HTTP-only file fixtures; no printing, motion, heat or WebSocket SET."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

import httpx
from fastapi.testclient import TestClient
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app
from k1monitor.file_transfers import gcode_name
from test_monitor import FakeMonitor

CONTENT='; Тест передачи\nG90\n'.encode()
NAME='Катушка.gcode'

def listing(monitor,name=NAME,content=CONTENT):
    monitor.files.ingest({'retGcodeFileInfo2':[] if name is None else [{'path':'/usr/data/printer_data/gcodes/'+name,'file_size':len(content),'create_time':1}]})

class TransferMonitor(FakeMonitor):
    async def start(self):
        await super().start();self._ws=object()
    async def list_files(self,refresh=False):return self.files

class TransfersTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        config=Path(self.temp.name)/'config.json';config.write_text(json.dumps({'printers':[{'id':'source','name':'Source','ip':'192.168.4.104'},{'id':'target','name':'Target','ip':'192.168.4.114'}]}))
        self.manager=PrinterManager(ConfigStore(config),factory=TransferMonitor)
        self.source=self.manager.get('source');self.target=self.manager.get('target');listing(self.source);listing(self.target,None)
        self.app=create_app(self.manager);self.headers={'X-K1-Token':self.app.state.csrf_token,'Content-Type':'application/octet-stream'}
        self.calls=[];self.on_get=lambda:None;self.confirm_upload=True;self.fail_upload=False
        def handler(request):
            self.calls.append(request)
            if request.method=='GET':
                self.assertEqual(request.url.host,'192.168.4.104');self.assertEqual(request.url.path,'/downloads/gcode/'+NAME);self.on_get()
                return httpx.Response(200,content=CONTENT,headers={'content-type':'text/plain'})
            self.assertEqual(request.method,'POST');self.assertEqual(request.url.host,'192.168.4.114');self.assertEqual(request.url.path,'/upload/'+NAME)
            self.assertIn('multipart/form-data',request.headers['content-type']);self.assertIn(b'name="file"',request.content);self.assertIn(CONTENT,request.content)
            if self.fail_upload:raise httpx.ReadTimeout('lost response',request=request)
            if self.confirm_upload:listing(self.target)
            return httpx.Response(200,text='ok')
        self.app.state.file_transfers.client_factory=lambda **kwargs:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kwargs)
        self.client=TestClient(self.app,base_url='http://127.0.0.1:8765');self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
    def upload(self,content=CONTENT,request_id='upload-test-123',filename=NAME,headers=None):
        return self.client.post('/api/v1/printers/target/files/upload',params={'filename':filename,'expected_session':1,'expected_ip':'192.168.4.114','request_id':request_id},content=content,headers=self.headers if headers is None else headers)
    def transfer(self,**changes):
        entry=self.source.files.files[0]
        body={'revision':self.source.files.revision,'expected_session':1,'expected_ip':'192.168.4.104','target_id':'target','target_session':1,'target_ip':'192.168.4.114','request_id':'transfer-12345',**changes}
        return self.client.post(f'/api/v1/printers/source/files/{entry["id"]}/transfer',json=body,headers={'X-K1-Token':self.app.state.csrf_token})
    def test_download_preserves_bytes_and_encoded_filename(self):
        file=self.source.files.files[0]
        response=self.client.get(f'/api/v1/printers/source/files/{file["id"]}/download',params={'revision':self.source.files.revision,'session':1,'ip':'192.168.4.104'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.content,CONTENT)
        self.assertIn(quote(NAME),response.headers['content-disposition']);self.assertEqual([r.method for r in self.calls],['GET'])
    def test_upload_is_multipart_only_and_duplicate_request_is_not_sent_again(self):
        self.assertEqual(self.upload().json()['phase'],'complete');self.assertEqual(self.upload().json()['phase'],'complete')
        self.assertEqual(len(self.calls),1);self.assertEqual(self.upload(content=CONTENT+b'; changed').status_code,409);self.assertEqual(len(self.calls),1)
    def test_transfer_downloads_from_source_and_uploads_to_selected_target(self):
        response=self.transfer();self.assertEqual(response.status_code,200);self.assertEqual(response.json()['phase'],'complete')
        self.assertEqual([r.method for r in self.calls],['GET','POST'])
    def test_rejects_existing_file_including_active_job_without_any_upload(self):
        listing(self.target);self.target.state.ingest({'state':1,'printFileName':NAME})
        self.assertEqual(self.upload().status_code,409);self.assertEqual(self.calls,[])
    def test_csrf_and_invalid_names_reject_before_network(self):
        self.assertEqual(self.upload(headers={}).status_code,403)
        for name in ['../x.gcode','x.exe','a;b.gcode','a\ngcode.gcode','a:b.gcode']:
            self.assertEqual(self.upload(filename=name).status_code,409,name)
        self.assertEqual(self.calls,[])
    def test_wrong_session_ip_or_same_target_cannot_transfer(self):
        for change in [{'target_session':2},{'expected_ip':'127.0.0.1'},{'target_id':'source'}]:
            self.assertEqual(self.transfer(**change).status_code,409)
        self.assertEqual(self.calls,[])
    def test_source_revision_change_and_target_replacement_cancel_before_upload(self):
        self.on_get=lambda:listing(self.source,'changed.gcode')
        self.assertEqual(self.transfer().status_code,409);self.assertEqual([r.method for r in self.calls],['GET'])
        listing(self.source);self.calls.clear()
        self.on_get=lambda:self.manager.monitors.__setitem__('target',TransferMonitor('192.168.4.114'))
        self.assertEqual(self.transfer().status_code,409);self.assertEqual([r.method for r in self.calls],['GET'])
    def test_empty_binary_and_oversized_uploads_are_rejected(self):
        for content in [b'',b'G90\0']:
            self.assertEqual(self.upload(content=content).status_code,409)
        with patch('k1monitor.file_transfers.MAX_GCODE',4):self.assertEqual(self.upload().status_code,409)
        self.assertEqual(self.calls,[])
    def test_missing_catalog_confirmation_and_lost_reply_are_not_retried(self):
        self.confirm_upload=False
        self.assertEqual(self.upload().json()['phase'],'unconfirmed');self.assertEqual(self.upload().json()['phase'],'unconfirmed');self.assertEqual(len(self.calls),1)
        self.fail_upload=True
        self.assertEqual(self.upload(request_id='other-test-123').json()['phase'],'unconfirmed')
        self.assertEqual(self.upload(request_id='other-test-123').json()['phase'],'unconfirmed');self.assertEqual(len(self.calls),2)
    def test_unsafe_catalog_path_does_not_reach_http(self):
        entry=self.source.files.files[0];entry['path']='/usr/data/printer_data/gcodes/../config.gcode'
        self.assertEqual(self.transfer().status_code,409);self.assertEqual(self.calls,[])

class NamesTests(unittest.TestCase):
    def test_unicode_names_and_uppercase_gcode_extension_supported(self):
        self.assertEqual(gcode_name('Катушка V2.GCODE'),'Катушка V2.GCODE')
