"""Mock transports and pure command validation. No start_print calls or hardware."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
from k1monitor.config import ConfigStore
from k1monitor.moonraker import MoonrakerMonitor, moon_command, relative_path
from k1monitor.protocols import camera_path
from k1monitor.operations import RunoutPlan, UnloadHeating
from k1monitor.file_transfers import FileTransfers
from test_file_transfers import TransferMonitor, listing

OBJECTS={'webhooks','print_stats','virtual_sdcard','extruder','heater_bed','pause_resume','gcode_move',
         'display_status','exclude_object','bed_mesh','filament_switch_sensor filament_sensor',
         'gcode_macro UNLOAD_FILAMENT','output_pin LED_pin'}
def monitor():
    m=MoonrakerMonitor('192.168.4.102','test');m.objects=OBJECTS.copy();m._ws=object();m.state.begin_session()
    m._ingest({'webhooks':{'state':'ready'},'print_stats':{'state':'standby','filename':''},
        'extruder':{'temperature':25,'target':0},'heater_bed':{'temperature':26,'target':0},
        'pause_resume':{'is_paused':False},'filament_switch_sensor filament_sensor':{'enabled':True,'filament_detected':False},
        'gcode_move':{'speed_factor':1},'output_pin LED_pin':{'value':0}})
    return m

class MoonStateTests(unittest.TestCase):
    def test_missing_print_state_or_virtual_sdcard_cannot_authorize_launch(self):
        m=monitor();m.objects.remove('virtual_sdcard');self.assertFalse(m.snapshot()['controls']['start'])
        m.objects.add('virtual_sdcard');m.raw_status['print_stats']={};self.assertFalse(m.snapshot()['controls']['start'])
    def test_idle_empty_sensor_does_not_claim_print_failure_and_camera_is_absent(self):
        s=monitor().snapshot();self.assertEqual(s['status'],'idle');self.assertFalse(s['error']['active'])
        self.assertTrue(s['controls']['start']);self.assertFalse(s['camera']['present']);self.assertFalse(s['auto_unload']['enabled'])
    def test_partial_status_preserves_job_and_heater_freshness(self):
        m=monitor();m._ingest({'print_stats':{'state':'printing','filename':'folder/one.gcode','print_duration':50},'display_status':{'progress':.25}})
        before=dict(m.state.field_times);m._ingest({'extruder':{'temperature':201}})
        s=m.snapshot();self.assertEqual(s['print']['file'],'folder/one.gcode');self.assertEqual(s['print']['progress_percent'],25)
        self.assertEqual(s['temperatures']['nozzle_target'],0);self.assertEqual(m.state.field_times['targetNozzleTemp'],before['targetNozzleTemp'])
        self.assertEqual(m.state.field_times['materialStatus'],before['materialStatus'])
        self.assertEqual(s['print']['remaining_seconds'],150)
    def test_repeated_same_file_is_a_new_job_but_partial_printing_update_is_not(self):
        m=monitor();m._ingest({'print_stats':{'state':'printing','filename':'one.gcode'}});job=m.snapshot()['print']['job_id']
        m._ingest({'print_stats':{'print_duration':30}});self.assertEqual(m.snapshot()['print']['job_id'],job)
        m._ingest({'print_stats':{'state':'complete'}});m._ingest({'print_stats':{'state':'printing'}})
        self.assertNotEqual(m.snapshot()['print']['job_id'],job)
    def test_shutdown_and_disconnect_disable_all_controls(self):
        m=monitor();m._ingest({'webhooks':{'state':'shutdown','state_message':'MCU error'}})
        self.assertFalse(any(m.snapshot()['controls'].values()));self.assertFalse(m.snapshot()['unload_eligible'])
        m.state.disconnected();self.assertFalse(m.snapshot()['connection']['online'])
    def test_new_runout_waits_for_pause_and_heat_and_never_replays_startup_error(self):
        m=monitor();m._ingest({'print_stats':{'state':'printing','filename':'one.gcode'},'filament_switch_sensor filament_sensor':{'filament_detected':True}})
        plan=RunoutPlan();self.assertFalse(plan.tick(m.snapshot(),True,0))
        m._ingest({'filament_switch_sensor filament_sensor':{'filament_detected':False}});self.assertFalse(plan.tick(m.snapshot(),True,1))
        self.assertFalse(plan.tick(m.snapshot(),True,7))
        m._ingest({'print_stats':{'state':'paused'},'pause_resume':{'is_paused':True}})
        self.assertTrue(plan.tick(m.snapshot(),True,8));self.assertFalse(plan.tick(m.snapshot(),True,9))
        startup=RunoutPlan();self.assertFalse(startup.tick(m.snapshot(),True,20));self.assertFalse(startup.tick(m.snapshot(),True,40))
        heat=UnloadHeating();heat.start(m.snapshot(),240,8);self.assertFalse(heat.tick(m.snapshot(),True,240,9))
        m._ingest({'extruder':{'temperature':240,'target':240}})
        self.assertFalse(heat.tick(m.snapshot(),True,240,10));self.assertTrue(heat.tick(m.snapshot(),True,240,12))
    def test_other_error_blocks_unload_and_absent_macro_disables_feature(self):
        m=monitor();m._ingest({'print_stats':{'state':'error','filename':'one.gcode','message':'Heater failure'},'pause_resume':{'is_paused':True}})
        self.assertFalse(m.snapshot()['unload_eligible']);m.objects.remove('gcode_macro UNLOAD_FILAMENT')
        self.assertFalse(m.snapshot()['auto_unload']['supported'])
    def test_no_missing_heater_can_be_controlled(self):
        m=monitor();m._ingest({'extruder':{'target':None}});self.assertFalse(m.snapshot()['controls']['nozzle'])
    def test_object_state_and_single_last_model_guard_retained(self):
        m=monitor();m._ingest({'print_stats':{'state':'printing','filename':'one.gcode'},'exclude_object':{'objects':[{'name':'a'},{'name':'b'}],'excluded_objects':[],'current_object':'a'}})
        self.assertTrue(m.snapshot()['controls']['exclude']);self.assertTrue(m.snapshot()['objects'][0]['current'])
        m._ingest({'exclude_object':{'excluded_objects':['b']}});self.assertFalse(m.snapshot()['controls']['exclude'])

class MoonValidationTests(unittest.TestCase):
    def test_build_launch_and_calibration_without_executing(self):
        params={'opGcodeFile':'printprt:folder/one.gcode','enableSelfTest':0}
        self.assertEqual(moon_command(params,OBJECTS,{}),('POST','/printer/print/start',{'filename':'folder/one.gcode'}))
        params['enableSelfTest']=1;command=moon_command(params,OBJECTS,{})
        self.assertIn('BED_MESH_CALIBRATE',command[2]['script']);self.assertNotIn('SAVE_CONFIG',command[2]['script'])
        with self.assertRaises(ValueError):moon_command(params,OBJECTS-{'bed_mesh'},{})
    def test_paths_and_model_names_cannot_inject_gcode_or_traverse(self):
        for path in ['../x.gcode','a/../x.gcode','/x.gcode','a\\x.gcode','x"\nM104.gcode']:
            self.assertFalse(relative_path(path))
            with self.assertRaises(ValueError):moon_command({'opGcodeFile':'deleteprt:'+path},OBJECTS,{})
        with self.assertRaises(ValueError):moon_command({'excludeObjects':['a\nG28']},OBJECTS,{})
    def test_temperature_limits_follow_both_ui_and_printer(self):
        cfg={'extruder':{'max_temp':250},'heater_bed':{'max_temp':80}}
        for params in [{'nozzleTempControl':250},{'bedTempControl':{'val':80}},{'nozzleTempControl':True}]:
            with self.assertRaises(ValueError):moon_command(params,OBJECTS,cfg)
        self.assertIn('TARGET=0',moon_command({'nozzleTempControl':0},OBJECTS,cfg)[2]['script'])
    def test_camera_url_is_optional_and_bound_to_printer(self):
        self.assertEqual(camera_path('','192.168.4.102'),'')
        self.assertEqual(camera_path('http://192.168.4.102:8080/?action=stream','192.168.4.102'),'http://192.168.4.102:8080/?action=stream')
        for path in ['//other/stream','http://127.0.0.1/','http://192.168.4.104/','/\\evil/','/stream#fragment','http://user@192.168.4.102/']:
            with self.assertRaises(ValueError):camera_path(path,'192.168.4.102')
    def test_protocol_switch_disables_automation_and_uses_default_port(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json';old={'id':'one','name':'K1','ip':'192.168.4.104','firmware':'test','auto_unload':True,'auto_unload_temperature':230}
            path.write_text(json.dumps({'printers':[old]}));store=ConfigStore(path)
            self.assertEqual(store.printers,[old]);store.update('one',protocol='moonraker')
            self.assertFalse(store.printers[0]['auto_unload']);self.assertEqual(store.printers[0]['api_port'],7125)
            self.assertEqual(store.printers[0]['auto_unload_temperature'],230)
            self.assertEqual(ConfigStore(path).printers,store.printers)

class MoonReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_ignores_bad_disabled_and_empty_cameras(self):
        m=monitor()
        async def get(path):
            return {'webcams':[None,{}, {'enabled':False,'stream_url':'/disabled'}, {'stream_url':'http://127.0.0.1/secret'}, {'stream_url':'/webcam/?action=stream'}]}
        m._get=get;await m._discover_camera();self.assertEqual(m.camera_path,'/webcam/?action=stream')
    async def test_nested_catalog_thumbnail_are_readonly_and_metadata_is_lazy(self):
        m=monitor();calls=[]
        def handler(req):
            calls.append((req.method,req.url.path));self.assertEqual(req.method,'GET')
            if req.url.path.endswith('/list'):return httpx.Response(200,json={'result':[{'path':'folder/one.gcode','size':10,'modified':4}]})
            if req.url.path.endswith('/metadata'):return httpx.Response(200,json={'result':{'thumbnails':[{'width':32,'relative_path':'.thumbs/one.png'}]}})
            return httpx.Response(200,content=b'\x89PNG\r\n\x1a\nfixture')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as m._client:
            files=await m.list_files(refresh=True);self.assertEqual(len(calls),1);self.assertEqual(files.files[0]['path'],'folder/one.gcode')
            content,mime=await m.thumbnail('folder/one.gcode');self.assertEqual(mime,'image/png');self.assertTrue(content)
            self.assertEqual(calls[-1][1],'/server/files/gcodes/folder/.thumbs/one.png')
    async def test_scan_recognizes_moonraker_via_get_only(self):
        from k1monitor.scanner import probe_printer
        calls=[]
        def handler(req):
            calls.append(req);self.assertEqual(req.method,'GET')
            return httpx.Response(200,json={'result':{'moonraker_version':'test'} if req.url.path=='/server/info' else {'hostname':'v400','software_version':'test'}})
        real=httpx.AsyncClient
        with patch('k1monitor.scanner.probe_creality',return_value=None),patch('k1monitor.scanner.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
            result=await probe_printer('192.168.4.102')
        self.assertEqual(result['protocol'],'moonraker');self.assertEqual(result['api_port'],7125);self.assertEqual(len(calls),2)
    async def test_cross_protocol_transfer_in_both_directions_never_prints(self):
        content=b'; file transfer fixture\n';name='one.gcode'
        for to_moon in (True,False):
            moon=monitor();creality=TransferMonitor('192.168.4.104','test');creality.state.begin_session();creality.state.ingest({'state':0});creality._ws=object()
            source,target=(creality,moon) if to_moon else (moon,creality)
            def moon_listing(items):
                moon.files.files=items;moon.files.revision=hashlib.sha256(json.dumps(items).encode()).hexdigest();moon.files.updated=time.monotonic()
            entry={'id':'moon-file','name':name,'path':'folder/'+name,'size_bytes':len(content),'modified':1,'thumbnail':None}
            moon_listing([] if to_moon else [entry]);listing(creality,name if to_moon else None,content)
            async def local_catalog(*,refresh=False):return moon.files
            moon.list_files=local_catalog
            class Manager:
                def get(self,key):return source if key=='source' else target
                def entry(self,key):return {'name':key}
            calls=[]
            def handler(req):
                calls.append(req)
                if req.method=='GET':return httpx.Response(200,content=content)
                self.assertEqual(req.method,'POST');self.assertIn(b'name="file"',req.content)
                if to_moon:
                    self.assertEqual(req.url.path,'/server/files/upload');self.assertIn(b'name="print"\r\n\r\nfalse',req.content)
                    moon_listing([{**entry,'path':name}])
                else:self.assertEqual(req.url.path,'/upload/'+name);listing(creality,name,content)
                return httpx.Response(200,json={})
            transfers=FileTransfers(Manager(),client_factory=lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw))
            file=source.files.files[0];revision=source.files.revision
            spool,size,digest,filename=await transfers.download('source',file['id'],revision,1,source.state.host)
            try:result=await transfers.upload('target',filename,spool,size,digest,1,target.state.host,'mock-transfer-123')
            finally:spool.close()
            self.assertEqual(result['phase'],'complete');self.assertEqual([r.method for r in calls],['GET','POST'])
            if not to_moon:self.assertEqual(calls[0].url.path,'/server/files/gcodes/folder/one.gcode')
