"""Local print pricing and one saved material estimate per observed job."""
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
import copy
import json
import math
import sqlite3
import time
from .config import atomic_json
from .activity import endpoint, same_endpoint

CURRENCIES=('RUB','USD','EUR')
DEFAULT={'price_per_kg':None,'mean_power_w':0.,'electricity_rate':0.,
         'machine_hour_rate':0.,'labor_hour_rate':0.,'manual_work_cost':0.,
         'extra_material_cost':0.,'failure_percent':0.,'extra_cost':0.,'markup_percent':0.}
LIMITS={'price_per_kg':1e7,'mean_power_w':100000.,'electricity_rate':1e6,
        'machine_hour_rate':1e7,'labor_hour_rate':1e7,'manual_work_cost':1e9,
        'extra_material_cost':1e9,'failure_percent':99.99,'labor_hours':87600.,
        'extra_cost':1e9,'markup_percent':10000.,
        'filament_g':1e7,'hours':87600.}

def number(key,value,nullable=False):
    if value is None and nullable:return None
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=LIMITS[key] or not math.isfinite(value):
        raise ValueError('Некорректные значения расчёта стоимости')
    return Decimal(str(value))

def currency(value):
    if value not in CURRENCIES:raise ValueError('Выберите рубли, доллары или евро')
    return value

def money(value):return float(value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))

def calculate(values):
    if not isinstance(values,dict) or set(values)-set(LIMITS)-{'currency'}:
        raise ValueError('Некорректные значения расчёта стоимости')
    cur=currency(values.get('currency','RUB'))
    grams=number('filament_g',values.get('filament_g'))
    price=number('price_per_kg',values.get('price_per_kg'))
    hours=number('hours',values.get('hours'))
    params={key:number(key,values.get(key,0)) for key in ('mean_power_w','electricity_rate','machine_hour_rate','labor_hour_rate','labor_hours',
             'manual_work_cost','extra_material_cost','failure_percent','extra_cost','markup_percent')}
    energy=hours*params['mean_power_w']/1000
    parts={'plastic_cost':money(grams*price/1000),
           'electricity_cost':money(energy*params['electricity_rate']),
           'machine_cost':money(hours*params['machine_hour_rate']),
           'extra_cost':money(params['extra_cost'])}
    # Reserve on printer expenses only; finishing and extra materials are paid once.
    printing=sum(Decimal(str(parts[key])) for key in ('plastic_cost','electricity_cost','machine_cost'))
    failure=params['failure_percent']/100
    parts.update(labor_cost=money(params['labor_hours']*params['labor_hour_rate']),
                 manual_work_cost=money(params['manual_work_cost']),
                 extra_material_cost=money(params['extra_material_cost']),
                 failure_reserve=money(printing*failure/(1-failure)))
    subtotal=sum(Decimal(str(value)) for value in parts.values())
    markup=Decimal(str(money(subtotal*params['markup_percent']/100)))
    return {**parts,'currency':cur,'energy_kwh':float(energy),'total_cost':money(subtotal),
            'markup_cost':float(markup),'sale_price':money(subtotal+markup)}

class CostStore:
    def __init__(self,path):
        self.path=path;path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:db.execute('CREATE TABLE IF NOT EXISTS estimates (id TEXT PRIMARY KEY, printer TEXT NOT NULL, endpoint TEXT NOT NULL, payload TEXT NOT NULL)')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            with db:yield db
        finally:db.close()
    def record(self,jid,pid):
        with self.db() as db:row=db.execute('SELECT payload FROM estimates WHERE id=? AND printer=?',(jid,pid)).fetchone()
        return json.loads(row[0]) if row else None
    def save(self,pid,address,value):
        with self.db() as db:db.execute('INSERT OR IGNORE INTO estimates VALUES (?,?,?,?)',(value['id'],pid,address,json.dumps(value)))
    def records(self,pid,ids):
        if not ids:return {}
        with self.db() as db:
            rows=db.execute('SELECT id,payload FROM estimates WHERE printer=? AND id IN ('+','.join('?' for _ in ids)+')',[pid,*ids]).fetchall()
        return {row[0]:json.loads(row[1]) for row in rows}

class CostService:
    def __init__(self,manager,filament,path,*,clock=time.time):
        self.manager=manager;self.filament=filament;self.path=path;self.clock=clock
        self.settings={};self.current={};self.store=CostStore(path.with_name('print-costs.sqlite3'))
        if path.exists():
            raw=json.loads(path.read_text(encoding='utf-8-sig'))
            printers=raw.get('printers',{})
            if not isinstance(printers,dict):raise ValueError('Некорректные настройки стоимости')
            for pid,value in printers.items():self.settings[pid]=self.validate(value)
    def validate(self,value):
        if not isinstance(value,dict) or set(value)-set(DEFAULT)-{'currency'}:raise ValueError('Некорректные настройки стоимости')
        result={**DEFAULT,**value}
        result['currency']=currency(result.get('currency','RUB'))
        for key in DEFAULT:
            amount=number(key,result[key],key=='price_per_kg');result[key]=float(amount) if amount is not None else None
        return result
    def pricing(self,pid):
        default='RUB' if self.manager.language_settings.language=='ru' else 'USD'
        return {**DEFAULT,'currency':default,**self.settings.get(pid,{})}
    def configure(self,pid,value):
        self.manager.get(pid);saved=self.validate(value)
        updated={**self.settings,pid:saved};atomic_json(self.path,{'schema_version':1,'printers':updated});self.settings=updated
        self.observe();return self.public(pid)
    def observe(self):
        # Reuse analysed totals. No timer, network request or repeated G-code parsing.
        for pid,view in self.filament.views.items():
            if not view.get('active') or view.get('analysis')!='ready' or view.get('total_g') is None:continue
            job=self.filament.jobs.get(pid)
            if not job:continue
            pricing=self.pricing(pid)
            if pricing['price_per_kg'] is None:continue
            previous=self.current.get(pid)
            if previous and previous['id']==view['id']:continue
            saved=self.store.record(view['id'],pid)
            if saved:
                if same_endpoint(saved['endpoint'],job['endpoint']):self.current[pid]=saved
                continue
            total=number('filament_g',view['total_g']);price=number('price_per_kg',pricing['price_per_kg'])
            saved={'id':view['id'],'endpoint':job['endpoint'],'currency':pricing['currency'],
                   'price_per_kg':float(price),'filament_g':float(total),
                   'plastic_cost':money(total*price/1000),'estimated':True,'captured_at':self.clock()}
            self.store.save(pid,job['endpoint'],saved);self.current[pid]=saved
    def public(self,pid):
        record=self.current.get(pid);view=self.filament.views.get(pid)
        if not view or not record or view['id']!=record['id'] or not same_endpoint(record['endpoint'],endpoint(self.manager.entry(pid))):record=None
        return {'pricing':self.pricing(pid),'job':copy.deepcopy(record)}
    def history(self,pid,items):
        records=self.store.records(pid,[item['id'] for item in items])
        for item in items:
            saved=records.get(item['id'])
            item['material_cost']=saved if saved and same_endpoint(saved['endpoint'],item['endpoint']) else None
        return items
