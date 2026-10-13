"""Lightweight nozzle telemetry checks. No polling or printer commands here."""
from collections import deque
import math
import json
import time
import uuid
from .config import atomic_json


def valid(value):
    return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value)


class TemperatureWatch:
    INTERVAL=5.
    DEVIATION=10.
    DURATION=30.
    JUMP=10.
    WINDOW=60.

    def __init__(self, notifications, path=None, *, clock=time.monotonic, wall_clock=time.time):
        self.notifications=notifications;self.clock=clock;self.wall_clock=wall_clock
        self.states={};self.views={};self.last_tick=None
        self.path=path;self.settings={}
        if path is not None and path.exists():
            data=json.loads(path.read_text(encoding='utf-8-sig'))
            settings=data.get('printers',{})
            if not isinstance(settings,dict) or any(type(value) is not bool for value in settings.values()):
                raise ValueError('Некорректные настройки контроля температуры')
            self.settings=settings

    def enabled(self,pid):return self.settings.get(pid,False)

    def configure(self,pid,enabled):
        if type(enabled) is not bool:raise ValueError('Некорректные настройки контроля температуры')
        updated={**self.settings,pid:enabled}
        if self.path is not None:atomic_json(self.path,{'schema_version':1,'printers':updated})
        self.settings=updated
        self.reset(pid,'waiting' if enabled else 'disabled')
        return self.public(pid)

    def reset(self,pid,phase='waiting'):
        self.states.pop(pid,None)
        self.views[pid]={'phase':phase,'active':False,'kind':None,'interval_seconds':self.INTERVAL,
                         'threshold_c':self.DEVIATION,'duration_seconds':self.DURATION}

    def public(self,pid):
        return {'enabled':self.enabled(pid),**dict(self.views.get(pid,{'phase':'waiting' if self.enabled(pid) else 'disabled','active':False,'kind':None,
                   'interval_seconds':self.INTERVAL,'threshold_c':self.DEVIATION,'duration_seconds':self.DURATION}))}

    def update(self,items):
        now=self.clock()
        if self.last_tick is not None and now-self.last_tick<self.INTERVAL:
            return
        self.last_tick=now
        ids={item['printer']['id'] for item in items}
        for pid in list(self.states):
            if pid not in ids:self.states.pop(pid,None);self.views.pop(pid,None)
        for item in items:
            pid=item['printer']['id']
            if not self.enabled(pid):
                self.reset(pid,'disabled');continue
            link=item.get('connection',{});temps=item.get('temperatures',{})
            actual=temps.get('nozzle');target=temps.get('nozzle_target')
            if not link.get('online') or link.get('stale') or not temps.get('nozzle_fresh') or not valid(actual) or not valid(target):
                self.reset(pid);continue
            if target<50:
                self.reset(pid,'off');continue
            address=tuple(item['printer'].get(key) for key in ('ip','protocol','api_port'))
            signature=(address,link.get('sessions'),target)
            state=self.states.get(pid)
            if state is None or state['signature']!=signature:
                state={'signature':signature,'started':now,'last':None,'sample_id':None,'last_at':None,
                       'near':0,'stable':False,'bad_since':None,'jumps':deque(),
                       'samples':deque(),'active':None,'recovered_since':None}
                self.states[pid]=state
            sample_id=temps.get('nozzle_sample')
            if sample_id is not None and sample_id==state['sample_id']:
                continue
            state['sample_id']=sample_id
            # Gaps never count as continuous deviation or an abrupt jump.
            if state['last_at'] is not None and now-state['last_at']>self.INTERVAL*2:
                state.update(last=None,near=0,stable=False,bad_since=None)
                state['jumps'].clear();state['samples'].clear()
            deviation=abs(actual-target)
            state['near']=state['near']+1 if deviation<=3 else 0
            if state['near']>=3:state['stable']=True
            kind=None
            if state['stable']:
                if state['last'] is not None and abs(actual-state['last'])>=self.JUMP:
                    state['jumps'].append(now)
                while state['jumps'] and state['jumps'][0]<now-self.WINDOW:state['jumps'].popleft()
                if deviation>=self.DEVIATION:
                    if state['bad_since'] is None:state['bad_since']=now
                    if now-state['bad_since']>=self.DURATION:kind='deviation'
                else:state['bad_since']=None
                if len(state['jumps'])>=2:kind='jumps'
            # A heater that never reached the target must also be observable.
            # Wait five minutes at an unchanged target, not ordinary warm-up.
            elif now-state['started']>=300 and deviation>=self.DEVIATION:
                kind='not_heated'
            state['samples'].append((now,actual))
            while state['samples'] and state['samples'][0][0]<now-self.WINDOW:state['samples'].popleft()
            state.update(last=actual,last_at=now)
            if kind and state['active'] is None:
                state['active']=kind
                values=[sample[1] for sample in state['samples']]
                self.notifications.enqueue({'id':uuid.uuid4().hex,'kind':'temperature_warning',
                    'created':self.wall_clock(),'printer_id':pid,'name':item['printer']['name'],
                    'file':item.get('print',{}).get('file'),'temperature_kind':kind,
                    'actual_c':actual,'target_c':target,'minimum_c':min(values),'maximum_c':max(values)})
            healthy=deviation<=5 and not state['jumps']
            if healthy:
                if state['recovered_since'] is None:state['recovered_since']=now
                if now-state['recovered_since']>=30:state['active']=None
            else:state['recovered_since']=None
            values=[sample[1] for sample in state['samples']]
            self.views[pid]={'phase':'monitoring' if state['stable'] else 'heating',
                            'active':state['active'] is not None,'kind':state['active'],
                            'actual_c':actual,'target_c':target,'minimum_c':min(values),'maximum_c':max(values),
                            'interval_seconds':self.INTERVAL,'threshold_c':self.DEVIATION,'duration_seconds':self.DURATION}
