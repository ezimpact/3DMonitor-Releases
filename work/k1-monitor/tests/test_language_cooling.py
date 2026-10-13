import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from k1monitor.config import ConfigStore
from k1monitor.language import LanguageSettings, translate
from k1monitor.manager import PrinterManager
from k1monitor.operations import UnloadHeating, unload_gcode
from k1monitor.remote import RemoteAccess
from k1monitor.server import create_app
import test_monitor as fixtures
import test_v094 as heating_fixtures

class LanguageTests(unittest.TestCase):
    def test_windows_display_language_not_regional_or_keyboard_settings(self):
        from k1monitor.language import windows_language
        for identifier,expected in ((0x0419,'ru'),(0x0409,'en'),(0x0809,'en'),(0x0407,'en')):
            kernel=MagicMock();kernel.GetUserDefaultUILanguage.return_value=identifier
            with patch('k1monitor.language.os.name','nt'),patch('k1monitor.language.ctypes.WinDLL',return_value=kernel):
                self.assertEqual(windows_language(),expected)

    def test_english_notifications_preserve_printer_and_file_names(self):
        from k1monitor.notifications import text
        event={'kind':'started','name':'Сопло','file':'Ошибка.gcode','created':1700000000,'remaining':None}
        message=text(event,lambda value:translate(value,'en'))
        self.assertTrue(message.startswith('Print started — Сопло'))
        self.assertIn('Ошибка.gcode',message)
        self.assertIn('Estimated finish:',message)

    def test_telegram_status_provider_translates_labels_without_touching_names(self):
        from types import SimpleNamespace
        from k1monitor.telemetry import Telemetry
        from k1monitor.activity import ActivityService
        state=Telemetry('192.168.254.250','fixture');state.begin_session();state.ingest({'state':1,'printFileName':'Ошибка.gcode','nozzleTemp':200})
        item=state.snapshot();item['printer'].update(id='fixture',name='Сопло');item['camera']={'present':False}
        service=SimpleNamespace(manager=SimpleNamespace(snapshot=lambda:{'printers':[item],'printer_order':['fixture']}),notifications=SimpleNamespace(clock=lambda:1700000000,translate=lambda value:translate(value,'en')),tracker=SimpleNamespace(running={}))
        report=ActivityService.status_reports(service)[0]
        self.assertEqual(report['name'],'Сопло')
        self.assertIn('Сопло — Printing',report['text'])
        self.assertIn('Ошибка.gcode',report['text'])

    def test_windows_default_manual_choice_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp, patch('k1monitor.language.windows_language',return_value='en'):
            path=Path(tmp)/'data/language.json'
            settings=LanguageSettings(path)
            self.assertEqual(settings.language,'en')
            settings.save('ru')
            self.assertEqual(LanguageSettings(path).language,'ru')
            before=path.read_bytes()
            with self.assertRaises(ValueError):settings.save('de')
            self.assertEqual(path.read_bytes(),before)

    def test_catalog_placeholders_do_not_translate_dynamic_names(self):
        self.assertEqual(translate('Выгрузка через 5 с','en'),'Unload in 5 s')
        self.assertEqual(translate('Загружаем «Ошибка» на Принтер…','en'),'Uploading “Ошибка” to Принтер…')
        self.assertEqual(translate('Ошибка прошивки F1234','en'),'Ошибка прошивки F1234')

    def test_language_api_csrf_remote_guard_and_independent_support_pages(self):
        with tempfile.TemporaryDirectory() as tmp, patch('k1monitor.language.windows_language',return_value='ru'):
            config_path=Path(tmp)/'config.json';config_path.write_text('{"printers":[]}',encoding='utf-8')
            manager=PrinterManager(ConfigStore(config_path),factory=fixtures.FakeMonitor)
            remote=RemoteAccess(Path(tmp)/'remote.json')
            app=create_app(manager,remote,csrf_token='test-language-token')
            with TestClient(app,base_url='http://localhost') as client:
                original=config_path.read_bytes()
                self.assertEqual(client.put('/api/v1/language',json={'language':'en'}).status_code,403)
                self.assertEqual(client.put('/api/v1/language',json={'language':'en'},headers={'X-K1-Token':'test-language-token','Origin':'http://evil.test'}).status_code,403)
                ru=client.get('/').text
                self.assertIn('yoomoney.ru/to/',ru)
                headers={'X-K1-Token':'test-language-token','Origin':'http://localhost'}
                self.assertEqual(client.put('/api/v1/language',json={'language':'en'},headers=headers).status_code,200)
                english=client.get('/').text
                from k1monitor import __version__
                self.assertIn('<small>'+__version__+'</small>',english)
                self.assertNotIn('__VERSION__',english)
                self.assertIn('EZIMPACT',english)
                self.assertIn('mailto:ezimpactpsn@gmail.com',english)
                self.assertIn('<html lang="en">',english)
                self.assertIn('Support via lava.top',english)
                self.assertNotIn('yoomoney.ru/to/',english)
                self.assertNotIn('finance.ozon.ru',english)
                self.assertIn('id="language-open"',english)
                self.assertEqual(config_path.read_bytes(),original)
                client.cookies.set('3dm-language','ru')
                self.assertIn('yoomoney.ru/to/',client.get('/').text)
                self.assertEqual(manager.language_settings.language,'en')
                client.cookies.set('3dm-language','bad<script>')
                self.assertIn('<html lang="en">',client.get('/').text)
                # The remote middleware denies host language changes before session authorization.
                remote.host='fixture.tail12345.ts.net'
                response=client.put('https://fixture.tail12345.ts.net/api/v1/language',json={'language':'ru'},headers={'X-K1-Token':'test-language-token'})
                self.assertEqual(response.status_code,403)
                self.assertEqual(manager.language_settings.language,'en')

class CoolingTests(unittest.TestCase):
    def test_captures_target_before_heating_and_restores_after_moves(self):
        s=heating_fixtures.snapshot();s['temperatures']['nozzle_target']=140
        heat=UnloadHeating();heat.start(s,240,0)
        s['temperatures'].update(nozzle=240,nozzle_target=240)
        self.assertFalse(heat.tick(s,True,240,1))
        self.assertTrue(heat.tick(s,True,240,3))
        self.assertEqual(heat.restore_temperature,140)
        program=unload_gcode(heat.restore_temperature).splitlines()
        self.assertEqual(program[-1],'M104 S140')
        self.assertLess(program.index('M400'),program.index('M104 S140'))
        self.assertLess(program.index('RESTORE_GCODE_STATE NAME=K1MONITOR_UNLOAD'),program.index('M104 S140'))
        self.assertFalse(any('RESUME' in line or 'M140' in line for line in program))

    def test_target_zero_is_valid_no_unknown_temperature_is_guessed(self):
        for target in (0,140,150,190.5):
            self.assertEqual(unload_gcode(target).splitlines()[-1],f'M104 S{target:g}')
        for target in (True,-1,301,float('nan'),float('inf'),'140'):
            with self.assertRaises(ValueError):unload_gcode(target)

    def test_cancellation_clears_restore_target(self):
        s=heating_fixtures.snapshot();s['temperatures']['nozzle_target']=140
        heat=UnloadHeating();heat.start(s,240,0)
        changed=copy.deepcopy(s);changed['print']['job_id']='other'
        self.assertFalse(heat.tick(changed,True,240,3))
        self.assertIsNone(heat.restore_temperature)

    def test_creality_refuses_unknown_or_stale_previous_target(self):
        from k1monitor.client import PrinterMonitor
        monitor=PrinterMonitor('192.168.254.250','fixture')
        s=heating_fixtures.snapshot()
        for target,fresh in ((None,True),(float('nan'),True),(140,False)):
            s['temperatures'].update(nozzle_target=target,nozzle_fresh=fresh)
            self.assertFalse(monitor._can_restore_unload_temperature(s))
        with self.assertRaises(ValueError):monitor._unload_gcode()

    def test_moonraker_keeps_the_macro_cooling_policy(self):
        from k1monitor.moonraker import MoonrakerMonitor
        monitor=MoonrakerMonitor('192.168.254.251','fixture')
        monitor.objects=['gcode_macro UNLOAD_FILAMENT']
        self.assertTrue(monitor._can_restore_unload_temperature({}))
        program=monitor._unload_gcode()
        self.assertIn('UNLOAD_FILAMENT',program)
        self.assertNotIn('M104',program)
        self.assertNotIn('140',program)
