"""Read-only fixtures and pure scheduling decisions; no print or motion calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient
from k1monitor.operations import RunoutPlan
from k1monitor.telemetry import Telemetry
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app
from k1monitor.files import Thumbnails
from test_monitor import FakeMonitor

def snapshot():
    return {'printer':{'ip':'192.168.4.104'},'connection':{'sessions':1,'online':True,'stale':False},
            'print':{'file':'one.gcode'},'filament_runout':False,'paused_confirmed':True,'unload_eligible':True}

class RunoutDecisionTests(unittest.TestCase):
    def test_five_seconds_once_then_rearm_only_after_clear(self):
        plan=RunoutPlan();s=snapshot();self.assertFalse(plan.tick(s,True,100))
        s['filament_runout']=True
        self.assertFalse(plan.tick(s,True,101));self.assertFalse(plan.tick(s,True,105.99));self.assertTrue(plan.tick(s,True,106))
        self.assertFalse(plan.tick(s,True,200));s['filament_runout']=False;plan.tick(s,True,201)
        s['filament_runout']=True;plan.tick(s,True,202);self.assertTrue(plan.tick(s,True,207))
    def test_default_disabled_and_startup_runout_are_never_replayed(self):
        for enabled in (False,True):
            plan=RunoutPlan();s=snapshot();s['filament_runout']=True
            self.assertFalse(plan.tick(s,enabled,1));self.assertFalse(plan.tick(s,enabled,100))
    def test_disable_job_change_reconnect_and_ip_change_cancel_pending(self):
        for change in ('disable','file','job_id','session','ip','offline'):
            plan=RunoutPlan();s=snapshot();plan.tick(s,True,1);s['filament_runout']=True;plan.tick(s,True,2)
            if change=='file':s['print']['file']='other.gcode'
            if change=='job_id':s['print']['job_id']='new-print-of-same-file'
            if change=='session':s['connection']['sessions']=2
            if change=='ip':s['printer']['ip']='192.168.4.105'
            if change=='offline':s['connection']['online']=False
            self.assertFalse(plan.tick(s,change!='disable',7),change)
            s['connection']['online']=True
            self.assertFalse(plan.tick(s,True,20),change)
    def test_no_command_decision_until_pause_and_safe_error_state(self):
        plan=RunoutPlan();s=snapshot();plan.tick(s,True,1);s.update(filament_runout=True,paused_confirmed=False)
        self.assertFalse(plan.tick(s,True,2));self.assertFalse(plan.tick(s,True,8))
        s.update(paused_confirmed=True,unload_eligible=False);self.assertFalse(plan.tick(s,True,9))
        s['unload_eligible']=True;self.assertTrue(plan.tick(s,True,10))

class NewTelemetryTests(unittest.TestCase):
    def test_stock_objects_strings_and_job_change_clear_previous_models(self):
        state=Telemetry('192.168.4.104','test');state.begin_session()
        state.ingest({'printFileName':'one.gcode','objects':'[{"name":"a"},{"name":"b"}]','excluded_objects':'["a"]','current_object':'b','lightSw':1,'curFeedratePct':85},now=100)
        result=state.snapshot(now=101);self.assertTrue(result['objects'][0]['excluded']);self.assertTrue(result['objects'][1]['current']);self.assertEqual(result['speed_percent'],85);self.assertTrue(result['light_on'])
        state.ingest({'printFileName':'two.gcode'},now=102);self.assertEqual(state.snapshot(now=103)['objects'],[])
    def test_material_sensor_runout_requires_fresh_pause_and_no_other_error(self):
        state=Telemetry('192.168.4.104','test');state.begin_session()
        state.ingest({'materialStatus':1,'state':5,'err':{'errcode':0,'key':0}},now=100)
        result=state.snapshot(now=101);self.assertTrue(result['filament_runout']);self.assertTrue(result['unload_eligible']);self.assertTrue(result['paused_confirmed']);self.assertEqual(result['error_code'],116)
        state.ingest({'nozzleTemp':240},now=116);self.assertFalse(state.snapshot(now=117)['unload_eligible'])
        state.ingest({'materialStatus':1,'state':5,'err':{'key':201}},now=118);self.assertFalse(state.snapshot(now=119)['unload_eligible'])
        state.ingest({'materialStatus':0,'state':1,'err':{'key':0}},now=120);self.assertFalse(state.snapshot(now=121)['filament_runout'])

class ReadOnlyNewApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'config.json';self.path.write_text(json.dumps({'printer_ip':'192.168.4.104'}))
        self.manager=PrinterManager(ConfigStore(self.path),factory=FakeMonitor);self.monitor=self.manager.get('k1max');self.app=create_app(self.manager)
        monitor=self.monitor;test=self
        class ReadSocket:
            async def send(self,raw):
                request=json.loads(raw);test.assertEqual(request['method'],'get')
                if request['params']=={'reqGcodeFile':1}:
                    monitor.files.ingest({'retGcodeFileInfo':{'fileInfo':'/usr/data/printer_data/gcodes:one.gcode:123:0.2:1:1:/tmp/humbnail/one.png;'}})
                elif request['params']=={'printObjects':1}:
                    monitor.state.ingest({'objects':'[{"name":"a"}]','current_object':'a','excluded_objects':'[]'});monitor._objects_event.set()
                else:test.fail('Unexpected GET')
        self.monitor._ws=ReadSocket()
        self.headers={'X-K1-Token':self.app.state.csrf_token}
    def test_per_printer_setting_persists_and_does_not_enable_other_printer(self):
        self.manager.config.add('192.168.4.105','Second')
        with TestClient(self.app,base_url='http://127.0.0.1:8765') as client:
            reply=client.put('/api/v1/printers/k1max/auto-unload',json={'enabled':True},headers=self.headers)
            self.assertEqual(reply.status_code,200)
            printers=ConfigStore(self.path).printers;self.assertTrue(printers[0]['auto_unload']);self.assertFalse(printers[1].get('auto_unload',False))
            client.put('/api/v1/printers/k1max/auto-unload',json={'enabled':False},headers=self.headers)
            self.assertFalse(ConfigStore(self.path).printers[0]['auto_unload'])
    def test_file_thumbnail_bound_to_catalog_and_session(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8765') as client:
            data=client.get('/api/v1/printers/k1max/files').json();file=data['files'][0]
            url=f"/api/v1/printers/k1max/files/{file['id']}/thumbnail"
            with patch.object(Thumbnails,'get',new=AsyncMock(return_value=(b'\x89PNG\r\n\x1a\n','image/png'))) as proxy:
                self.assertEqual(client.get(url,params={'session':1,'revision':data['revision']}).status_code,200)
                self.assertEqual(client.get(url,params={'session':2,'revision':data['revision']}).status_code,409)
                self.assertEqual(proxy.await_count,1)
    def test_current_print_file_cannot_be_deleted_and_last_object_cannot_be_excluded(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8765') as client:
            self.monitor.state.ingest({'printFileName':'one.gcode'})
            data=client.get('/api/v1/printers/k1max/files').json()
            reply=client.post('/api/v1/printers/k1max/files/delete',headers=self.headers,json={'file_ids':[data['files'][0]['id']],'revision':data['revision'],'expected_session':1,'expected_ip':'192.168.4.104','request_id':'delete-123456'})
            self.assertEqual(reply.status_code,409)
            objects=client.get('/api/v1/printers/k1max/objects');self.assertEqual(objects.status_code,200);self.assertEqual(len(objects.json()['objects']),1)
            reply=client.post('/api/v1/printers/k1max/settings-command',headers=self.headers,json={'action':'exclude','value':'a','expected_file':'one.gcode','expected_ip':'192.168.4.104','expected_session':1,'request_id':'exclude-123456'})
            self.assertEqual(reply.status_code,409)
