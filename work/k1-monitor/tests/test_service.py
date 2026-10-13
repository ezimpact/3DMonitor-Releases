"""Service lifecycle checks use an empty registry; no printer commands."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler

from fastapi.testclient import TestClient
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.remote import RemoteAccess
from k1monitor.server import create_app
from k1monitor.service_control import ServiceControl
from k1monitor.tray import InstanceLease, WindowsTray, MENU
import run_monitor


class ServiceTests(unittest.TestCase):
    def test_restart_coalesces_and_stop_takes_priority(self):
        control=ServiceControl()
        server=SimpleNamespace(started=True,should_exit=False)
        control.attach(server)
        self.assertEqual(control.status,'Работает')
        self.assertTrue(control.request('restart'))
        self.assertFalse(control.request('restart'))
        self.assertTrue(server.should_exit)
        self.assertTrue(control.request('stop'))
        self.assertFalse(control.request('restart'))
        self.assertFalse(control.next_cycle())
        self.assertEqual(control.status,'Остановка')

    def test_request_before_server_exists_is_not_lost(self):
        control=ServiceControl()
        control.request('stop')
        server=SimpleNamespace(started=False,should_exit=False)
        control.attach(server)
        self.assertTrue(server.should_exit)
        with self.assertRaises(ValueError):control.request('pause')

    def test_cleanup_finishes_before_next_cycle(self):
        control=ServiceControl();events=[]
        class Server:
            started=False;should_exit=False
            def run(self):
                events.append('start')
                control.request('restart' if len(events)==2 else 'stop')
                events.append('cleanup')
        def factory():events.append('create');return Server()
        run_monitor.run_service(factory,control)
        self.assertEqual(events,['create','start','cleanup','create','start','cleanup'])

    def test_open_and_service_actions_are_separate(self):
        opened=threading.Event();control=ServiceControl()
        tray=WindowsTray(control,opened.set)
        tray.activate(1001);self.assertTrue(opened.wait(2));self.assertIsNone(control.action)
        tray.activate(1002);self.assertEqual(control.action,'restart')
        tray.activate(1003);self.assertEqual(control.action,'stop')
        tray.activate(9999);self.assertEqual(control.action,'stop')

    def test_screensaver_menu_and_actions_are_removed(self):
        control=ServiceControl();tray=WindowsTray(control,lambda:None)
        tray.activate(1004);tray.activate(1005);self.assertIsNone(control.action)
        self.assertFalse(any('заставк' in entry[1] for entry in MENU))

    def test_restart_preserves_session_sequence_and_rereads_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            path.write_text(json.dumps({'printers':[{'id':'one','name':'Before','ip':'192.168.254.250'}]}))
            config=ConfigStore(path);remote=RemoteAccess(Path(directory)/'remote.json')
            control=ServiceControl();managers=[]
            real_app=create_app
            def app_factory(manager,*args,**kwargs):
                managers.append(manager)
                return real_app(manager,*args,**kwargs)
            class Server:
                started=False;should_exit=False
                def __init__(self,config):self.config=config
                def run(self):
                    # No lifespan is started, hence no network or commands.
                    monitor=managers[-1].monitors['one']
                    if len(managers)==1:
                        monitor.state.connection_count=8
                        data=json.loads(path.read_text());data['printers'][0]['name']='After'
                        path.write_text(json.dumps(data));control.request('restart')
                    else:
                        self_test.assertEqual(monitor.state.connection_count,8)
                        monitor.state.begin_session()
                        self_test.assertEqual(monitor.state.connection_count,9)
                        self_test.assertEqual(managers[-1].config.printers[0]['name'],'After')
                        control.request('stop')
            self_test=self
            args=SimpleNamespace(config=path,no_browser=True,no_tray=True,port=62002)
            with patch.object(run_monitor,'ServiceControl',return_value=control),patch.object(run_monitor,'create_app',side_effect=app_factory),patch.object(run_monitor.uvicorn,'Server',Server):
                run_monitor.serve(path,args,'http://127.0.0.1:62002/',config,remote)
            self.assertEqual(len(managers),2)

    def test_csrf_supplied_to_new_app_is_used_by_page_and_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';path.write_text('{"printers":[]}')
            manager=PrinterManager(ConfigStore(path))
            with TestClient(create_app(manager,csrf_token='stable-token'),base_url='http://127.0.0.1') as client:
                self.assertIn('stable-token',client.get('/').text)
                # Invalid request is harmless; 422 proves the CSRF guard accepted
                # the existing browser token without executing any action.
                response=client.post('/api/v1/printers',json={},headers={'X-K1-Token':'stable-token','Origin':'http://127.0.0.1'})
                self.assertEqual(response.status_code,422)

    @unittest.skipUnless(os.name=='nt','Windows native notification area')
    def test_native_icon_menu_and_removal(self):
        tray=WindowsTray(ServiceControl(),lambda:None,title='K1Monitor test')
        try:
            tray.start();state=tray.inspect()
            self.assertTrue(state['registered'])
            self.assertTrue(state['icon_rect_available'])
            self.assertEqual(state['menu'],[item[1] for item in MENU])
            self.assertIsNone(tray.error)
        finally:tray.stop()
        self.assertFalse(tray.registered)
        self.assertFalse(tray.thread.is_alive())

    @unittest.skipUnless(os.name=='nt','Windows process lease')
    def test_lease_blocks_other_owner_through_restart_gap(self):
        first=InstanceLease(62001);results=[]
        def duplicate():
            other=InstanceLease(62001);results.append(other.owned);other.close()
        try:
            self.assertTrue(first.owned)
            worker=threading.Thread(target=duplicate);worker.start();worker.join(2)
            self.assertEqual(results,[False])
        finally:first.close()
        last=InstanceLease(62001)
        try:self.assertTrue(last.owned)
        finally:last.close()

    def test_running_server_restarts_with_fresh_settings_and_keeps_browser_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';path.write_text('{"printers":[],"poll_seconds":5}')
            config=ConfigStore(path);remote=RemoteAccess(Path(directory)/'remote.json')
            remote.configure('test.tail12345.ts.net')
            token=remote.pair(remote.pair_code()['code'],'Test device','test@example.com')
            saved=remote.path.read_bytes()
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            url=f'http://127.0.0.1:{port}/';opener=build_opener(ProxyHandler({}))
            captured=[];servers=[];errors=[]
            real_server=run_monitor.uvicorn.Server
            class CapturedTray(WindowsTray):
                def start(self):captured.append(self)
                def stop(self):pass
            def server_factory(config):
                server=real_server(config);servers.append(server);return server
            def execute():
                try:run_monitor.serve(path,SimpleNamespace(config=path,no_browser=True,no_tray=False,port=port),url,config,remote)
                except BaseException as exc:errors.append(exc)
            def wait_for(predicate):
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    if predicate():return
                    time.sleep(.05)
                self.fail(f'Service timeout: {errors}')
            def get_page():
                with opener.open(url,timeout=3) as response:return response.read()
            with patch.object(run_monitor,'WindowsTray',CapturedTray),patch.object(run_monitor.uvicorn,'Server',side_effect=server_factory),patch.object(run_monitor.webbrowser,'open') as browser:
                thread=threading.Thread(target=execute,daemon=True);thread.start()
                try:
                    wait_for(lambda:len(servers)==1 and servers[0].started)
                    captured[0].activate(1001)
                    wait_for(lambda:browser.called)
                    browser.assert_called_once_with(url)
                    page=get_page()
                    path.write_text('{"printers":[],"poll_seconds":6}')
                    captured[0].activate(1002)
                    wait_for(lambda:len(servers)==2 and servers[1].started)
                    self.assertEqual(get_page(),page)
                    self.assertIsNot(servers[0].config.app,servers[1].config.app)
                    self.assertEqual(remote.path.read_bytes(),saved)
                    self.assertTrue(RemoteAccess(remote.path).authorize(token,'test@example.com'))
                    self.assertFalse(servers[0].servers[0].is_serving())
                finally:
                    if captured:captured[0].activate(1003)
                    thread.join(15)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
            self.assertEqual(ConfigStore(path).data['poll_seconds'],6)


if __name__=='__main__':unittest.main()
