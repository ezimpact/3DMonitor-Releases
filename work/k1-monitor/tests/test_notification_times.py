"""Zero time at startup is unknown ETA, never a completion date."""
import tempfile
from pathlib import Path
import unittest
from datetime import datetime
from k1monitor.activity import ActivityStore,ActivityTracker,ActivityService
from k1monitor.notifications import Notifications,text
from test_activity_notifications import sample

class NotificationTimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        self.now=1791753138;self.store=ActivityStore(root/'history.sqlite3');self.tracker=ActivityTracker(self.store,lambda:self.now)
        self.bot=Notifications(root/'notifications.json',self.store,clock=lambda:self.now)
    def start(self,remaining=0,elapsed=0):
        self.tracker.update([sample()]);item=sample('printing',elapsed=elapsed);item['print']['remaining_seconds']=remaining
        return self.tracker.update([item])[0]
    def test_zero_at_start_has_real_start_line_and_unknown_end(self):
        event=self.start();self.assertEqual(event['kind'],'started');self.assertEqual(event['started'],self.now);self.assertIsNone(event['remaining'])
        result=text(event);self.assertIn('Начало печати: '+datetime.fromtimestamp(self.now).astimezone().strftime('%d.%m %H:%M'),result);self.assertIn('принтер пока не сообщил',result)
        self.assertNotIn('Примерное окончание: '+datetime.fromtimestamp(self.now).astimezone().strftime('%d.%m %H:%M'),result)
    def test_positive_remaining_shows_start_and_actual_forecast(self):
        event=self.start(3600,60);self.assertEqual(event['started'],self.now-60);result=text(event)
        self.assertIn('Начало печати: '+datetime.fromtimestamp(self.now-60).astimezone().strftime('%d.%m %H:%M'),result)
        self.assertIn('Примерное окончание: '+datetime.fromtimestamp(self.now+3600).astimezone().strftime('%d.%m %H:%M'),result)
    def test_legacy_queued_zero_is_also_unknown_not_a_date(self):
        result=text({'kind':'started','created':self.now,'name':'K1C','remaining':0})
        self.assertIn('Начало печати:',result);self.assertIn('принтер пока не сообщил',result)
    def test_unknown_invalid_and_boolean_remaining_do_not_make_eta(self):
        for index,remaining in enumerate([None,-1,False,True,float('inf'),float('nan')]):
            self.tracker=ActivityTracker(ActivityStore(Path(self.temp.name)/f'isolated-{index}.sqlite3'),lambda:self.now)
            with self.subTest(remaining=remaining):self.assertIn('принтер пока не сообщил',text(self.start(remaining)))
    def test_status_zero_eta_unknown_becomes_available_when_printer_updates_it(self):
        item=sample('printing');item['print']['remaining_seconds']=0
        manager=type('Manager',(),{'snapshot':lambda _:{'printers':[item]}})();service=ActivityService(manager,self.bot)
        self.assertIn('Примерное окончание: неизвестно',service.status_reports()[0]['text'])
        item['print']['remaining_seconds']=3600;self.assertNotIn('окончание: неизвестно',service.status_reports()[0]['text'])
