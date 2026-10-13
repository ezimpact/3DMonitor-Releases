"""GitHub and installer mocks only. Never contact or control a printer."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import httpx
from fastapi.testclient import TestClient
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.remote import RemoteAccess,COOKIE
from k1monitor.server import create_app
from k1monitor.updates import Updates,release_plan,version_tuple,checksum_value,API
import test_monitor as fixtures

CONTENT=b'MZ'+b'Fixture installer that is never executed.'*10
DIGEST=hashlib.sha256(CONTENT).hexdigest()
NAME='3DMonitor-Setup-v0.101.exe'
def release():return {'tag_name':'v0.101','draft':False,'prerelease':False,'assets':[{'id':1,'name':NAME,'state':'uploaded','size':len(CONTENT),'digest':'sha256:'+DIGEST},{'id':2,'name':NAME[:-4]+'.sha256','state':'uploaded','size':len(DIGEST)+len(NAME)+3}]}

class ReleaseTests(unittest.TestCase):
    def test_version_numbers_not_lexicographic_and_no_downgrade(self):
        self.assertGreater(version_tuple('0.100'),version_tuple('0.99.1'))
        self.assertEqual(version_tuple('1.0'),version_tuple('1.0.0'))
        for value in ('v1.0','1.0-rc1','../1.0','1','1.0\n',True):
            with self.assertRaises(ValueError):version_tuple(value)
        self.assertIsNone(release_plan(release(),'0.101'));self.assertIsNone(release_plan(release(),'0.102'))

    def test_missing_duplicate_draft_prerelease_large_wrong_name_rejected(self):
        for mutation in ('draft','pre','missing','duplicate','size','name'):
            data=release()
            if mutation=='draft':data['draft']=True
            elif mutation=='pre':data['prerelease']=True
            elif mutation=='missing':data['assets'].pop()
            elif mutation=='duplicate':data['assets'].append(copy.deepcopy(data['assets'][0]))
            elif mutation=='size':data['assets'][0]['size']=1024**3
            else:data['assets'][0]['name']='unrelated.exe'
            with self.assertRaises(ValueError,msg=mutation):release_plan(data,'0.100')

    def test_checksum_is_bound_to_exact_installer_name(self):
        self.assertEqual(checksum_value((DIGEST+'  '+NAME+'\n').encode(),NAME),DIGEST)
        for data in [DIGEST.encode(),(DIGEST+'  wrong.exe').encode(),(DIGEST+'  '+NAME+'\nextra').encode()]:
            with self.assertRaises(ValueError):checksum_value(data,NAME)

class UpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.requests=[];self.runner=Mock();self.control=Mock()
        def handler(request):
            self.requests.append(request)
            if request.url.path.endswith('/latest'):return httpx.Response(200,json=release())
            if request.url.path.endswith('/2'):return httpx.Response(200,content=(DIGEST+'  '+NAME+'\n').encode())
            return httpx.Response(200,content=CONTENT)
        self.handler=handler
        self.update=Updates(self.root,current='0.100',control=self.control,install_supported=True,runner=self.runner,client_factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(lambda req:self.handler(req))))
        self.update.token='SECRET_TEST_TOKEN'

    async def test_check_gets_public_release_without_authorization(self):
        status=await self.update.check();self.assertEqual(status['phase'],'available');self.assertEqual(status['available']['version'],'0.101')
        self.assertEqual(str(self.requests[0].url),API+'/releases/latest');self.assertNotIn('authorization',self.requests[0].headers)
        self.assertNotIn('SECRET_TEST_TOKEN',json.dumps(status));self.runner.assert_not_called();self.control.request.assert_not_called()

    async def test_fresh_install_checks_without_credentials(self):
        self.update.token='';status=await self.update.check();self.assertEqual(status['phase'],'available');self.assertEqual(len(self.requests),1)

    async def test_unauthorized_response_is_sanitized(self):
        self.handler=lambda req:httpx.Response(401,content=b'SECRET_TEST_TOKEN private debug')
        status=await self.update.check();self.assertEqual(status['phase'],'error');self.assertNotIn('SECRET_TEST_TOKEN',json.dumps(status));self.assertIn('временно недоступен',status['error'])

    async def test_legacy_secret_is_preserved_opaque_and_never_used(self):
        (self.root/'config.json').write_text('{"printers":[]}');before=(self.root/'config.json').read_bytes()
        legacy={'auto_check':False,'token':'OPAQUE_LEGACY_SECRET','extra':'preserve'}
        self.update.settings.parent.mkdir(parents=True,exist_ok=True);self.update.settings.write_text(json.dumps(legacy))
        reopened=Updates(self.root,client_factory=self.update.client_factory,current='0.100')
        self.assertFalse(reopened.auto_check);self.assertFalse(reopened.status()['has_token'])
        self.assertEqual((await reopened.check())['phase'],'available')
        self.assertNotIn('authorization',self.requests[-1].headers)
        await reopened.configure(True)
        stored=json.loads(reopened.settings.read_text());self.assertEqual(stored['token'],legacy['token']);self.assertEqual(stored['extra'],'preserve')
        self.assertEqual((self.root/'config.json').read_bytes(),before)
        with self.assertRaises(ValueError):await reopened.configure(True,'new_token')

    async def test_download_verifies_hash_size_and_never_runs_file(self):
        await self.update.check();status=await self.update.download();self.assertEqual(status['phase'],'ready');self.assertEqual(status['received'],len(CONTENT));self.assertEqual(self.update.downloaded['path'].read_bytes(),CONTENT)
        self.assertFalse(list(self.update.cache.glob('*.part')));self.runner.assert_not_called();self.control.request.assert_not_called()

    async def test_corrupted_or_truncated_installer_never_becomes_ready(self):
        await self.update.check();original=self.handler
        for content in [CONTENT[:-1],CONTENT[:-1]+b'X',CONTENT+b'extra']:
            self.handler=lambda req,content=content:httpx.Response(200,content=content) if req.url.path.endswith('/1') else original(req)
            status=await self.update.download();self.assertEqual(status['phase'],'error');self.assertIsNone(self.update.downloaded);self.assertFalse(list(self.update.cache.glob('*.part')))
        self.runner.assert_not_called()

    async def test_redirect_never_forwards_authorization(self):
        await self.update.check();original=self.handler
        def handler(req):
            self.requests.append(req)
            if req.url.host=='release-assets.githubusercontent.com':return httpx.Response(200,content=CONTENT)
            if req.url.path.endswith('/1'):return httpx.Response(302,headers={'location':'https://release-assets.githubusercontent.com/fixture?signature=example'})
            return original(req)
        self.handler=handler;self.assertEqual((await self.update.download())['phase'],'ready')
        redirected=[req for req in self.requests if req.url.host=='release-assets.githubusercontent.com'];self.assertEqual(len(redirected),1);self.assertNotIn('authorization',redirected[0].headers)

    async def test_foreign_or_insecure_redirect_is_rejected_before_request(self):
        await self.update.check();original=self.handler
        for url in ['https://evil.example/installer','http://release-assets.githubusercontent.com/file','https://user@objects.githubusercontent.com/file']:
            self.handler=lambda req,url=url:httpx.Response(302,headers={'location':url}) if req.url.path.endswith('/1') else original(req)
            self.assertEqual((await self.update.download())['phase'],'error');self.assertIsNone(self.update.downloaded)
        self.assertTrue(all(req.url.host=='api.github.com' for req in self.requests))

    async def test_github_digest_and_checksum_must_agree(self):
        await self.update.check();self.update.plan['digest']='sha256:'+'0'*64
        self.assertEqual((await self.update.download())['phase'],'error');self.assertIsNone(self.update.downloaded)

    async def test_install_requires_reverification_and_explicit_supported_environment(self):
        await self.update.check();await self.update.download();path=self.update.downloaded['path'];path.write_bytes(CONTENT+b'changed')
        with self.assertRaises(ValueError):self.update.launch_install()
        self.runner.assert_not_called();self.control.request.assert_not_called()
        path.write_bytes(CONTENT);self.update.install_supported=False
        with self.assertRaises(ValueError):self.update.launch_install()
        self.runner.assert_not_called()

    async def test_explicit_install_starts_only_fixed_helper_and_stops_monitor(self):
        await self.update.check();await self.update.download();self.update.launch_install()
        self.runner.assert_called_once();command=self.runner.call_args.args[0];self.assertTrue(command[0].endswith('powershell.exe'));self.assertIn('-File',command)
        self.assertEqual(self.runner.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'],'1')
        job=json.loads((self.update.cache/'install-job.json').read_text());self.assertEqual(job['app_dir'],str(self.root));self.assertEqual(job['sha256'],DIGEST);self.assertNotIn('SECRET_TEST_TOKEN',json.dumps(job))
        self.control.request.assert_called_once_with('stop');self.assertEqual(self.update.phase,'installing')

    async def test_background_download_returns_immediately_and_finishes_without_install(self):
        await self.update.check();status=self.update.begin_download();self.assertEqual(status['phase'],'downloading');await self.update.download_task
        self.assertEqual(self.update.phase,'ready');self.runner.assert_not_called()

    async def test_busy_and_missing_plan_reject_duplicate_actions(self):
        self.update.plan=None
        with self.assertRaises(ValueError):self.update.begin_download()
        await self.update.lock.acquire()
        try:
            with self.assertRaises(ValueError):await self.update.check()
            with self.assertRaises(ValueError):self.update.begin_download()
        finally:self.update.lock.release()

class UpdateApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);path=self.root/'config.json';path.write_text('{"printers":[]}')
        self.manager=PrinterManager(ConfigStore(path),factory=fixtures.FakeMonitor);self.remote=RemoteAccess(self.root/'remote.json');self.remote.configure('pc.tail12345.ts.net')
        self.updater=Updates(self.root,current='0.100');self.updater.token='SECRET_TEST_TOKEN';self.updater.launch_install=Mock()
        self.app=create_app(self.manager,self.remote,updates=self.updater);self.headers={'X-K1-Token':self.app.state.csrf_token}

    def test_local_status_has_no_secret_and_screensaver_endpoints_removed(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8765') as client:
            result=client.get('/api/v1/updates');self.assertEqual(result.status_code,200);self.assertNotIn('SECRET_TEST_TOKEN',result.text)
            html=client.get('/').text;self.assertNotIn('saver-open',html);self.assertNotIn('saver-dialog',html);self.assertIn('updates-check',html)
            for method in ('get','put','post'):self.assertEqual(getattr(client,method)('/api/v1/screensaver',headers=self.headers).status_code,404)

    def test_csrf_and_foreign_origin_block_update_actions(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8765') as client:
            for path in ('check','download','install'):
                self.assertEqual(client.post('/api/v1/updates/'+path).status_code,403)
                self.assertEqual(client.post('/api/v1/updates/'+path,headers={**self.headers,'Origin':'https://foreign.example'}).status_code,403)
        self.updater.launch_install.assert_not_called()

    def test_remote_cannot_read_or_change_update_credentials_or_install(self):
        session=self.remote.pair(self.remote.pair_code()['code'],'Phone','owner')
        with TestClient(self.app,base_url='https://pc.tail12345.ts.net',headers={'Tailscale-User-Login':'owner','Cookie':COOKIE+'='+session},client=('127.0.0.1',50000)) as client:
            self.assertEqual(client.get('/api/v1/updates').status_code,403)
            for path in ('check','download','install'):self.assertEqual(client.post('/api/v1/updates/'+path,headers=self.headers).status_code,403)
        self.updater.launch_install.assert_not_called()

    def test_runout_or_pending_command_blocks_install_without_stopping_monitor(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8765') as client:
            for item in [{'filament_runout':True,'auto_unload':{'enabled':True}},{'command':{'phase':'pending'}}]:
                self.manager.snapshot=lambda:{'printers':[item]}
                self.assertEqual(client.post('/api/v1/updates/install',headers=self.headers).status_code,409)
        self.updater.launch_install.assert_not_called()

    def test_upload_receive_or_transfer_lock_blocks_install(self):
        with TestClient(self.app,base_url='http://127.0.0.1:8765') as client:
            transfers=self.app.state.file_transfers
            transfers.slots._value=1
            self.assertEqual(client.post('/api/v1/updates/install',headers=self.headers).status_code,409)
            transfers.slots._value=2
            lock=Mock();lock.locked.return_value=True;transfers.locks['fixture']=lock
            self.assertEqual(client.post('/api/v1/updates/install',headers=self.headers).status_code,409)
        self.updater.launch_install.assert_not_called()
