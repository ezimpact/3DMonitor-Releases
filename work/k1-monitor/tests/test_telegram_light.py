"""Telegram light controls with isolated async mocks; never connect to printers."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock
from k1monitor.activity import ActivityService
import test_status_history as fixtures
from test_activity_notifications import sample

class BotLightTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BotStatusTests.setUp
    request=fixtures.BotStatusTests.request
    update=fixtures.BotStatusTests.update
    async def test_toggle_on_off_explicit_aliases(self):
        self.bot.light_provider=AsyncMock(return_value=['K1: команда отправлена'])
        for i,(command,value) in enumerate([('свет',None),('свет вкл',True),('свет выкл',False),('/light@fixture_bot on',True),('/свет off',False)]):
            self.now+=4;self.updates=[self.update(command,id=i+1)];await self.bot.poll_commands_once();self.assertIs(self.bot.light_provider.call_args.args[0],value)
        self.assertEqual(self.bot.light_provider.await_count,5);self.assertTrue(all(m=='sendMessage' for m,b in self.sent))
    async def test_unpaired_group_old_and_foreign_bot_cannot_control(self):
        self.bot.light_provider=AsyncMock(return_value=[])
        self.updates=[self.update('свет',chat=8,id=1),self.update('свет',type='group',id=2),self.update('свет',date=self.now-31,id=3),self.update('/light@another_bot',id=4)]
        await self.bot.poll_commands_once();self.bot.light_provider.assert_not_awaited();self.assertEqual(len(self.sent),1);self.assertIn('устарела',self.sent[0][1]['text'])
    async def test_duplicate_update_and_restart_offset_never_repeat_control(self):
        self.bot.light_provider=AsyncMock(return_value=['OK']);message=self.update('свет');self.updates=[message,message]
        await self.bot.poll_commands_once();self.now+=5;await self.bot.poll_commands_once();self.assertEqual(self.bot.light_provider.await_count,1)
    async def test_invalid_light_argument_and_other_controls_ignored(self):
        self.bot.light_provider=AsyncMock(return_value=[]);self.updates=[self.update('свет maybe',id=1),self.update('нагрев 240',id=2),self.update('пауза',id=3),self.update('стоп',id=4)]
        await self.bot.poll_commands_once();self.bot.light_provider.assert_not_awaited();self.assertEqual(len(self.sent),1);self.assertIn('свет выкл',self.sent[0][1]['text'])
    async def test_light_works_while_status_waits_for_camera(self):
        waiting=asyncio.Event()
        async def photo(*args):await waiting.wait();return None
        self.bot.photo_provider=photo;self.bot.light_provider=AsyncMock(return_value=['K1: свет отправлен'])
        self.updates=[self.update('статус',id=1)];await self.bot.poll_commands_once(background_status=True);await asyncio.sleep(.01)
        self.assertFalse(self.bot.status_task.done());self.updates.append(self.update('свет',id=2));await self.bot.poll_commands_once(background_status=True)
        self.assertEqual(self.bot.light_provider.await_count,1);self.assertIn('свет отправлен',self.sent[-1][1]['text']);await self.bot.stop()
    async def test_short_cooldown_returns_explanation(self):
        self.bot.light_provider=AsyncMock(return_value=['OK']);self.updates=[self.update('свет',id=1),self.update('свет',id=2)];await self.bot.poll_commands_once()
        self.assertEqual(self.bot.light_provider.await_count,1);self.assertIn('3 секунды',self.sent[-1][1]['text'])

class LightGroupTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BotStatusTests.setUp
    request=fixtures.BotStatusTests.request
    def service(self,states):
        items=[];monitors={};entries={}
        for i,state in enumerate(states):
            item=sample(pid='p'+str(i));item.update(light_on=state,capabilities={'light':True});item['connection']['sessions']=3
            monitor=type('Monitor',(),{})();monitor.snapshot=lambda item=item:deepcopy(item);monitor.stock_command=AsyncMock(return_value={'phase':'sent'})
            items.append(item);monitors[item['printer']['id']]=monitor;entries[item['printer']['id']]=deepcopy(item['printer'])
        manager=type('Manager',(),{'snapshot':lambda _:{'printers':deepcopy(items)},'get':lambda _,pid:monitors[pid],'entry':lambda _,pid:entries[pid]})()
        return ActivityService(manager,self.bot),items,monitors,entries
    async def test_mixed_states_turn_all_on_and_all_on_turn_off(self):
        service,items,monitors,_=self.service([False,True]);result=await service.set_lights(None,'request',lambda:True)
        self.assertIs(monitors['p0'].stock_command.call_args.kwargs['value'],True);monitors['p1'].stock_command.assert_not_awaited();self.assertIn('уже включён',result[1])
        items[0]['light_on']=True;await service.set_lights(None,'second',lambda:True)
        self.assertIs(monitors['p0'].stock_command.call_args.kwargs['value'],False);self.assertIs(monitors['p1'].stock_command.call_args.kwargs['value'],False)
        self.assertEqual(monitors['p1'].stock_command.call_args.kwargs['action'],'light')
    async def test_offline_unsupported_unknown_skipped(self):
        service,items,monitors,_=self.service([False,False,None,False]);items[0]['connection']['online']=False;items[1]['capabilities']['light']=False
        result=await service.set_lights(True,'request',lambda:True)
        for pid in ['p0','p1','p2']:monitors[pid].stock_command.assert_not_awaited()
        self.assertEqual(monitors['p3'].stock_command.await_count,1);self.assertIn('нет связи',result[0]);self.assertIn('не поддерживается',result[1])
    async def test_reconnect_before_later_command_is_skipped(self):
        service,items,monitors,_=self.service([False,False])
        async def first(**kwargs):items[1]['connection']['sessions']=4;return {'phase':'sent'}
        monitors['p0'].stock_command.side_effect=first;result=await service.set_lights(True,'request',lambda:True)
        monitors['p1'].stock_command.assert_not_awaited();self.assertIn('не удалось',result[1])
    async def test_replaced_monitor_never_receives_old_selection(self):
        service,items,monitors,_=self.service([False,False]);old=monitors['p1']
        async def first(**kwargs):monitors['p1']=type('Monitor',(),{'stock_command':AsyncMock()})();return {'phase':'sent'}
        monitors['p0'].stock_command.side_effect=first;await service.set_lights(True,'request',lambda:True)
        old.stock_command.assert_not_awaited();monitors['p1'].stock_command.assert_not_awaited()
    async def test_chat_revocation_stops_remaining_commands(self):
        service,items,monitors,_=self.service([False,False]);allowed=True
        async def first(**kwargs):
            nonlocal allowed
            allowed=False;return {'phase':'sent'}
        monitors['p0'].stock_command.side_effect=first;result=await service.set_lights(True,'request',lambda:allowed)
        monitors['p1'].stock_command.assert_not_awaited();self.assertIn('прекращено',result[1])
    async def test_failure_isolated_and_unconfirmed_never_retried(self):
        service,items,monitors,_=self.service([False,False,False]);monitors['p0'].stock_command.side_effect=OSError();monitors['p1'].stock_command.return_value={'phase':'unconfirmed'}
        result=await service.set_lights(False,'request',lambda:True);self.assertTrue(all(m.stock_command.await_count==0 for m in monitors.values()))
        result=await service.set_lights(True,'request',lambda:True);self.assertTrue(all(m.stock_command.await_count==1 for m in monitors.values()));self.assertIn('не удалось',result[0]);self.assertIn('не подтверждён',result[1]);self.assertIn('отправлена',result[2])
