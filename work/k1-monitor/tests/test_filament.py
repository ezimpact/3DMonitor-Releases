import asyncio
import io
import json
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import httpx

from fastapi.testclient import TestClient
from k1monitor.activity import ActivityStore, ActivityTracker, ActivityService
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.notifications import Notifications, text
from k1monitor.server import create_app
from k1monitor.remote import RemoteAccess
from k1monitor.gcode_usage import parse_gcode, consumption
from k1monitor.filament import FilamentService
from test_monitor import FakeMonitor
from test_activity_notifications import sample


def parse(code, **kwargs):
    return parse_gcode(io.BytesIO(code.encode()), **kwargs)


class GcodeUsageTests(unittest.TestCase):
    def test_slicer_weight_variants_and_meters(self):
        for header in ['; filament used [g] = 12.5', '; total filament used [g] = 12.5',
                       '; filament weight = 12.5 g', '; filament_weight = 12.5']:
            with self.subTest(header=header):
                self.assertAlmostEqual(parse(header)['total_g'],12.5)
        profile=parse(';Filament used: 2.0m\n;filament_diameter = 1.75\n;filament_density = 1.27')
        self.assertAlmostEqual(profile['length_mm'],2000)
        self.assertAlmostEqual(profile['total_g'],6.1093,places=3)

    def test_absolute_relative_retraction_and_g92_preserve_net_feed(self):
        a=parse('M82\nG1 E100\nG1 E98\nG1 E100\nG92 E0\nG1 E10\n')
        b=parse('M83\nG1 E100\nG1 E-2\nG1 E2\nG92 E0\nG1 E10\n')
        self.assertEqual(a['length_mm'],110)
        self.assertEqual(a['length_mm'],b['length_mm'])

    def test_layers_follow_extrusion_not_time_or_layer_fraction(self):
        code='M83\n;LAYER_CHANGE\n;BEFORE_LAYER_CHANGE\nG1 E10\n;AFTER_LAYER_CHANGE\n;LAYER_CHANGE\nG1 E90\n; filament used [g] = 100\n; filament used [mm] = 100'
        profile=parse(code)
        self.assertEqual(profile['layer_count'],2)
        self.assertEqual(consumption(profile,{'layer':1,'total_layers':2}), (0,10,'layers'))
        self.assertEqual(consumption(profile,{'layer':2,'total_layers':2}), (10,100,'layers'))
        self.assertEqual(consumption(profile,{'progress_percent':50}), (0,100,'reservation'))

    def test_cura_and_simplify_layer_markers(self):
        for marker in [';LAYER:0','; layer 1, Z = 0.2']:
            profile=parse('M83\n'+marker+'\nG1 E100')
            self.assertEqual(profile['layer_count'],1)

    def test_m73_uses_first_percent_boundary_not_repeated_last(self):
        profile=parse('M83\nM73 P0\nG1 E10\nM73 P0\nG1 E20\nM73 P10\nG1 E70\nM73 P100\n; filament used [g] = 100\n; filament used [mm] = 100')
        self.assertEqual(consumption(profile,{'progress_percent':0}), (0,30,'gcode_progress'))
        self.assertEqual(consumption(profile,{'progress_percent':10}), (30,100,'gcode_progress'))

    def test_bad_binary_multimaterial_volumetric_and_inch_unknown(self):
        for code in ['\0G1 E2','M200 D1.75\nG1 E20','G20\nG1 E20','M83\nT0\nG1 E100\nT1\nG1 E100',
                     '; filament used [g] = 10, 20','G1 X20']:
            with self.subTest(code=code):
                with self.assertRaises(ValueError):parse(code)

    def test_config_values_not_counted_as_consumption_and_density_override(self):
        profile=parse(';filament_density = 1.27\n;filament_diameter = 1.75\nM83\nG1 E1000')
        self.assertAlmostEqual(profile['total_g'],3.0547,places=3)
        self.assertFalse(profile['assumed_density'])

    def test_unsafe_layer_count_or_sequential_print_reserves_whole_job(self):
        profile=parse('M83\n;LAYER_CHANGE\nG1 E10\n;LAYER_CHANGE\nG1 E90')
        self.assertEqual(consumption(profile,{'layer':1,'total_layers':10})[2], 'reservation')
        profile=parse(';print_sequence = by object\nM83\n;LAYER_CHANGE\nG1 E100')
        self.assertEqual(consumption(profile,{'layer':1})[2], 'reservation')


PROFILE={'total_g':300.,'layers':[[1,0.],[2,100.],[3,200.],[4,300.]],
         'layer_count':3,'progress':[],'source':'slicer','assumed_density':False}

class SpoolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=1700000000.
        self.history=ActivityStore(self.root/'history.sqlite3')
        self.tracker=ActivityTracker(self.history,lambda:self.now)
        self.events=[];self.items=[]
        self.manager=types.SimpleNamespace(get=lambda pid:object(),entry=lambda pid:next(x['printer'] for x in self.items if x['printer']['id']==pid))
        async def loader(pid,job):return PROFILE
        self.loader=loader
        self.make_service()
    def make_service(self):
        self.service=FilamentService(self.manager,self.tracker,types.SimpleNamespace(enqueue=self.events.append),self.root/'filament.sqlite3',clock=lambda:self.now,loader=self.loader)
    async def asyncTearDown(self):await self.service.stop()
    async def tick(self,status='printing',layer=1,runout=False,file='part.gcode',pid='one',online=True):
        self.now+=1
        item=sample(status,pid=pid,elapsed=100+layer,file=file,runout=runout,online=online)
        item['print'].update(layer=layer,total_layers=3)
        self.items=[item];old=dict(self.tracker.running)
        self.tracker.update(self.items);self.service.observe(self.items,old)
        await asyncio.sleep(0);await asyncio.sleep(0)
        return item
    async def ready(self,**kwargs):
        await self.tick(**kwargs);await self.tick(**kwargs)
    async def test_initial_unknown_no_invented_spool_and_no_warning(self):
        await self.ready()
        view=self.service.public('one')
        self.assertIsNone(view['remaining_g']);self.assertEqual(view['job']['total_g'],300)
        self.assertEqual(self.events,[])
    async def test_auto_new_spool_only_runout_resume_and_no_repeat(self):
        await self.ready()
        await self.tick('paused',runout=True)
        self.assertTrue(self.service.public('one')['awaiting_resume'])
        await self.tick('paused',runout=False)
        self.assertEqual(self.service.public('one')['remaining_g'],0)
        await self.tick(layer=2)
        state=self.service.public('one');self.assertEqual(state['remaining_g'],900)
        self.assertEqual(state['origin'],'automatic');spool=state['spool_id']
        await self.tick(layer=2)
        self.assertEqual(self.service.public('one')['remaining_g'],900)
        self.assertEqual(self.service.public('one')['spool_id'],spool)
        await self.tick(layer=3)
        self.assertEqual(self.service.public('one')['remaining_g'],800)
    async def test_ordinary_pause_and_idle_sensor_do_not_replace(self):
        self.service.configure('one',{'remaining_g':500})
        await self.ready();spool=self.service.public('one')['spool_id']
        await self.tick('paused');await self.tick(layer=2)
        self.assertEqual(self.service.public('one')['spool_id'],spool)
        self.assertEqual(self.service.public('one')['remaining_g'],300)
    async def test_stale_signal_does_not_arm_and_other_job_does_not_reset(self):
        await self.ready();await self.tick('paused',runout=True,online=False)
        self.assertFalse(self.service.public('one')['awaiting_resume'])
        await self.tick('paused',runout=True);await self.tick(file='other.gcode')
        self.assertNotEqual(self.service.public('one')['origin'],'automatic')
    async def test_restart_preserves_checkpoint_and_pending_runout(self):
        self.service.configure('one',{'remaining_g':500})
        await self.ready();await self.service.stop();self.tracker.flush()
        self.tracker=ActivityTracker(self.history,lambda:self.now);self.make_service()
        await self.tick();self.assertEqual(self.service.public('one')['remaining_g'],400)
        await self.tick('paused',runout=True);await self.service.stop();self.tracker.flush()
        self.tracker=ActivityTracker(self.history,lambda:self.now);self.make_service()
        await self.tick(layer=2);self.assertEqual(self.service.public('one')['remaining_g'],900)
    async def test_warn_once_on_external_job_and_respects_corrected_weight(self):
        self.service.configure('one',{'remaining_g':150})
        await self.ready()
        self.assertEqual([event['kind'] for event in self.events],['filament_low'])
        self.assertIn('Остатка катушки',text(self.events[0]))
        await self.tick();self.assertEqual(len(self.events),1)
        self.service.configure('one',{'capacity_g':2000,'enabled':True})
        self.assertEqual(self.service.public('one')['remaining_g'],50)
        self.service.configure('one',{'remaining_g':700})
        await self.tick(layer=2);self.assertEqual(self.service.public('one')['remaining_g'],600)
    async def test_cancel_charges_observed_layers_not_entire_job(self):
        self.service.configure('one',{'remaining_g':1000})
        await self.ready();await self.tick('stopped')
        self.assertEqual(self.service.public('one')['remaining_g'],900)
    async def test_complete_debits_full_total_once(self):
        self.service.configure('one',{'remaining_g':1000})
        await self.ready();await self.tick('completed',layer=3);await self.tick('completed',layer=3)
        self.assertEqual(self.service.public('one')['remaining_g'],700)
    async def test_material_settings_between_jobs_and_disable_invalidates_missing_usage(self):
        await self.ready()
        with self.assertRaises(ValueError):self.service.configure('one',{'density':1.27})
        self.service.configure('one',{'enabled':False});await self.tick('paused',runout=True)
        self.service.configure('one',{'enabled':True})
        self.assertIsNone(self.service.public('one')['remaining_g'])
    async def test_changed_address_invalidates_old_spool(self):
        self.service.configure('one',{'remaining_g':1000});await self.ready()
        item=sample('printing');item['printer']['ip']='192.168.4.20';self.items=[item]
        old=dict(self.tracker.running);self.tracker.update(self.items);self.service.observe(self.items,old)
        self.assertIsNone(self.service.public('one')['remaining_g'])
    async def test_late_analysis_of_superseded_job_does_not_attach(self):
        gate=asyncio.Event()
        async def loader(pid,job):await gate.wait();return PROFILE
        self.service.loader=loader
        await self.tick();await self.tick(file='new.gcode');gate.set()
        await asyncio.sleep(0);await asyncio.sleep(0);await self.tick(file='new.gcode')
        self.assertEqual(self.service.public('one')['job']['file'],'new.gcode')

    async def test_short_same_file_native_job_change_is_a_new_ledger(self):
        self.service.configure('one',{'remaining_g':1000})
        await self.ready()
        previous=self.tracker.running['one']['id']
        item=sample('printing');item['print']['job_id']='one';item['connection']['sessions']=1
        self.tracker.update([item]);item['print']['job_id']='two'
        self.tracker.update([item])
        self.assertNotEqual(self.tracker.running['one']['id'],previous)

    async def test_profile_network_reads_only_and_repeat_uses_validated_cache(self):
        body=b'M83\n;LAYER_CHANGE\nG1 E100\n; filament used [g] = 5'
        entry={'path':'/usr/data/printer_data/gcodes/part.gcode','size_bytes':len(body),'modified':1700000000}
        async def listing(refresh=False):return types.SimpleNamespace(files=[entry])
        monitor=types.SimpleNamespace(list_files=listing,state=types.SimpleNamespace(host='192.168.4.10'))
        self.items=[sample('printing')];self.manager.get=lambda pid:monitor
        seen=[]
        def respond(request):
            seen.append(request.method);self.assertEqual(request.method,'GET')
            return httpx.Response(200,content=body,headers={'Content-Type':'text/plain'})
        client=httpx.AsyncClient
        def factory(**options):return client(transport=httpx.MockTransport(respond),**options)
        job={'endpoint':'creality|192.168.4.10|9999','file':'part.gcode'}
        with patch('k1monitor.filament.httpx.AsyncClient',factory):
            a=await self.service.read_profile('one',job);b=await self.service.read_profile('one',job)
        self.assertEqual(a,b);self.assertEqual(seen,['GET'])


class SpoolApiTests(unittest.TestCase):
    def test_csrf_endpoint_validation_remote_read_only_and_existing_preferences(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config.json').write_text(json.dumps({'printer_ip':'192.168.4.10','firmware':'test'}))
            config=ConfigStore(root/'config.json');manager=PrinterManager(config,factory=FakeMonitor)
            store=ActivityStore(root/'history.sqlite3');bot=Notifications(root/'notifications.json',store)
            activity=ActivityService(manager,bot)
            remote=RemoteAccess(root/'remote.json');remote.configure('pc.tail123.ts.net')
            app=create_app(manager,remote,activity=activity,csrf_token='test-token')
            pid=manager.config.printers[0]['id'];p=manager.entry(pid)
            ep='|'.join(str(p.get(key,'')) for key in ('protocol','ip','api_port'))
            path='/api/v1/printers/'+pid+'/filament';headers={'X-K1-Token':'test-token'}
            # No lifespan: fixtures must not start observation/network tasks.
            with TestClient(app,base_url='http://127.0.0.1:8765') as client:
                self.assertEqual(client.put(path,json={'expected_endpoint':ep,'remaining_g':900}).status_code,403)
                self.assertEqual(client.put(path,headers=headers,json={'expected_endpoint':'bad','remaining_g':900}).status_code,409)
                self.assertEqual(client.put(path,headers=headers,json={'expected_endpoint':ep,'remaining_g':900}).status_code,200)
                self.assertEqual(client.get(path).json()['remaining_g'],900)
                self.assertEqual(client.get('/api/v1/status').json()['printers'][0]['filament']['capacity_g'],1000)
                watch_path='/api/v1/printers/'+pid+'/temperature-watch'
                self.assertFalse(client.get(watch_path).json()['enabled'])
                self.assertEqual(client.put(watch_path,json={'expected_endpoint':ep,'enabled':True}).status_code,403)
                self.assertEqual(client.put(watch_path,headers=headers,json={'expected_endpoint':'bad','enabled':True}).status_code,409)
                self.assertEqual(client.put(watch_path,headers=headers,json={'expected_endpoint':ep,'enabled':True}).status_code,200)
                self.assertTrue(client.get(watch_path).json()['enabled'])
                self.assertEqual(client.put(watch_path,headers=headers,json={'expected_endpoint':ep,'enabled':False}).status_code,200)
                self.assertFalse(client.get(watch_path).json()['enabled'])
                for value in [True,-1,float('inf')]:
                    if value==float('inf'):continue
                    self.assertEqual(client.put(path,headers=headers,json={'expected_endpoint':ep,'capacity_g':value}).status_code,422)
                result=client.put(path,headers={**headers,'Host':remote.host,'Tailscale-User-Login':'owner@test'},json={'expected_endpoint':ep,'remaining_g':20})
                self.assertEqual(result.status_code,403)
            saved=Notifications(root/'notifications.json',store)
            self.assertIn('filament_low',saved.public()['events'])

if __name__=='__main__':unittest.main()
