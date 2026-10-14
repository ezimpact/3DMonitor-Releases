from pathlib import Path
from types import SimpleNamespace
import copy,json,tempfile,unittest
from fastapi.testclient import TestClient
from k1monitor.print_costs import calculate,CostService
from k1monitor.config import ConfigStore
from k1monitor.manager import PrinterManager
from k1monitor.activity import ActivityStore,ActivityService
from k1monitor.notifications import Notifications
from k1monitor.remote import RemoteAccess
from k1monitor.server import create_app
from test_monitor import FakeMonitor

class CalculationTests(unittest.TestCase):
    def test_components_markup_and_display_sum(self):
        result=calculate({'currency':'RUB','filament_g':100,'price_per_kg':1500,'hours':4,
            'mean_power_w':150,'electricity_rate':6,'machine_hour_rate':50,'extra_cost':20,'markup_percent':25})
        self.assertEqual(result,{'currency':'RUB','plastic_cost':150.,'electricity_cost':3.6,
            'machine_cost':200.,'extra_cost':20.,'labor_cost':0.,'manual_work_cost':0.,
            'extra_material_cost':0.,'failure_reserve':0.,'energy_kwh':.6,
            'total_cost':373.6,'markup_cost':93.4,'sale_price':467.})
    def test_labor_time_is_independent_and_all_components_enter_markup(self):
        result=calculate({'currency':'RUB','filament_g':100,'price_per_kg':1500,'hours':4,
            'mean_power_w':150,'electricity_rate':6,'machine_hour_rate':50,'extra_cost':20,
            'labor_hours':2,'labor_hour_rate':400,'manual_work_cost':100,'extra_material_cost':50,
            'failure_percent':10,'markup_percent':25})
        self.assertEqual(result['labor_cost'],800)
        self.assertEqual(result['failure_reserve'],39.29)
        self.assertEqual(result['total_cost'],1362.89)
        self.assertEqual(result['markup_cost'],340.72)
        self.assertEqual(result['sale_price'],1703.61)
        self.assertEqual(result['total_cost'],sum(result[k] for k in
            ('plastic_cost','electricity_cost','machine_cost','extra_cost','labor_cost',
             'manual_work_cost','extra_material_cost','failure_reserve')))
    def test_expected_repeat_print_reserve_excludes_finishing_expenses(self):
        result=calculate({'filament_g':900,'price_per_kg':1000,'hours':0,'failure_percent':10,
            'labor_hours':1,'labor_hour_rate':400,'manual_work_cost':100,'extra_material_cost':50,'extra_cost':20})
        self.assertEqual(result['failure_reserve'],100)
        self.assertEqual(result['total_cost'],1570)
    def test_fractional_labor_hours_and_half_up_rounding(self):
        result=calculate({'filament_g':0,'price_per_kg':0,'hours':20,'labor_hours':.5,'labor_hour_rate':10.01})
        self.assertEqual(result['labor_cost'],5.01)
        self.assertEqual(result['total_cost'],5.01)
    def test_failure_boundaries_and_invalid_labor_inputs(self):
        base={'filament_g':100,'price_per_kg':1000,'hours':0}
        for key in ('labor_hours','labor_hour_rate','manual_work_cost','extra_material_cost','failure_percent'):
            for value in (-1,True,float('nan'),float('inf')):
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):calculate({**base,key:value})
        for value in (100,101,99.991):
            with self.assertRaises(ValueError):calculate({**base,'failure_percent':value})
        self.assertEqual(calculate({**base,'failure_percent':99.99})['total_cost'],1000000)
    def test_decimal_rounding_half_up_and_no_currency_conversion(self):
        for currency in ('RUB','USD','EUR'):
            result=calculate({'currency':currency,'filament_g':100,'price_per_kg':.05,'hours':0})
            self.assertEqual(result['plastic_cost'],.01);self.assertEqual(result['total_cost'],.01)
    def test_invalid_unknown_and_explicit_zero(self):
        base={'currency':'RUB','filament_g':100,'price_per_kg':1000,'hours':1}
        for field,value in [('price_per_kg',None),('filament_g',-1),('hours',True),('mean_power_w',float('nan')),('electricity_rate',float('inf')),('price_per_kg',10**1000),('currency','GBP')]:
            with self.subTest(field=field,value=str(value)[:30]),self.assertRaises(ValueError):calculate({**base,field:value})
        self.assertEqual(calculate({**base,'price_per_kg':0})['plastic_cost'],0)

class CostPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.language=SimpleNamespace(language='ru')
        self.manager=SimpleNamespace(language_settings=self.language,get=lambda pid:object(),entry=lambda pid:{'protocol':'creality','ip':'192.168.250.10','api_port':9999})
        self.filament=SimpleNamespace(views={},jobs={})
        self.service=CostService(self.manager,self.filament,self.root/'print-pricing.json')
    def job(self,jid='job1',grams=200):
        self.filament.views['one']={'id':jid,'active':True,'analysis':'ready','total_g':grams}
        self.filament.jobs['one']={'id':jid,'endpoint':'creality|192.168.250.10|9999'}
    def test_language_default_explicit_selection_and_restart(self):
        self.assertIsNone(self.service.pricing('one')['price_per_kg']);self.assertEqual(self.service.pricing('one')['currency'],'RUB')
        self.language.language='en';self.assertEqual(self.service.pricing('one')['currency'],'USD')
        self.service.configure('one',{'currency':'EUR','price_per_kg':20})
        restarted=CostService(self.manager,self.filament,self.root/'print-pricing.json')
        self.assertEqual(restarted.pricing('one')['currency'],'EUR');self.assertEqual(restarted.pricing('one')['price_per_kg'],20)
    def test_price_snapshot_survives_price_change_restart_and_native_history(self):
        self.job();self.service.configure('one',{'currency':'RUB','price_per_kg':1500})
        saved=self.service.public('one')['job'];self.assertEqual(saved['plastic_cost'],300)
        self.service.configure('one',{'currency':'USD','price_per_kg':30})
        self.assertEqual(self.service.public('one')['job'],saved)
        restarted=CostService(self.manager,self.filament,self.root/'print-pricing.json');restarted.observe()
        self.assertEqual(restarted.public('one')['job'],saved)
        history=[{'id':'job1','endpoint':'|192.168.250.10|','source':'printer'}]
        self.assertEqual(restarted.history('one',history)[0]['material_cost'],saved)
        self.job('job2');restarted.observe();self.assertEqual(restarted.public('one')['job']['plastic_cost'],6)
    def test_missing_analysis_price_inactive_history_and_endpoint_never_invent_cost(self):
        self.job();self.service.observe();self.assertIsNone(self.service.public('one')['job'])
        self.filament.views['one']['analysis']='unavailable'
        self.service.configure('one',{'currency':'RUB','price_per_kg':1000});self.assertIsNone(self.service.public('one')['job'])
        self.filament.views['one'].update(analysis='ready',active=False)
        self.service.observe();self.assertIsNone(self.service.public('one')['job'])
        self.filament.views['one']['active']=True;self.service.observe()
        self.assertIsNone(self.service.history('one',[{'id':'job1','endpoint':'creality|192.168.250.11|9999'}])[0]['material_cost'])
        self.manager.entry=lambda pid:{'protocol':'creality','ip':'192.168.250.11','api_port':9999}
        self.assertIsNone(self.service.public('one')['job'])
    def test_repeated_observation_does_not_rewrite_database(self):
        self.job();self.service.configure('one',{'currency':'RUB','price_per_kg':1000})
        stamp=self.service.store.path.stat().st_mtime_ns
        for _ in range(100):self.service.observe()
        self.assertEqual(self.service.store.path.stat().st_mtime_ns,stamp)
    def test_old_pricing_migrates_and_new_rates_persist_without_job_duration(self):
        path=self.root/'print-pricing.json'
        path.write_text(json.dumps({'schema_version':1,'printers':{'one':{'currency':'EUR','price_per_kg':20,'machine_hour_rate':5}}}))
        restarted=CostService(self.manager,self.filament,path)
        self.assertEqual(restarted.pricing('one')['labor_hour_rate'],0)
        self.assertEqual(restarted.pricing('one')['failure_percent'],0)
        self.assertEqual(restarted.pricing('one')['machine_hour_rate'],5)
        restarted.configure('one',{**restarted.pricing('one'),'labor_hour_rate':40,
            'manual_work_cost':10,'extra_material_cost':5,'failure_percent':12})
        again=CostService(self.manager,self.filament,path).pricing('one')
        self.assertEqual(again['labor_hour_rate'],40);self.assertEqual(again['manual_work_cost'],10)
        self.assertEqual(again['extra_material_cost'],5);self.assertEqual(again['failure_percent'],12)
        self.assertNotIn('labor_hours',again)

class CostApiTests(unittest.TestCase):
    def test_validation_csrf_remote_access_persistence_and_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config.json').write_text(json.dumps({'printer_ip':'192.168.250.10'}))
            manager=PrinterManager(ConfigStore(root/'config.json'),factory=FakeMonitor)
            activity=ActivityService(manager,Notifications(root/'notifications.json',ActivityStore(root/'history.sqlite3')))
            remote=RemoteAccess(root/'remote.json');remote.configure('pc.tail123.ts.net')
            app=create_app(manager,remote,activity=activity,csrf_token='test-token');pid=manager.config.printers[0]['id']
            p=manager.entry(pid);ep='|'.join(str(p.get(key,'')) for key in ('protocol','ip','api_port'))
            path='/api/v1/printers/'+pid+'/pricing';headers={'X-K1-Token':'test-token'}
            prices={'expected_endpoint':ep,'currency':'EUR','price_per_kg':25,'labor_hour_rate':40,
                'manual_work_cost':10,'extra_material_cost':5,'failure_percent':10}
            with TestClient(app,base_url='http://127.0.0.1:8765') as client:
                self.assertEqual(client.put(path,json=prices).status_code,403)
                self.assertEqual(client.put(path,headers=headers,json={**prices,'expected_endpoint':'bad'}).status_code,409)
                self.assertEqual(client.put(path,headers=headers,json=prices).status_code,200)
                self.assertEqual(client.get(path).json()['pricing']['currency'],'EUR')
                for value in (True,-1,'100'):
                    self.assertEqual(client.put(path,headers=headers,json={**prices,'price_per_kg':value}).status_code,422)
                remote_headers={**headers,'Host':remote.host,'Tailscale-User-Login':'owner@test'}
                self.assertEqual(client.put(path,headers=remote_headers,json=prices).status_code,403)
                request={'currency':'USD','filament_g':250,'price_per_kg':20,'hours':1.5}
                self.assertEqual(client.post('/api/v1/cost/calculate',json=request).status_code,403)
                self.assertEqual(client.post('/api/v1/cost/calculate',headers=headers,json=request).json()['total_cost'],5)
                extended={**request,'labor_hours':.5,'labor_hour_rate':40,'manual_work_cost':10,
                    'extra_material_cost':5,'failure_percent':10}
                self.assertEqual(client.post('/api/v1/cost/calculate',headers=headers,json=extended).json()['total_cost'],40.56)
                for key in ('labor_hour_rate','labor_hours','manual_work_cost','extra_material_cost','failure_percent'):
                    self.assertEqual(client.post('/api/v1/cost/calculate',headers=headers,json={**request,key:True}).status_code,422)
                self.assertEqual(client.post('/api/v1/cost/calculate',headers=headers,json={**request,'failure_percent':100}).status_code,422)
                self.assertEqual(client.post('/api/v1/cost/calculate',headers=headers,json={**request,'price_per_kg':None}).status_code,422)
                self.assertEqual(client.post('/api/v1/cost/calculate',headers=remote_headers,json=request).status_code,403)
            self.assertEqual(CostService(manager,activity.filament,root/'print-pricing.json').pricing(pid)['price_per_kg'],25)
            self.assertEqual(CostService(manager,activity.filament,root/'print-pricing.json').pricing(pid)['labor_hour_rate'],40)
            self.assertFalse(activity.temperature_watch.enabled(pid))

if __name__=='__main__':unittest.main()
