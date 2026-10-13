import asyncio
import unittest
from k1monitor.files import FileCatalog,safe_directory,GCODE_ROOT
from test_moonraker import monitor

class DirectoryTests(unittest.TestCase):
    def test_catalog_preserves_paths_and_lists_only_direct_children(self):
        catalog=FileCatalog()
        catalog.files=[{'id':'a','name':'same.gcode','path':GCODE_ROOT+'Folder/nested/same.gcode','size_bytes':1,'modified':0},
                       {'id':'b','name':'same.gcode','path':GCODE_ROOT+'Other/same.gcode','size_bytes':2,'modified':0}]
        self.assertEqual(catalog.public()[0]['relative_path'],'Folder/nested/same.gcode')
        self.assertEqual(catalog.directory_entries(''),[{'name':'Folder','path':'Folder'},{'name':'Other','path':'Other'}])
        self.assertEqual(catalog.directory_entries('Folder'),[{'name':'nested','path':'Folder/nested'}])
        for bad in ['../config','/config','a//b','a/../b','a\\b','a\x00b']:self.assertFalse(safe_directory(bad))
        self.assertTrue(safe_directory('Папка с пробелами/Детали'))

    def test_moonraker_empty_directories_are_read_with_encoded_get_only(self):
        async def run():
            m=monitor();calls=[]
            async def get(path):
                calls.append(path)
                return {'dirs':[{'dirname':'Empty'},{'dirname':'Детали'},{'dirname':'../config'},{'dirname':'bad/slash'},{'dirname':'Empty'}]}
            m._get=get
            result=await m.list_directory('Модели с пробелами')
            self.assertEqual([item['name'] for item in result],['Empty','Детали'])
            self.assertEqual(result[0]['path'],'Модели с пробелами/Empty')
            self.assertIn('path=gcodes%2F',calls[0]);self.assertNotIn(' ',calls[0]);self.assertEqual(len(calls),1)
            with self.assertRaises(ValueError):await m.list_directory('../config')
            self.assertEqual(len(calls),1)
        asyncio.run(run())

    def test_moonraker_changed_session_cannot_supply_an_old_directory(self):
        async def run():
            m=monitor()
            async def get(path):m.state.begin_session();return {'dirs':[]}
            m._get=get
            with self.assertRaises(ValueError):await m.list_directory('')
        asyncio.run(run())

    def test_wrong_control_ip_is_rejected_before_any_transport(self):
        async def run():
            m=monitor();m._ingest({'print_stats':{'state':'printing','filename':'one.gcode'}})
            async def forbidden(*args):raise AssertionError('No transport is allowed in this test')
            m._send=forbidden
            with self.assertRaises(ValueError):
                await m.control('pause','wrong-ip-123',m.state.connection_count,'one.gcode',expected_ip='192.168.254.99')
        asyncio.run(run())
