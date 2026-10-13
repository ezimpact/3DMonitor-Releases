"""Persistent per-printer spool ledger. This service only reads printer files."""
import asyncio
import copy
from contextlib import contextmanager
import json
import hashlib
import logging
import sqlite3
import tempfile
import time
import uuid
from urllib.parse import quote
import zlib

import httpx

from .activity import endpoint, same_endpoint
from .files import GCODE_ROOT, safe_file_path
from .gcode_usage import consumption, finite, parse_gcode, GcodeUsageError

LOG = logging.getLogger(__name__)
MAX_BYTES = 512 * 1024 * 1024
DEFAULT = {'enabled': True, 'capacity_g': 1000., 'density': 1.24,
           'diameter': 1.75, 'remaining_g': None, 'spool_id': None,
           'spool_started': None, 'origin': 'unknown', 'pending': None}


class FilamentStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS printers (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, printer TEXT NOT NULL, payload TEXT NOT NULL, profile BLOB)')
            db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, profile BLOB NOT NULL, used REAL NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def printer(self, pid):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM printers WHERE id=?', (pid,)).fetchone()
        return {**copy.deepcopy(DEFAULT), **(json.loads(row[0]) if row else {})}

    def job(self, jid):
        with self.connect() as db:
            row = db.execute('SELECT payload,profile FROM jobs WHERE id=?', (jid,)).fetchone()
        if not row:
            return None
        result = json.loads(row[0])
        result['profile'] = json.loads(zlib.decompress(row[1])) if row[1] else None
        return result

    def save(self, pid, state, job=None):
        # Remaining spool weight and its job checkpoint commit together.
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO printers VALUES (?,?)', (pid, json.dumps(state)))
            if job:
                payload = {key: value for key, value in job.items() if key != 'profile'}
                profile = zlib.compress(json.dumps(job['profile']).encode()) if job.get('profile') else None
                db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)',
                           (job['id'], pid, json.dumps(payload), profile))
                db.execute('UPDATE jobs SET profile=NULL WHERE rowid NOT IN (SELECT rowid FROM jobs ORDER BY rowid DESC LIMIT 100)')

    def cached(self, key):
        with self.connect() as db:
            row=db.execute('SELECT profile FROM cache WHERE key=?',(key,)).fetchone()
            if row:db.execute('UPDATE cache SET used=? WHERE key=?',(time.time(),key))
        return json.loads(zlib.decompress(row[0])) if row else None

    def cache(self, keys, profile):
        blob=zlib.compress(json.dumps(profile).encode())
        with self.connect() as db:
            for key in keys:db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',(key,blob,time.time()))
            db.execute('DELETE FROM cache WHERE key NOT IN (SELECT key FROM cache ORDER BY used DESC LIMIT 100)')


class FilamentService:
    def __init__(self, manager, tracker, notifications, path, *, clock=time.time, loader=None):
        self.manager = manager; self.tracker = tracker; self.notifications = notifications
        self.store = FilamentStore(path); self.clock = clock
        self.states = {}; self.jobs = {}; self.tasks = {}; self.saved = {}; self.views = {}
        self.slots = asyncio.Semaphore(2)
        self.loader = loader or self.read_profile

    def state(self, pid):
        if pid not in self.states:
            self.states[pid] = self.store.printer(pid)
        return self.states[pid]

    def public(self, pid):
        state = self.state(pid)
        result = {key: state.get(key) for key in ('enabled', 'capacity_g', 'density', 'diameter',
                  'remaining_g', 'spool_id', 'spool_started', 'origin')}
        result['awaiting_resume'] = bool(state.get('pending'))
        result['job'] = copy.deepcopy(self.views.get(pid))
        return result

    def configure(self, pid, changes):
        self.manager.get(pid)
        state = self.state(pid)
        allowed = {'enabled', 'capacity_g', 'density', 'diameter', 'remaining_g'}
        if not changes or not set(changes) <= allowed:
            raise ValueError('Некорректная настройка катушки')
        ranges = {'capacity_g': (50, 10000), 'remaining_g': (0, 10000),
                  'density': (.5, 3), 'diameter': (1, 4)}
        for key, value in changes.items():
            if key == 'enabled':
                if type(value) is not bool:
                    raise ValueError('Некорректная настройка катушки')
            elif value is None and key == 'remaining_g':
                pass
            elif finite(value, *ranges[key]) is None:
                raise ValueError('Некорректные параметры филамента')
        changed_material = any(key in changes and changes[key] != state[key] for key in ('density', 'diameter'))
        if changed_material and self.jobs.get(pid) and not self.jobs[pid].get('closed'):
            raise ValueError('Плотность и диаметр меняйте между заданиями')
        was_enabled = state['enabled']
        state.update(changes)
        if 'remaining_g' in changes:
            state['origin'] = 'manual' if state['remaining_g'] is not None else 'unknown'
            if not state.get('spool_id') and state['remaining_g'] is not None:
                state.update(spool_id=uuid.uuid4().hex, spool_started=self.clock())
            job = self.jobs.get(pid)
            if job:
                # The correction is a weight NOW, not before the current print.
                job['charged'] = job.get('last_upper', 0)
                job['warned_spool'] = None
        if changes.get('enabled') is False:
            state['pending'] = None
            if pid in self.tasks:self.tasks[pid].cancel()
        elif changes.get('enabled') is True and not was_enabled:
            # Prints made with tracking disabled cannot be reconstructed safely.
            state['remaining_g'] = changes.get('remaining_g')
            state['origin'] = 'manual' if state['remaining_g'] is not None else 'unknown'
        self.persist(pid, force=True)
        return self.public(pid)

    def persist(self, pid, force=False):
        now = self.clock()
        if force or now - self.saved.get(pid, 0) >= 15:
            self.store.save(pid, self.state(pid), self.jobs.get(pid))
            self.saved[pid] = now

    def observe(self, items, previous_jobs=None):
        previous_jobs = previous_jobs or {}
        live = {item['printer']['id'] for item in items}
        for pid, task in list(self.tasks.items()):
            if pid not in live:
                task.cancel()
        for item in items:
            pid = item['printer']['id']; state = self.state(pid)
            address = endpoint(item['printer'])
            if state.get('endpoint') and not same_endpoint(state['endpoint'], address):
                state.update(remaining_g=None, origin='unknown', pending=None)
            state['endpoint'] = address
            if not state['enabled']:
                self.views.pop(pid, None)
                continue
            if not item.get('connection', {}).get('online') or item['connection'].get('stale'):
                continue
            tracked = self.tracker.running.get(pid) or previous_jobs.get(pid)
            if not tracked or not same_endpoint(tracked['endpoint'], endpoint(item['printer'])):
                if state.get('pending') and item.get('status') in ('idle', 'completed', 'failed', 'stopped'):
                    state['pending'] = None; self.persist(pid, True)
                continue
            job = self.jobs.get(pid)
            if job is None or job['id'] != tracked['id']:
                if job and not job.get('closed'):
                    # A missed/cancelled job may have consumed more since its last
                    # observation. Reserve its unobserved remainder conservatively.
                    self.charge(pid, job, (job.get('profile') or {}).get('total_g', job.get('last_upper', 0)))
                    if not job.get('profile'):
                        state.update(remaining_g=None, origin='unknown')
                    job['closed'] = True; self.persist(pid, True)
                task = self.tasks.get(pid)
                if task:
                    task.cancel()
                job = self.store.job(tracked['id']) or {'id': tracked['id'], 'endpoint': tracked['endpoint'],
                        'file': tracked['file'], 'charged': 0., 'last_upper': 0.,
                        'profile': None, 'attempts': 0, 'last_attempt': 0,
                        'warned_spool': None, 'closed': False, 'analysis_error': None}
                self.jobs[pid] = job
                if state.get('pending') and state['pending']['job'] != job['id']:
                    state['pending'] = None
                self.persist(pid, True)
            self.process(pid, item, job)
            if not job.get('profile') and not job.get('closed') and pid not in self.tasks:
                delay = (0, 5, 30)[min(job['attempts'], 2)]
                if job['attempts'] < 3 and self.clock() - job['last_attempt'] >= delay:
                    task = asyncio.create_task(self.analyze(pid, job))
                    self.tasks[pid] = task
                    task.add_done_callback(lambda done, key=pid: self.tasks.pop(key, None) if self.tasks.get(key) is done else None)

    def charge(self, pid, job, upper):
        upper = max(job.get('last_upper', 0), upper)
        delta = max(0., upper - job.get('charged', 0))
        state = self.state(pid)
        if state['remaining_g'] is not None:
            state['remaining_g'] = max(0., state['remaining_g'] - delta)
        job['charged'] = upper; job['last_upper'] = upper

    def process(self, pid, item, job):
        state = self.state(pid); profile = job.get('profile'); details = item.get('print', {})
        status = item.get('status'); completed = status == 'completed' or status == 'error' and item.get('state_code') == 2
        terminal = status in ('completed', 'failed', 'stopped', 'idle')
        lower, upper, method = consumption(profile, details, completed) if profile else (0., 0., None)
        runout = bool(item.get('filament_runout'))
        paused = bool(item.get('paused_confirmed')) or status == 'paused' or status == 'error' and item.get('state_code') == 5
        if runout and paused and not job.get('closed'):
            if not state.get('pending'):
                state['pending'] = {'job': job['id'], 'endpoint': job['endpoint'], 'at': self.clock()}
                state['remaining_g'] = 0.; state['origin'] = 'runout'
                job['charged'] = upper; job['last_upper'] = upper
                self.persist(pid, True)
        pending = state.get('pending')
        if pending and pending['job'] == job['id'] and same_endpoint(pending['endpoint'], endpoint(item['printer'])):
            if status == 'printing' and not runout and not item.get('error', {}).get('active'):
                state.update(remaining_g=state['capacity_g'], spool_id=uuid.uuid4().hex,
                             spool_started=self.clock(), origin='automatic', pending=None)
                # Start at the beginning of the resumed layer. This deliberately
                # reserves its whole uncertain remainder on the replacement spool.
                job.update(charged=lower, last_upper=lower, replacement_without_profile=profile is None,
                           warned_spool=None)
                self.persist(pid, True)
            elif terminal:
                state['pending'] = None; self.persist(pid, True)
        if profile and not state.get('pending'):
            if job.pop('replacement_without_profile', False):
                job['charged'] = lower; job['last_upper'] = lower
            self.charge(pid, job, upper)
        self.views[pid] = {'id': job['id'], 'file': job['file'],
                          'analysis': 'ready' if profile else 'unavailable' if job['attempts'] >= 3 else 'reading',
                          'error': job.get('analysis_error'), 'total_g': profile['total_g'] if profile else None,
                          'needed_g': max(0., profile['total_g'] - lower) if profile else None,
                          'method': method, 'source': profile.get('source') if profile else None,
                          'assumed_density': profile.get('assumed_density') if profile else False,
                          'macro_estimate': bool(profile and profile.get('warnings')),
                          'low': False, 'active': not terminal}
        if profile and not terminal and not state.get('pending') and state['remaining_g'] is not None:
            required = max(0., profile['total_g'] - upper)
            reserve = max(10., required * .05)
            low = required + reserve > state['remaining_g']
            self.views[pid]['low'] = low
            spool = state.get('spool_id') or 'initial'
            if low and job.get('warned_spool') != spool:
                job['warned_spool'] = spool
                self.notifications.enqueue({'id': uuid.uuid4().hex, 'kind': 'filament_low',
                    'created': self.clock(), 'printer_id': pid, 'name': item['printer']['name'],
                    'file': job['file'], 'required_g': self.views[pid]['needed_g'],
                    'available_g': state['remaining_g'], 'reserve_g': reserve})
                self.persist(pid, True)
        if terminal:
            if not profile and state['remaining_g'] is not None:
                # An unanalysed job consumed an unknown amount.
                state['remaining_g'] = None; state['origin'] = 'unknown'
            job['closed'] = True; self.persist(pid, True)
        else:
            self.persist(pid)

    async def analyze(self, pid, job):
        job['attempts'] += 1; job['last_attempt'] = self.clock()
        try:
            async with self.slots:
                profile = await self.loader(pid, job)
            current = self.tracker.running.get(pid)
            if self.jobs.get(pid) is not job or not current or current['id'] != job['id']:
                return
            if not same_endpoint(endpoint(self.manager.entry(pid)), job['endpoint']):
                return
            job['profile'] = profile; job['analysis_error'] = None
            self.persist(pid, True)
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, OSError) as error:
            if isinstance(error,GcodeUsageError):job['attempts']=3
            job['analysis_error'] = str(error) if isinstance(error, ValueError) else 'Не удалось прочитать G-code с принтера'
            self.persist(pid, True)
        except Exception:
            LOG.exception('Filament analysis failed')
            job['analysis_error'] = 'Не удалось прочитать G-code с принтера'; self.persist(pid, True)

    async def read_profile(self, pid, job):
        monitor = self.manager.get(pid)
        def unchanged():
            if self.manager.get(pid) is not monitor or not same_endpoint(endpoint(self.manager.entry(pid)), job['endpoint']):
                raise ValueError('Подключение принтера изменилось')
        async with asyncio.timeout(180):
            catalog = await monitor.list_files(refresh=True)
            unchanged()
            name = job['file'].replace('\\', '/')
            normalized = name[len(GCODE_ROOT):] if name.startswith(GCODE_ROOT) else name
            matches = [entry for entry in catalog.files if entry['path'] in (name, normalized, GCODE_ROOT + normalized)]
            if len(matches) != 1:
                raise ValueError('Текущий G-code не найден однозначно в списке файлов')
            entry = dict(matches[0])
            valid = getattr(monitor, 'file_path_valid', safe_file_path)
            if not valid(entry['path']) or entry.get('_read_only'):
                raise ValueError('G-code недоступен для чтения расхода')
            if (entry.get('size_bytes') or 0) > MAX_BYTES:
                raise ValueError('G-code больше 512 МБ')
            settings=self.state(pid)
            identity_key=None
            if entry.get('size_bytes') and entry.get('modified'):
                identity_key=hashlib.sha256(json.dumps([job['endpoint'],entry['path'],entry['size_bytes'],entry['modified'],settings['density'],settings['diameter']]).encode()).hexdigest()
                cached=self.store.cached(identity_key)
                if cached:return cached
            url = monitor.download_url(entry['path']) if hasattr(monitor, 'download_url') else (
                f'http://{monitor.state.host}/downloads/gcode/' + quote(entry['path'][len(GCODE_ROOT):], safe='/'))
            with tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode='w+b') as file:
                options = monitor.http_options() if hasattr(monitor, 'http_options') else {}
                size = 0;digest=hashlib.sha256()
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                             timeout=httpx.Timeout(30, connect=5), **options) as client:
                    async with client.stream('GET', url) as reply:
                        reply.raise_for_status()
                        if any(kind in reply.headers.get('content-type', '').lower() for kind in ('html', 'json')):
                            raise ValueError('Принтер вернул страницу вместо G-code')
                        async for chunk in reply.aiter_bytes(65536):
                            unchanged(); size += len(chunk)
                            if size > MAX_BYTES:
                                raise ValueError('G-code больше 512 МБ')
                            file.write(chunk)
                            digest.update(chunk)
                unchanged()
                if not size or entry.get('size_bytes') is not None and size != entry['size_bytes']:
                    raise ValueError('G-code прочитан не полностью')
                file.seek(0)
                state = self.state(pid)
                content_key=hashlib.sha256(json.dumps([digest.hexdigest(),state['density'],state['diameter']]).encode()).hexdigest()
                cached=self.store.cached(content_key)
                if cached:
                    if identity_key:self.store.cache([identity_key],cached)
                    return cached
                # Keep the temporary file open until the worker has released it,
                # including cancellation during shutdown or a printer edit.
                parsing = asyncio.create_task(asyncio.to_thread(parse_gcode, file, state['density'], state['diameter']))
                try:
                    result = await asyncio.shield(parsing)
                except asyncio.CancelledError:
                    await parsing
                    raise
                unchanged()
                self.store.cache([content_key]+([identity_key] if identity_key else []),result)
                return result

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for pid in self.states:
            self.persist(pid, True)
