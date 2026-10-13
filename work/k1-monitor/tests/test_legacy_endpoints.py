"""Legacy registry defaults must agree with telemetry, without hardware commands."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from copy import deepcopy
from k1monitor.activity import ActivityService,ActivityStore,ActivityTracker,endpoint,same_endpoint
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.notifications import Notifications
from test_monitor import FakeMonitor
from test_activity_notifications import sample
import test_status_history as fixtures
from test_relay import JPEG

class EndpointTests(unittest.TestCase):
    def test_legacy_defaults_equivalent_to_explicit_defaults(self):
        for left,right in [('|192.168.4.10|','creality|192.168.4.10|9999'),('creality|192.168.4.10|','creality|192.168.4.10|9999'),('moonraker|192.168.4.10|','moonraker|192.168.4.10|7125'),('octoprint|192.168.4.10|','octoprint|192.168.4.10|80'),('prusalink|192.168.4.10|','prusalink|192.168.4.10|80')]:
            with self.subTest(left=left):self.assertTrue(same_endpoint(left,right));self.assertTrue(same_endpoint(right,left))
    def test_actual_ip_protocol_and_custom_port_changes_still_rejected(self):
        original='|192.168.4.10|'
        for other in ['creality|192.168.4.11|9999','creality|192.168.4.10|9998','moonraker|192.168.4.10|9999','octoprint|192.168.4.10|80','old','']:
            with self.subTest(other=other):self.assertFalse(same_endpoint(original,other))
        self.assertFalse(same_endpoint('moonraker|192.168.4.10|80','moonraker|192.168.4.10|'))

class LegacyCameraTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        (root/'config.json').write_text(json.dumps({'printers':[{'id':str(i),'name':'P'+str(i),'ip':'192.168.254.'+str(10+i),**({'protocol':'creality','api_port':9999} if i==2 else {})} for i in range(3)]}))
        self.manager=PrinterManager(ConfigStore(root/'config.json'),factory=FakeMonitor);self.items={}
        for pid,monitor in self.manager.monitors.items():
            item=sample('printing',pid=pid);item['printer']={'ip':self.manager.entry(pid)['ip'],'protocol':'creality'};item.update(camera={'present':True},light_on=False,capabilities={'light':True},controls={'start':False});item['connection']['sessions']=1
            self.items[pid]=item;monitor.snapshot=lambda item=item:deepcopy(item);monitor.stock_command=AsyncMock(return_value={'phase':'sent'})
        self.sent=[]
        def request(token,method,body):self.sent.append((method,body));return {}
        self.bot=Notifications(root/'notifications.json',ActivityStore(root/'history.sqlite3'),request=request);self.bot.token='fixture';self.bot.data.update(telegram=True,chats=[{'id':7}]);self.bot.status_retry_delay=0
        self.relay=type('Relay',(),{'snapshot':AsyncMock(return_value=JPEG)})();self.service=ActivityService(self.manager,self.bot,self.relay)
    async def test_real_manager_legacy_registry_and_telemetry_send_every_camera(self):
        self.assertNotIn('protocol',self.manager.entry('0'));report=self.service.status_reports()[0]
        self.assertNotEqual(endpoint(self.manager.entry('0')),report['endpoint']) # The previous defect.
        await self.bot.reply_status(7,'fixture')
        self.assertEqual([m for m,b in self.sent],['sendPhoto']*3);self.assertEqual([c.args[0] for c in self.relay.snapshot.await_args_list],['0','1','2'])
        self.assertTrue(all(b['caption'].startswith(f'[{i+1}/3] P{i}') for i,(m,b) in enumerate(self.sent)))
    async def test_changed_address_before_capture_never_subscribes(self):
        report=self.service.status_reports()[0];self.manager.entry('0')['ip']='192.168.254.200'
        self.assertIsNone(await self.service.status_photo('0',report['endpoint']));self.relay.snapshot.assert_not_awaited()
    async def test_changed_protocol_or_port_during_capture_discards_frame(self):
        for field,value in [('protocol','moonraker'),('api_port',9998)]:
            report=self.service.status_reports()[0]
            async def capture(pid):self.manager.entry(pid)[field]=value;return JPEG
            self.relay.snapshot.side_effect=capture
            self.assertIsNone(await self.service.status_photo('0',report['endpoint']));self.manager.entry('0').pop(field)
    async def test_replaced_monitor_during_capture_discards_frame(self):
        report=self.service.status_reports()[0]
        async def capture(pid):self.manager.monitors[pid]=FakeMonitor('192.168.254.10','test');return JPEG
        self.relay.snapshot.side_effect=capture;self.assertIsNone(await self.service.status_photo('0',report['endpoint']))
    async def test_materializing_defaults_during_capture_does_not_discard_frame(self):
        report=self.service.status_reports()[0]
        async def capture(pid):self.manager.entry(pid).update(protocol='creality',api_port=9999);return JPEG
        self.relay.snapshot.side_effect=capture;self.assertEqual(await self.service.status_photo('0',report['endpoint']),JPEG)
    async def test_common_light_accepts_legacy_connection_with_session_guard(self):
        results=await self.service.set_lights(True,'fixture',lambda:True)
        self.assertEqual(len(results),3)
        for monitor in self.manager.monitors.values():self.assertEqual(monitor.stock_command.await_count,1);self.assertEqual(monitor.stock_command.call_args.kwargs['action'],'light')
    async def test_running_job_survives_equivalent_defaults_without_rewriting_record_id(self):
        item=sample('printing');tracker=ActivityTracker(self.bot.store);tracker.update([item]);job=tracker.running['one'];record_id=job['id'];original=job['endpoint']
        item['printer'].pop('protocol');item['printer'].pop('api_port');self.assertEqual(tracker.update([item]),[]);self.assertEqual(tracker.running['one']['id'],record_id);self.assertEqual(job['endpoint'],original)

class LegacyHistoryTests(unittest.TestCase):
    setUp=fixtures.HistoryReplayTests.setUp
    def test_history_telemetry_endpoint_accepted_with_legacy_registry(self):
        self.assertNotIn('protocol',self.manager.entry('one'));self.store.save({**self.record,'endpoint':'creality|192.168.254.10|9999'})
        response=self.client.get(self.url);self.assertEqual(response.status_code,200);self.assertEqual(self.monitor.list_files.await_count,1)
    def test_history_custom_port_or_protocol_remains_rejected(self):
        for value in ['creality|192.168.254.10|9998','moonraker|192.168.254.10|9999']:
            self.store.save({**self.record,'endpoint':value});self.assertEqual(self.client.get(self.url).status_code,409)
        self.monitor.list_files.assert_not_awaited()
