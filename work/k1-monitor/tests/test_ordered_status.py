"""Serial Telegram responses and warm camera retries; hardware stays mocked."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock
import test_status_history as fixtures
import test_relay as relay_fixtures
from test_activity_notifications import sample
from k1monitor.activity import ActivityService
from k1monitor.notifications import TelegramError

class OrderedStatusTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BotStatusTests.setUp
    request=fixtures.BotStatusTests.request
    update=fixtures.BotStatusTests.update
    def cameras(self):
        self.reports=[{'printer_id':str(i),'endpoint':'test','name':'P'+str(i),'text':'P'+str(i)+'\nНачало: 10:00\nПримерное окончание: 11:00','camera':True} for i in range(3)]
        self.bot.status_provider=lambda:deepcopy(self.reports)
    async def test_retry_first_three_times_then_next_and_matching_photo_caption(self):
        self.cameras();calls=[]
        async def photo(pid,address):
            calls.append(pid)
            if pid=='0' and calls.count(pid)<3:raise TimeoutError()
            self.assertEqual(len(self.sent),int(pid))
            return b'photo'+pid.encode()
        self.bot.photo_provider=photo;await self.bot.reply_status(7,'fixture')
        self.assertEqual(calls,['0','0','0','1','2'])
        for i,(method,body) in enumerate(self.sent):
            self.assertEqual(method,'sendPhoto');self.assertEqual(body['photo'],b'photo'+str(i).encode());self.assertTrue(body['caption'].startswith(f'[{i+1}/3] P{i}'))
    async def test_exhausted_first_sends_full_status_before_next_capture(self):
        self.cameras();calls=[]
        async def photo(pid,address):
            calls.append(pid)
            if pid=='0':return None
            self.assertEqual(len(self.sent),int(pid));return relay_fixtures.JPEG
        self.bot.photo_provider=photo;await self.bot.reply_status(7,'fixture')
        self.assertEqual(calls,['0','0','0','1','2']);self.assertEqual([m for m,b in self.sent],['sendMessage','sendPhoto','sendPhoto']);self.assertIn('Примерное окончание',self.sent[0][1]['text'])
    async def test_refreshes_next_printer_and_caption_after_capture(self):
        self.cameras()
        async def photo(pid,address):
            self.reports[int(pid)]['text']+='\nОбновлено '+pid
            if pid=='0':self.reports[1]['text']='P1 — На паузе\nНачало: 10:00\nПримерное окончание: изменится'
            return relay_fixtures.JPEG
        self.bot.photo_provider=photo;await self.bot.reply_status(7,'fixture');self.assertIn('На паузе',self.sent[1][1]['caption']);self.assertIn('Обновлено 1',self.sent[1][1]['caption'])
    async def test_endpoint_change_discards_old_photo_preserves_order(self):
        self.cameras()
        async def photo(pid,address):
            if pid=='0':self.reports[0]['endpoint']='changed'
            return relay_fixtures.JPEG
        self.bot.photo_provider=photo;await self.bot.reply_status(7,'fixture')
        self.assertEqual(self.sent[0][0],'sendMessage');self.assertIn('изменено',self.sent[0][1]['text']);self.assertTrue(self.sent[1][1]['caption'].startswith('[2/3]'))
    async def test_events_deferred_for_status_chat_windows_and_other_chat_continue(self):
        self.cameras();native=[];self.bot.windows=lambda *args:native.append(args);self.bot.data['chats'].append({'id':8,'name':'Other'})
        self.bot.enqueue({'id':'started','kind':'started','created':self.now,'name':'P','remaining':0})
        async def photo(pid,address):
            await self.bot.dispatch()
            self.assertFalse(any(method=='sendMessage' and body['chat_id']==7 for method,body in self.sent))
            return relay_fixtures.JPEG
        self.bot.photo_provider=photo;await self.bot.reply_status(7,'fixture')
        self.assertEqual(len(native),1);self.assertEqual([b['chat_id'] for m,b in self.sent if m=='sendMessage'],[8])
        self.assertEqual(self.store.pending(self.now)[0]['attempts'],0)
        await self.bot.dispatch();self.assertEqual(self.sent[-1][1]['chat_id'],7);self.assertEqual(self.store.pending(self.now),[])
    async def test_cancellation_releases_deferred_messages(self):
        entered=asyncio.Event()
        async def photo(*args):entered.set();await asyncio.Event().wait()
        self.bot.photo_provider=photo;task=asyncio.create_task(self.bot.reply_status(7,'fixture'));await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.bot.status_chats,set());self.assertEqual(self.sent,[])
    async def test_photo_send_uncertain_is_not_resent_and_next_printer_continues(self):
        original=self.bot.request;attempts=[]
        def request(token,method,body):
            if method=='sendPhoto':attempts.append(method);raise TelegramError('temporary')
            return original(token,method,body)
        self.bot.request=request;await self.bot.reply_status(7,'fixture')
        self.assertEqual(attempts,['sendPhoto']);self.assertIn('не подтверждена',self.sent[0][1]['text']);self.assertIn('V400',self.sent[1][1]['text'])
    async def test_rejected_photo_rate_limit_retried_before_next_printer(self):
        self.cameras();original=self.bot.request;calls=[]
        def request(token,method,body):
            calls.append(body.get('caption',body.get('text')))
            if method=='sendPhoto' and len(calls)<3:raise TelegramError('rate limit',0,retry_safe=True)
            return original(token,method,body)
        self.bot.request=request;await self.bot.reply_status(7,'fixture')
        self.assertEqual(len(calls),5);self.assertTrue(all(c.startswith('[1/3]') for c in calls[:3]));self.assertTrue(calls[3].startswith('[2/3]'));self.assertEqual(len(self.sent),3)
    async def test_persistent_rejection_bounded_three_attempts_then_fallback(self):
        original=self.bot.request;attempts=[]
        def request(token,method,body):
            if method=='sendPhoto':attempts.append(method);raise TelegramError('rate limit',0,retry_safe=True)
            return original(token,method,body)
        self.bot.request=request;await self.bot.reply_status(7,'fixture');self.assertEqual(len(attempts),3);self.assertEqual(len(self.sent),2)
    async def test_long_unicode_file_error_keeps_times_within_photo_limit(self):
        item=sample('paused',file='😀'*1024,error={'active':True,'message':'😀'*350});item['printer']['name']='😀'*64;item['camera']={'present':True}
        manager=type('Manager',(),{'snapshot':lambda _:{'printers':[item]}})();ActivityService(manager,self.bot)
        self.bot.photo_provider=AsyncMock(return_value=relay_fixtures.JPEG);await self.bot.reply_status(7,'fixture')
        caption=self.sent[0][1]['caption'];self.assertLessEqual(len(caption.encode('utf-16-le'))//2,1024);self.assertIn('Начало:',caption);self.assertIn('Примерное окончание:',caption);self.assertIn('…',caption)

class SnapshotSequenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=relay_fixtures.RelayTests.asyncSetUp
    async def test_snapshot_retry_keeps_source_worker_and_releases_after_grace(self):
        task=asyncio.create_task(self.relay.snapshot('p0'));await asyncio.sleep(0);generation=self.relay.sources['p0'].generation;task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        second=asyncio.create_task(self.relay.snapshot('p0'));await asyncio.sleep(0);self.assertEqual(self.relay.sources['p0'].generation,generation)
        self.relay.frame('p0',generation,relay_fixtures.JPEG);self.assertEqual(await second,relay_fixtures.JPEG)
        self.assertEqual(self.browser.starts,1);self.assertFalse(self.relay.sources['p0'].viewers)
        self.now+=3.1;self.relay.sweep();self.assertFalse(self.browser.running)
    async def test_failed_source_retries_new_generation(self):
        first=asyncio.create_task(self.relay.snapshot('p0'));await asyncio.sleep(0);generation=self.relay.sources['p0'].generation;self.relay.fail('p0','unavailable')
        with self.assertRaises(ValueError):await first
        second=asyncio.create_task(self.relay.snapshot('p0'));await asyncio.sleep(0);self.assertNotEqual(self.relay.sources['p0'].generation,generation)
        self.relay.frame('p0',self.relay.sources['p0'].generation,relay_fixtures.JPEG);self.assertEqual(await second,relay_fixtures.JPEG)
    async def test_seven_serial_snapshots_do_not_exhaust_camera_slots(self):
        for i in range(7):
            pid='p'+str(i);task=asyncio.create_task(self.relay.snapshot(pid));await asyncio.sleep(0)
            self.relay.frame(pid,self.relay.sources[pid].generation,relay_fixtures.JPEG);self.assertEqual(await task,relay_fixtures.JPEG)
        self.assertEqual(self.browser.starts,1);self.assertLessEqual(len(self.relay.sources),6)
    async def test_idle_snapshot_eviction_never_displaces_existing_viewers(self):
        queues=[await self.relay.subscribe('p'+str(i)) for i in range(5)]
        idle=await self.relay.subscribe('p5');self.relay.unsubscribe('p5',idle)
        await self.relay.subscribe('p6');self.assertNotIn('p5',self.relay.sources)
        for i,queue in enumerate(queues):self.assertIn(queue,self.relay.sources['p'+str(i)].viewers)
