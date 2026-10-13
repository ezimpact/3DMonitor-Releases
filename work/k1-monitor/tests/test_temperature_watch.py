import copy
import types
import unittest

from k1monitor.temperature_watch import TemperatureWatch
from k1monitor.notifications import text
from k1monitor.language import translate


def sample(actual=240,target=240,fresh=True,online=True,session=1):
    return {'printer':{'id':'one','name':'K1 Max','ip':'192.168.4.10','protocol':'creality'},
            'connection':{'online':online,'stale':not online,'sessions':session},
            'temperatures':{'nozzle':actual,'nozzle_target':target,'nozzle_fresh':fresh},
            'print':{'file':'part.gcode'}}


class TemperatureTests(unittest.TestCase):
    def setUp(self):
        self.now=0;self.events=[]
        self.watch=TemperatureWatch(types.SimpleNamespace(enqueue=self.events.append),clock=lambda:self.now,wall_clock=lambda:1700000000+self.now)
        self.watch.configure('one',True)
    def tick(self,actual=240,seconds=5,**kwargs):
        self.now+=seconds;self.watch.update([sample(actual,**kwargs)])
    def stable(self):
        for _ in range(3):self.tick()
    def test_checks_at_five_second_intervals(self):
        self.tick(240);last=self.watch.last_tick
        self.tick(10,seconds=1);self.assertEqual(self.watch.last_tick,last)
        self.assertEqual(self.events,[])
    def test_normal_warmup_and_target_changes_are_not_failures(self):
        for actual in (20,60,100,180,225,237,240,240):self.tick(actual)
        self.assertEqual(self.events,[])
        for _ in range(12):self.tick(240,target=270)
        self.assertEqual(self.events,[])
    def test_persistent_deviation_needs_thirty_seconds_and_alerts_once(self):
        self.stable()
        for _ in range(6):self.tick(229)
        self.assertEqual(self.events,[])
        self.tick(229);self.assertEqual(len(self.events),1)
        for _ in range(10):self.tick(229)
        self.assertEqual(len(self.events),1)
        self.assertEqual(self.events[0]['temperature_kind'],'deviation')
    def test_one_jump_not_alarm_but_repeated_jumps_trigger(self):
        self.stable();self.tick(220);self.assertEqual(self.events,[])
        self.tick(240);self.assertEqual(len(self.events),1)
        self.assertEqual(self.events[0]['temperature_kind'],'jumps')
    def test_small_pid_variation_not_alarm(self):
        self.stable()
        for _ in range(8):
            for actual in (237,244,239,242):self.tick(actual)
        self.assertEqual(self.events,[])
    def test_stale_reading_offline_and_new_session_break_continuity(self):
        self.stable();self.tick(220);self.tick(220,fresh=False);self.tick(240)
        self.assertEqual(self.events,[])
        self.stable();self.tick(220);self.tick(240,session=2)
        self.assertEqual(self.events,[])
        self.tick(0,online=False);self.assertFalse(self.watch.public('one')['active'])
    def test_same_sample_cannot_accumulate_a_false_alarm(self):
        self.stable()
        item=sample(220);item['temperatures']['nozzle_sample']=42
        for _ in range(12):self.now+=5;self.watch.update([item])
        self.assertEqual(self.events,[])
    def test_off_and_cooling_target_disable_monitoring(self):
        self.stable();self.tick(170,target=0)
        self.assertEqual(self.watch.public('one')['phase'],'off')
        self.assertEqual(self.events,[])
    def test_heater_never_reaches_target_gets_delayed_warning(self):
        for _ in range(60):self.tick(80)
        self.assertEqual(self.events,[])
        self.tick(80);self.assertEqual(len(self.events),1)
        self.assertEqual(self.events[0]['temperature_kind'],'not_heated')
    def test_recovery_rearms_for_a_new_episode(self):
        self.stable();self.tick(220);self.tick(240)
        for _ in range(20):self.tick(240)
        self.assertFalse(self.watch.public('one')['active'])
        self.tick(220);self.tick(240);self.assertEqual(len(self.events),2)
    def test_printers_do_not_share_alarm_state(self):
        self.stable()
        other=sample(220);other['printer']['id']='two'
        self.now+=5;self.watch.update([sample(),other])
        self.assertEqual(self.events,[])
    def test_alert_text_is_cautious_and_translates(self):
        self.stable();self.tick(220);self.tick(240)
        message=text(self.events[0]);self.assertIn('а не диагноз',message)
        translated=text(self.events[0],lambda value:translate(value,'en'))
        # This test is run after the bilingual UI catalog is updated.
        self.assertIn('temperature',translated.lower())

    def test_default_disabled_requires_explicit_opt_in(self):
        watch=TemperatureWatch(types.SimpleNamespace(enqueue=self.events.append),clock=lambda:self.now)
        for _ in range(100):self.now+=5;watch.update([sample(80)])
        self.assertEqual(self.events,[]);self.assertFalse(watch.public('one')['enabled'])
        self.assertEqual(watch.public('one')['phase'],'disabled');self.assertEqual(watch.states,{})

    def test_per_printer_choice_persists_and_disabling_clears_alarm(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'temperature-watch.json'
            watch=TemperatureWatch(types.SimpleNamespace(enqueue=self.events.append),path)
            watch.configure('one',True)
            loaded=TemperatureWatch(types.SimpleNamespace(enqueue=self.events.append),path)
            self.assertTrue(loaded.public('one')['enabled']);self.assertFalse(loaded.public('two')['enabled'])
            loaded.views['one']={'active':True,'kind':'jumps'}
            loaded.configure('one',False)
            self.assertFalse(loaded.public('one')['active']);self.assertFalse(loaded.public('one')['enabled'])

if __name__=='__main__':unittest.main()
