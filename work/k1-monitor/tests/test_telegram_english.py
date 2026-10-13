"""Command tests use mocked Telegram and light providers only."""
import unittest
from unittest.mock import AsyncMock
from k1monitor.language import translate, catalog
import test_status_history as fixtures

class EnglishCommandsTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.BotStatusTests.setUp
    request=fixtures.BotStatusTests.request
    update=fixtures.BotStatusTests.update

    async def test_english_status_and_light_aliases_keep_existing_guards(self):
        self.bot.reply_status=AsyncMock();self.bot.light_provider=AsyncMock(return_value=['fixture'])
        for index,(command,value) in enumerate([('status',None),('/status@fixture_bot',None),('LIGHT ON',True),('/light off',False),('lights',None)]):
            self.now+=4;self.updates=[self.update(command,id=index+1)];await self.bot.poll_commands_once()
            if 'status' in command:self.bot.reply_status.assert_awaited_with(7,'fixture')
            else:self.assertIs(self.bot.light_provider.call_args.args[0],value)
        self.assertEqual(self.bot.reply_status.await_count,2);self.assertEqual(self.bot.light_provider.await_count,3)

    async def test_help_is_english_and_never_sends_a_printer_command(self):
        self.bot.reply_status=AsyncMock();self.bot.light_provider=AsyncMock()
        for index,command in enumerate(['/help@fixture_bot','HELP']):
            self.updates=[self.update(command,id=index+1)];await self.bot.poll_commands_once()
            message=self.sent[-1][1]['text'];self.assertIn('/status',message);self.assertIn('/light on',message);self.assertIn('/light off',message);self.assertIn('paired private chats',message)
            self.assertFalse(any('\u0400'<=letter<='\u04ff' for letter in message))
        self.bot.translate=lambda value:translate(value,'en')
        self.updates=[self.update('/start',id=3)];await self.bot.poll_commands_once();self.assertIn('3DMonitor commands',self.sent[-1][1]['text'])
        self.bot.reply_status.assert_not_awaited();self.bot.light_provider.assert_not_awaited()

    async def test_unpaired_old_group_and_foreign_help_ignored(self):
        self.updates=[self.update('/help',chat=8,id=1),self.update('/help',type='group',id=2),self.update('/help',date=self.now-121,id=3),self.update('/help@other_bot',id=4)]
        await self.bot.poll_commands_once();self.assertEqual(self.sent,[])

    def test_english_help_and_pairing_instructions_use_english_command_names(self):
        instructions=[value for key,value in catalog().items() if key.startswith('Код действует 5 минут. Подключаются личные чаты') or key.startswith('3DMonitor подключён.')]
        self.assertEqual(len(instructions),2)
        for value in instructions:
            self.assertIn('/status',value);self.assertIn('/light on',value);self.assertIn('/help',value)
            self.assertNotIn('свет',value);self.assertNotIn('статус',value)
