"""Creality delta telemetry replay; pure decisions, no printer connections."""
import unittest
from k1monitor.telemetry import Telemetry
from k1monitor.operations import RunoutPlan,UnloadHeating
from k1monitor.client import PrinterMonitor
from k1monitor.moonraker import MoonrakerMonitor
from k1monitor.http_printers import OctoPrintMonitor

def state():
    result=Telemetry('192.168.254.250','fixture',persistent_status=True)
    result.begin_session()
    result.ingest({'state':1,'printFileName':'fixture.gcode','printId':'fixture',
                   'materialStatus':0,'err':{'errcode':0},'withSelfTest':100,
                   'nozzleTemp':200,'targetNozzleTemp':200},now=0)
    return result

class RunoutBugfixTests(unittest.TestCase):
    def test_error_and_pause_same_packet_accepts_completed_self_test(self):
        for code in (116,2839):
            with self.subTest(code=code):
                s=state();plan=RunoutPlan();plan.tick(s.snapshot(now=0),True,0)
                s.ingest({'state':5,'err':{'key':code}},now=1)
                self.assertTrue(s.snapshot(now=1)['paused_confirmed'])
                self.assertTrue(s.snapshot(now=1)['unload_eligible'])
                self.assertFalse(plan.tick(s.snapshot(now=1),True,1))
                self.assertFalse(plan.tick(s.snapshot(now=5.99),True,5.99))
                self.assertTrue(plan.tick(s.snapshot(now=6),True,6))
                self.assertFalse(plan.tick(s.snapshot(now=7),True,7))

    def test_pause_before_error_and_error_before_pause_need_no_second_pause_packet(self):
        for order in ('pause_first','error_first'):
            with self.subTest(order=order):
                s=state();plan=RunoutPlan();plan.tick(s.snapshot(now=0),True,0)
                first={'state':5} if order=='pause_first' else {'materialStatus':1}
                second={'materialStatus':1} if order=='pause_first' else {'state':5}
                s.ingest(first,now=1);self.assertFalse(plan.tick(s.snapshot(now=1),True,1))
                s.ingest(second,now=2);self.assertFalse(plan.tick(s.snapshot(now=2),True,2))
                s.ingest({'nozzleTemp':201},now=6)
                self.assertTrue(plan.tick(s.snapshot(now=7),True,7))

    def test_delta_status_remains_valid_during_long_heating_and_sends_once(self):
        s=state();plan=RunoutPlan();plan.tick(s.snapshot(now=0),True,0)
        s.ingest({'state':5,'materialStatus':1},now=1);plan.tick(s.snapshot(now=1),True,1)
        self.assertTrue(plan.tick(s.snapshot(now=6),True,6))
        heat=UnloadHeating();heat.start(s.snapshot(now=6),250,6)
        s.ingest({'targetNozzleTemp':250},now=7)
        for now,temp in [(7,205),(20,220),(40,246),(60,250)]:
            s.ingest({'nozzleTemp':temp},now=now)
            snap=s.snapshot(now=now)
            self.assertTrue(snap['unload_eligible'])
            self.assertFalse(heat.tick(snap,True,250,now))
            self.assertIsNotNone(heat.pending)
        s.ingest({'nozzleTemp':250},now=62)
        self.assertTrue(heat.tick(s.snapshot(now=62),True,250,62))
        self.assertFalse(heat.tick(s.snapshot(now=63),True,250,63))

    def test_new_positive_sensor_error_clear_resume_and_other_errors_take_effect(self):
        for update in ({'state':1},{'materialStatus':0,'err':{'errcode':0}},
                       {'err':{'key':201}},{'withSelfTest':50}):
            with self.subTest(update=update):
                s=state();s.ingest({'state':5,'materialStatus':1},now=1)
                heat=UnloadHeating();heat.start(s.snapshot(now=2),250,2)
                s.ingest(update,now=3)
                self.assertFalse(heat.tick(s.snapshot(now=3),True,250,3))
                self.assertIsNone(heat.pending)

    def test_target_change_cancels_and_old_actual_temperature_never_authorizes_unload(self):
        s=state();s.ingest({'state':5,'materialStatus':1,'targetNozzleTemp':250,'nozzleTemp':250},now=1)
        heat=UnloadHeating();heat.start(s.snapshot(now=1),250,1)
        self.assertFalse(heat.tick(s.snapshot(now=1),True,250,1))
        s.ingest({'bedTemp0':30},now=30)
        self.assertFalse(s.snapshot(now=30)['temperatures']['nozzle_fresh'])
        self.assertFalse(heat.tick(s.snapshot(now=30),True,250,30))
        s.ingest({'nozzleTemp':250,'targetNozzleTemp':200},now=31)
        self.assertFalse(heat.tick(s.snapshot(now=31),True,250,31))
        self.assertIsNone(heat.pending)

    def test_initial_error_reconnect_job_change_and_lost_telemetry_never_trigger(self):
        for change in ('startup','reconnect','job','stale','offline'):
            with self.subTest(change=change):
                s=state();plan=RunoutPlan()
                if change!='startup':plan.tick(s.snapshot(now=0),True,0)
                s.ingest({'state':5,'materialStatus':1},now=1)
                plan.tick(s.snapshot(now=1),True,1)
                if change=='reconnect':s.begin_session();s.ingest({'state':5,'printFileName':'fixture.gcode','materialStatus':1},now=2)
                if change=='job':s.ingest({'printId':'different'},now=2)
                if change=='offline':s.disconnected()
                if change=='stale':s.heartbeat(now=40)
                self.assertFalse(plan.tick(s.snapshot(now=40 if change=='stale' else 6),True,40 if change=='stale' else 6))

    def test_protocol_specific_delta_mode_and_other_adapters_stay_strict(self):
        self.assertTrue(PrinterMonitor('192.168.254.250','fixture').state.persistent_status)
        self.assertFalse(MoonrakerMonitor('192.168.254.250','fixture').state.persistent_status)
        self.assertFalse(OctoPrintMonitor('192.168.254.250','fixture').state.persistent_status)
        s=Telemetry('192.168.254.250','fixture');s.begin_session()
        s.ingest({'state':5,'materialStatus':1,'withSelfTest':100},now=0)
        s.ingest({'nozzleTemp':250},now=30)
        self.assertFalse(s.snapshot(now=30)['unload_eligible'])

    def test_completed_check_allowed_active_or_invalid_check_and_absent_pause_block(self):
        for self_test in (None,0,100,1,50,99,101):
            s=state();s.ingest({'state':5,'materialStatus':1,'withSelfTest':self_test},now=1)
            self.assertEqual(s.snapshot(now=1)['unload_eligible'],self_test in (None,0,100))
        s=state();s.ingest({'err':{'key':116}},now=1)
        self.assertFalse(s.snapshot(now=1)['paused_confirmed'])
        plan=RunoutPlan();s=state();plan.tick(s.snapshot(now=0),True,0)
        s.ingest({'state':5,'materialStatus':1,'withSelfTest':50},now=1)
        self.assertFalse(plan.tick(s.snapshot(now=6),True,6))
        self.assertIn('самопроверки',plan.message)
