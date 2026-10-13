"""Stock LAN commands, bounded to the UI's printer/session/job selection.

Command keys are taken from installed Creality Print 7.2. Withdrawal strokes
follow CrealityOfficial/K1_Series_Klipper; heating is verified separately.
No hardware-changing commands from this module are exercised by probes.
"""
import asyncio
import json
import math
import time

def temperature_params(kind,value):
    if kind not in ('nozzle','bed') or type(value) is not int or not 0<=value<=(300 if kind=='nozzle' else 100):raise ValueError('Некорректная температура')
    return {'nozzleTempControl':value} if kind=='nozzle' else {'bedTempControl':{'num':0,'val':value}}


def unload_gcode(restore_temperature=None):
    """Stock K1 withdrawal strokes; heating is handled by the guarded planner.

    No XY/Z movement, calibration, homing or resume. An optional captured
    nozzle target is restored only after M400; the default primitive has no
    heater changes. The
    legacy current macros are absent on newer stock firmware, so current
    settings are left unchanged. Reference: CrealityOfficial gcode_macro.cfg.
    """
    strokes=['E100 F300','E-15 F3000','E-22.4700 F2400','E-6.4200 F1200','E-3.2100 F720','E5 F356','E-5 F384','E5 F412','E-5 F440','E5 F467','E-5 F495','E5 F523','E-5 F3000','E-15 F3000']
    program=['SAVE_GCODE_STATE NAME=K1MONITOR_UNLOAD','M83',*['G1 '+stroke for stroke in strokes],'M400','RESTORE_GCODE_STATE NAME=K1MONITOR_UNLOAD']
    if restore_temperature is not None:
        if type(restore_temperature) not in (int,float) or not math.isfinite(restore_temperature) or not 0<=restore_temperature<=300:
            raise ValueError('Неизвестна прежняя целевая температура сопла')
        # Executed by the printer only after all withdrawal moves have finished.
        # Never use a timer or a second network request to guess completion.
        program.append(f'M104 S{restore_temperature:g}')
    return '\n'.join(program)


class UnloadHeating:
    """Pure wait/cancel decisions after one request to set the nozzle target."""
    def __init__(self):self.pending=None;self.message='';self.ready_since=None;self.target_seen=False;self.restore_temperature=None
    @staticmethod
    def identity(s):return (s['printer']['ip'],s['connection']['sessions'],s['print']['file'],s['print'].get('job_id'))
    def start(self,s,temperature,now):
        previous=s['temperatures'].get('nozzle_target')
        self.restore_temperature=previous if type(previous) in (int,float) and math.isfinite(previous) and 0<=previous<=300 else None
        self.pending=(self.identity(s),temperature,now);self.ready_since=None;self.target_seen=False
        self.message=f'Нагрев для выгрузки до {temperature} °C · печать на паузе'
    def cancel(self,text):self.pending=None;self.message=text;self.ready_since=None;self.restore_temperature=None
    def tick(self,s,enabled,temperature,now):
        if not self.pending:return False
        identity,target,started=self.pending
        if (not enabled or temperature!=target or self.identity(s)!=identity or not s['connection']['online'] or s['connection']['stale'] or not s.get('filament_runout') or not s.get('paused_confirmed') or not s.get('unload_eligible')):
            self.cancel('Выгрузка отменена: состояние или настройка изменились');return False
        if now-started>600:self.cancel('Выгрузка отменена: температура не достигнута за 10 минут');return False
        temps=s['temperatures'];requested=temps.get('nozzle_target');actual=temps.get('nozzle')
        if requested is not None and abs(requested-target)<=1:self.target_seen=True
        elif self.target_seen or now-started>15:
            self.cancel('Выгрузка отменена: целевая температура изменена или не подтверждена');return False
        if actual is None or not self.target_seen or not temps.get('nozzle_fresh',False) or abs(actual-target)>3:
            self.ready_since=None;return False
        if self.ready_since is None:self.ready_since=now
        if now-self.ready_since<2:return False
        self.pending=None;self.message='Команда выгрузки отправляется · печать остаётся на паузе';return True


class RunoutPlan:
    """Pure decision logic. One attempt per runout episode; no startup replay."""
    def __init__(self):
        self.enabled = False
        self.identity = None
        self.latched = True
        self.due = None
        self.message = 'Автовыгрузка выключена'

    def tick(self, snapshot, enabled, now):
        identity = (snapshot['printer']['ip'], snapshot['connection']['sessions'], snapshot['print']['file'],snapshot['print'].get('job_id'))
        runout = snapshot.get('filament_runout', False)
        fresh = snapshot['connection']['online'] and not snapshot['connection']['stale']
        paused = snapshot.get('paused_confirmed', False)
        if identity != self.identity or enabled != self.enabled:
            self.identity, self.enabled = identity, enabled
            self.due = None
            self.latched = bool(runout)
        if not enabled:
            self.due = None; self.message = 'Автовыгрузка выключена'; return False
        if not fresh:
            if runout: self.latched = True
            self.due = None; self.message = 'Ожидаем свежие данные'; return False
        if not runout:
            self.latched = False; self.due = None; self.message = 'Включена · ждём окончания филамента'; return False
        if self.latched:
            self.message = self.message if self.message.startswith('Команда') else 'Ошибка уже была активна · повторной выгрузки нет'
            return False
        if self.due is None:
            self.due = now + 5
        if not snapshot['print']['file']:
            self.message = 'Филамент закончился · нет текущего файла печати'; return False
        if not paused:
            self.message = 'Филамент закончился · ждём подтверждения паузы'; return False
        if not snapshot.get('unload_eligible',False):
            self.message = snapshot.get('unload_block_reason') or 'Филамент закончился · выгрузка пока недоступна'; return False
        if now < self.due:
            self.message = f'Выгрузка через {max(1, int(self.due-now+0.99))} с'; return False
        self.latched = True; self.due = None
        self.message = 'Команда выгрузки отправляется · печать остаётся на паузе'
        return True

    def public(self):
        return {'enabled': self.enabled, 'message': self.message}


class Operations:
    def _unload_gcode(self):
        if self.unload_heating.restore_temperature is None:
            raise ValueError('Неизвестна прежняя целевая температура сопла')
        return unload_gcode(self.unload_heating.restore_temperature)

    def _can_restore_unload_temperature(self,snapshot):
        target=snapshot['temperatures'].get('nozzle_target')
        return snapshot['temperatures'].get('nozzle_fresh',False) and type(target) in (int,float) and math.isfinite(target) and 0<=target<=300

    def _unload_sent_message(self):
        return 'Команда выгрузки с возвратом прежней температуры отправлена · печать остаётся на паузе'
    async def list_objects(self):
        async with self._objects_lock:
            snapshot=self.snapshot()
            self.check_selection(snapshot['connection']['sessions'],self.state.host)
            self._objects_event.clear()
            try:
                await asyncio.wait_for(self._send(self._ws,json.dumps({'method':'get','params':{'printObjects':1}})),3)
                await asyncio.wait_for(self._objects_event.wait(),5)
            except Exception as exc:
                raise ValueError('Принтер не передал список моделей. Проверьте маркировку объектов в слайсере.') from exc
            return self.snapshot()

    def check_selection(self, expected_session, expected_ip, expected_file=None, *, job=False, expected_job=None):
        snap = self.snapshot()
        if self._ws is None or not snap['connection']['online'] or snap['connection']['stale']:
            raise ValueError('Нет свежих данных принтера')
        if self.state.host != expected_ip or snap['connection']['sessions'] != expected_session:
            raise ValueError('Подключение изменилось. Обновите страницу.')
        if job and (snap['print']['file'] != expected_file or snap['print'].get('job_id') != expected_job):
            raise ValueError('Задание изменилось. Обновите страницу.')
        return snap

    async def stock_command(self, *, action, value, request_id, expected_session, expected_ip, expected_file=None, expected_job=None):
        async with self._control_lock:
            signature = (action, value, expected_session, expected_ip, expected_file,expected_job)
            saved = self._operation_requests.get(request_id)
            if saved:
                if saved[1] != signature: raise ValueError('Идентификатор команды уже использован')
                return saved[2]
            snap = self.check_selection(expected_session, expected_ip, expected_file, job=action != 'light',expected_job=expected_job)
            if action=='exclude':
                await self.list_objects()
                snap=self.check_selection(expected_session,expected_ip,expected_file,job=True,expected_job=expected_job)
            if action == 'light' and type(value) is bool and 'lightSw' in snap['observed_fields']:
                params = {'lightSw': int(value)}
            elif action == 'speed' and type(value) is int and 10 <= value <= 200 and snap['controls']['speed']:
                params = {'setFeedratePct': value}
            elif action in ('nozzle','bed') and type(value) is int and 0<=value<=(300 if action=='nozzle' else 100) and snap['controls'].get(action):
                params=temperature_params(action,value)
            elif action == 'exclude' and type(value) is str and snap['controls']['exclude']:
                model = next((obj for obj in snap['objects'] if obj['name'] == value), None)
                if not model or model['excluded'] or model['current']: raise ValueError('Модель недоступна для исключения: уже исключена или печатается сейчас')
                params = {'excludeObjects': [value]}
            else:
                raise ValueError('Команда недоступна в текущем состоянии')
            result = {'phase':'sent', 'message':'Команда отправлена. Проверьте обновлённое состояние принтера.'}
            self._operation_requests[request_id] = (time.monotonic(), signature, result)
            try:
                await asyncio.wait_for(self._send(self._ws, json.dumps({'method':'set','params':params})), 3)
            except Exception:
                result.update(phase='unconfirmed', message='Подтверждение не получено. Автоматического повтора нет.')
            return result

    async def delete_files(self, *, file_ids, revision, request_id, expected_session, expected_ip):
        async with self._control_lock:
            signature = ('delete', tuple(file_ids), revision, expected_session, expected_ip)
            saved = self._operation_requests.get(request_id)
            if saved:
                if saved[1] != signature: raise ValueError('Идентификатор команды уже использован')
                return saved[2]
            self.check_selection(expected_session, expected_ip)
            catalog = await self.list_files(refresh=True)
            snap = self.check_selection(expected_session, expected_ip)
            entries = [catalog.get(key) for key in file_ids]
            if not entries or len(set(file_ids)) != len(file_ids) or catalog.revision != revision or any(item is None for item in entries):
                raise ValueError('Список файлов изменился. Обновите его.')
            def protected(item, snapshot):
                active = snapshot['state_code'] not in (0,2,3,4) or snapshot['status']=='preparing'
                pending = self._command and self._command.get('action')=='start' and self._command.get('phase') in ('pending','unconfirmed') and self._command.get('_file')==item['path']
                return bool(pending or (snapshot['print']['file'] in (item['path'],item['name']) and active))
            if any(protected(item,snap) for item in entries):
                raise ValueError('Нельзя удалить файл текущей печати')
            result = {'phase':'sent', 'message':'Запросы удаления отправлены. Обновите список для проверки.', 'attempted':[]}
            self._operation_requests[request_id] = (time.monotonic(), signature, result)
            for item in entries:
                try:
                    snap = self.check_selection(expected_session, expected_ip)
                    if protected(item,snap): raise ValueError('Файл стал текущим заданием')
                    result['attempted'].append(item['id'])
                    await asyncio.wait_for(self._send(self._ws,json.dumps({'method':'set','params':{'opGcodeFile':'deleteprt:'+item['path']}})),3)
                    await asyncio.sleep(.01)
                except Exception:
                    result.update(phase='unconfirmed',message='Удаление остановлено. Обновите список: часть файлов могла удалиться. Автоматического повтора нет.')
                    break
            self.files.updated = 0
            return result

    async def _automation_loop(self):
        while True:
            snap = self.snapshot()
            now = time.monotonic()
            self._operation_requests = {key:entry for key,entry in self._operation_requests.items() if now-entry[0] < 86400}
            ready=self.unload_heating.tick(snap,self.auto_unload,self.auto_unload_temperature,now)
            if not snap['filament_runout'] and not self.unload_heating.pending:self.unload_heating.message=''
            if ready:
                async with self._control_lock:
                    current=self.snapshot()
                    if self.auto_unload and self.auto_unload_temperature is not None and self._ws is not None and current['filament_runout'] and current['paused_confirmed'] and current['unload_eligible'] and current['connection']['online'] and not current['connection']['stale'] and UnloadHeating.identity(current)==self.runout_plan.identity and current['temperatures'].get('nozzle_fresh') and abs((current['temperatures']['nozzle'] or 0)-self.auto_unload_temperature)<=3 and abs((current['temperatures']['nozzle_target'] or 0)-self.auto_unload_temperature)<=1 and not (self._command and self._command['phase']=='pending'):
                        try:
                            await asyncio.wait_for(self._send(self._ws,json.dumps({'method':'set','params':{'gcodeCmd':self._unload_gcode()}})),3)
                            self.unload_heating.message=self._unload_sent_message()
                        except Exception:self.unload_heating.message='Выгрузка не подтверждена · автоматического повтора нет'
                    else:self.unload_heating.message='Выгрузка отменена: состояние изменилось'
            if self.runout_plan.tick(snap, self.auto_unload, now):
                async with self._control_lock:
                    current = self.snapshot()
                    if (self.auto_unload and self._ws is not None and current['filament_runout'] and current['paused_confirmed'] and current['unload_eligible']
                            and current['connection']['online'] and not current['connection']['stale']
                            and (current['printer']['ip'],current['connection']['sessions'],current['print']['file'],current['print'].get('job_id')) == self.runout_plan.identity
                            and not (self._command and self._command['phase']=='pending')):
                        try:
                            if not self._can_restore_unload_temperature(current):
                                raise ValueError('Неизвестна прежняя целевая температура сопла')
                            self.unload_heating.start(current,self.auto_unload_temperature,now)
                            params=temperature_params('nozzle',self.auto_unload_temperature)
                            await asyncio.wait_for(self._send(self._ws,json.dumps({'method':'set','params':params})),3)
                            self.runout_plan.message = 'Команда отправлена · печать на паузе'
                        except Exception:
                            self.unload_heating.cancel('Нагрев/выгрузка не подтверждены · автоматического повтора нет')
                            self.runout_plan.message = 'Команда выгрузки не подтверждена · автоматического повтора нет'
                    else:
                        self.runout_plan.message = 'Команда отменена: состояние принтера изменилось'
            await asyncio.sleep(.5)
