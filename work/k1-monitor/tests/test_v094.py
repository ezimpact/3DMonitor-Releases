"""Pure heating decisions and temporary preferences; no dispatched G-code."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from k1monitor.operations import UnloadHeating,temperature_params,unload_gcode
from k1monitor.config import ConfigStore
from k1monitor.remote import RemoteAccess
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app
from fastapi.testclient import TestClient
from test_monitor import FakeMonitor

def snapshot():
    return {'printer':{'ip':'192.168.4.104'},'connection':{'sessions':1,'online':True,'stale':False},'print':{'file':'one.gcode','job_id':'one'},'filament_runout':True,'paused_confirmed':True,'unload_eligible':True,'temperatures':{'nozzle':210,'nozzle_target':240,'nozzle_fresh':True}}

class HeatingTests(unittest.TestCase):
    def test_waits_for_temperature_confirmation_and_stability_then_once(self):
        s=snapshot();plan=UnloadHeating();plan.start(s,240,0)
        self.assertFalse(plan.tick(s,True,240,1));s['temperatures']['nozzle']=240
        self.assertFalse(plan.tick(s,True,240,2));self.assertFalse(plan.tick(s,True,240,3.9));self.assertTrue(plan.tick(s,True,240,4));self.assertFalse(plan.tick(s,True,240,8))
    def test_cancel_on_disable_temperature_job_session_ip_or_connection_change(self):
        for change in ['disable','temperature','job','session','ip','offline','stale','resume','runout','other_error']:
            s=snapshot();plan=UnloadHeating();plan.start(s,240,0);enabled=True;temperature=240
            if change=='disable':enabled=False
            if change=='temperature':temperature=250
            if change=='job':s['print']['job_id']='two'
            if change=='session':s['connection']['sessions']=2
            if change=='ip':s['printer']['ip']='192.168.4.105'
            if change=='offline':s['connection']['online']=False
            if change=='stale':s['connection']['stale']=True
            if change=='resume':s['paused_confirmed']=False
            if change=='runout':s['filament_runout']=False
            if change=='other_error':s['unload_eligible']=False
            self.assertFalse(plan.tick(s,enabled,temperature,5),change);self.assertIsNone(plan.pending,change)
    def test_unconfirmed_or_changed_target_and_timeout_cancel(self):
        for mode in ['unconfirmed','changed','timeout']:
            s=snapshot();plan=UnloadHeating();plan.start(s,240,0)
            if mode=='changed':plan.tick(s,True,240,1)
            if mode!='timeout':s['temperatures']['nozzle_target']=200
            self.assertFalse(plan.tick(s,True,240,601 if mode=='timeout' else 16));self.assertIsNone(plan.pending)
    def test_stale_temperature_or_overshoot_cannot_trigger_withdrawal(self):
        for stale,temp in [(True,240),(False,260),(False,220)]:
            s=snapshot();s['temperatures'].update(nozzle=temp,nozzle_fresh=not stale);plan=UnloadHeating();plan.start(s,240,0)
            self.assertFalse(plan.tick(s,True,240,1));self.assertFalse(plan.tick(s,True,240,10));self.assertIsNotNone(plan.pending)
    def test_stock_temperature_keys_bounds_and_strict_integers(self):
        self.assertEqual(temperature_params('nozzle',245),{'nozzleTempControl':245});self.assertEqual(temperature_params('bed',80),{'bedTempControl':{'num':0,'val':80}})
        for kind,value in [('nozzle',301),('nozzle',-1),('bed',101),('bed',True),('nozzle','240')]:
            with self.assertRaises(ValueError):temperature_params(kind,value)
    def test_withdrawal_program_has_no_heating_xyz_homing_or_resume(self):
        program=unload_gcode().splitlines()
        self.assertTrue(program[0].startswith('SAVE_GCODE_STATE'));self.assertTrue(program[-1].startswith('RESTORE_GCODE_STATE'))
        for line in program:
            self.assertNotIn('M109',line);self.assertNotIn('M104',line);self.assertNotIn('RESUME',line);self.assertNotIn('G28',line)
            if line.startswith('G1 '):self.assertTrue(all(part[0] in 'EF' for part in line.split()[1:]))

class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.path=self.root/'config.json';self.path.write_text(json.dumps({'printers':[{'id':'one','name':'One','ip':'192.168.4.104','auto_unload':True}]}));self.store=ConfigStore(self.path,portable=True)
    def test_temperature_roundtrip_does_not_change_enabled_or_other_preferences(self):
        self.store.update('one',auto_unload_temperature=255)
        p=ConfigStore(self.path,portable=True).printers[0];self.assertEqual(p['auto_unload_temperature'],255);self.assertTrue(p['auto_unload'])
        with self.assertRaises(ValueError):self.store.update('one',auto_unload_temperature=170)
    def test_rename_preserves_session_hash_identity_and_expiry(self):
        remote=RemoteAccess(self.root/'remote.json');remote.configure('pc.tail12345.ts.net');code=remote.pair_code()['code'];token=remote.pair(code,'Old','identity');before=copy.deepcopy(remote.devices)
        remote.rename(before[0]['id'],'Новое устройство')
        self.assertTrue(remote.authorize(token,'identity'))
        saved=RemoteAccess(remote.path).devices[0];self.assertEqual(saved['name'],'Новое устройство')
        for key in ['id','hash','identity','expires']:self.assertEqual(saved[key],before[0][key])
        for name in ['','x\n','x'*65]:
            with self.assertRaises(ValueError):remote.rename(saved['id'],name)
        with self.assertRaises(ValueError):remote.rename('missing','Name')
    def test_rename_api_requires_csrf_and_is_local_only(self):
        remote=RemoteAccess(self.root/'remote.json');remote.configure('pc.tail12345.ts.net');remote.pair(remote.pair_code()['code'],'Old','identity');device=remote.devices[0]['id']
        manager=PrinterManager(self.store,factory=FakeMonitor);app=create_app(manager,remote);headers={'X-K1-Token':app.state.csrf_token};path='/api/v1/remote/devices/'+device
        with TestClient(app,base_url='http://127.0.0.1:8765') as client:
            self.assertEqual(client.patch(path,json={'name':'New'}).status_code,403)
            reply=client.patch(path,json={'name':'New'},headers=headers);self.assertEqual(reply.status_code,200);self.assertEqual(reply.json()['devices'][0]['name'],'New');self.assertNotIn('hash',reply.text)
        with TestClient(app,base_url='https://pc.tail12345.ts.net',headers={'Tailscale-User-Login':'identity'}) as client:
            self.assertEqual(client.patch(path,json={'name':'Remote'},headers=headers).status_code,403)
        self.assertEqual(remote.devices[0]['name'],'New')
    def test_temperature_setting_api_persists_without_dispatching_commands(self):
        manager=PrinterManager(self.store,factory=FakeMonitor);app=create_app(manager);headers={'X-K1-Token':app.state.csrf_token}
        with TestClient(app,base_url='http://127.0.0.1:8765') as client:
            reply=client.put('/api/v1/printers/one/auto-unload',json={'temperature':250},headers=headers);self.assertEqual(reply.status_code,200);self.assertTrue(reply.json()['enabled'])
            for value in [179,301,True,'250']:
                self.assertEqual(client.put('/api/v1/printers/one/auto-unload',json={'temperature':value},headers=headers).status_code,422)
        self.assertEqual(ConfigStore(self.path,portable=True).printers[0]['auto_unload_temperature'],250)
