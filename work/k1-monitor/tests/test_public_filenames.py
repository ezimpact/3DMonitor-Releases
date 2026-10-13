"""Display-only paths and anonymous updates: synthetic inputs, no printer requests."""
import tempfile
from pathlib import Path
import unittest
from k1monitor.activity import ActivityService, ActivityStore
from k1monitor.notifications import Notifications, text
from test_activity_notifications import sample

class FilenameTests(unittest.TestCase):
    def test_event_basename_in_both_locales_and_original_path_retained(self):
        name='Антонов_8h34m50s.gcode'
        for path in ['/usr/data/printer_data/gcodes/'+name, 'folder/subfolder/'+name, 'C:\\jobs\\'+name, name]:
            event={'kind':'completed','created':1791753138,'name':'Fixture','file':path}
            for translate in [lambda value:value,lambda value:{'Файл: ':'File: '}.get(value,value)]:
                result=text(event,translate)
                self.assertEqual(result.splitlines()[-1],name)
                self.assertNotIn('Файл:',result);self.assertNotIn('File:',result)
            self.assertEqual(event['file'],path)

    def test_status_shortens_display_but_retains_job_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=ActivityStore(root/'history.sqlite3');bot=Notifications(root/'notifications.json',store)
            path='/usr/data/printer_data/gcodes/Антонов_8h34m50s.gcode';item=sample('printing',file=path)
            manager=type('Manager',(),{'snapshot':lambda _:{'printers':[item]}})()
            service=ActivityService(manager,bot);result=service.status_reports()[0]['text']
            self.assertIn('\nАнтонов_8h34m50s.gcode\n',result);self.assertNotIn('/usr/data',result);self.assertNotIn('Файл:',result)
            self.assertEqual(item['print']['file'],path)
