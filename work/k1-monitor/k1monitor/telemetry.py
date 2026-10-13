"""Merge partial printer updates and keep connection freshness explicit."""
from __future__ import annotations

import math
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

FIELDS = frozenset({
    "model", "modelVersion", "state", "deviceState", "nozzleTemp",
    "targetNozzleTemp", "bedTemp0", "targetBedTemp0", "boxTemp",
    "printProgress", "dProgress", "printFileName", "printJobTime",
    "printLeftTime", "layer", "TotalLayer", "err", "lightSw",
    "materialStatus", "modelFanPct", "caseFanPct", "auxiliaryFanPct",
    "webrtcSupport", "video", "withSelfTest", "pause", "paused", "isPaused",
    "curFeedratePct", "objects", "excluded_objects", "current_object",
    "printId", "printStartTime",
})


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def integer(value: Any) -> int | None:
    result = number(value)
    return int(result) if result is not None and result.is_integer() else None

def printer_error(value):
    raw=value if isinstance(value,dict) else {'errcode':value}
    code=integer(raw.get('errcode'))
    key=raw.get('key')
    key=str(key)[:64] if isinstance(key,(str,int)) and not isinstance(key,bool) else None
    message=raw.get('value',raw.get('msg',raw.get('message','')))
    if isinstance(message,str):
        try:
            parsed=json.loads(message)
            if isinstance(parsed,dict):message=parsed.get('msg',parsed.get('message',message))
        except (ValueError,TypeError):pass
    message=message[:2048] if isinstance(message,str) else ''
    active=code not in (None,0) or key not in (None,'','0')
    return {'code':code,'key':key,'message':message,'active':active}


class Telemetry:
    def __init__(self, host: str, firmware: str, stale_after: float = 15, *, persistent_status: bool = False):
        self.host = host
        self.configured_firmware = firmware
        self.stale_after = stale_after
        # Creality sends unchanged state/error fields once, then only deltas.
        # Other adapters refresh authoritative status through periodic queries.
        self.persistent_status = persistent_status
        self.data: dict[str, Any] = {}
        self.transport_open = False
        self.last_rx: float | None = None
        self.last_link_rx: float | None = None
        self.last_seen: str | None = None
        self.message_count = 0
        self.connection_count = 0
        self.last_error: str | None = None
        self.field_times = {}

    def begin_session(self) -> None:
        # A new session cannot inherit an old job's progress or file name.
        self.data = {}
        self.field_times = {}
        self.last_rx = None
        self.last_link_rx = None
        self.last_seen = None
        self.transport_open = True
        self.last_error = None
        self.connection_count += 1

    def disconnected(self, error: str | None = None) -> None:
        self.transport_open = False
        self.last_error = error

    def ingest(self, payload: Any, now: float | None = None) -> bool:
        if not isinstance(payload, dict):
            return False
        updates = {key: value for key, value in payload.items() if key in FIELDS}
        if not updates:
            return False
        if 'printFileName' in updates and updates['printFileName'] != self.data.get('printFileName'):
            for key in ('objects','excluded_objects','current_object'): self.data.pop(key,None)
        self.data.update(updates)
        self.last_rx = time.monotonic() if now is None else now
        self.field_times.update({key:self.last_rx for key in updates})
        self.last_link_rx = self.last_rx
        self.last_seen = datetime.now(timezone.utc).isoformat()
        self.message_count += 1
        self.last_error = None
        return True

    def heartbeat(self, now: float | None = None):
        # Heartbeats prove the connection, but never refresh printer parameters.
        self.last_link_rx = time.monotonic() if now is None else now

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        age = None if self.last_rx is None else max(0, now - self.last_rx)
        link_age = None if self.last_link_rx is None else max(0, now - self.last_link_rx)
        online = self.transport_open and age is not None and link_age is not None and link_age <= self.stale_after
        fresh = online and age <= self.stale_after
        d = self.data
        progress = number(d.get("printProgress"))
        if progress is None:
            progress = number(d.get("dProgress"))
        if progress is not None and not 0 <= progress <= 100:
            progress = None
        error = printer_error(d.get("err"))
        error_code = error['code']
        error_runout = error['key'] in ('116','2839') or error_code in (116,2839)
        runout = integer(d.get('materialStatus')) == 1 or error_runout
        other_error = error['active'] and not error_runout
        signal_times = [self.field_times.get(key,-1e9) for key in (('err',) if error_runout else ('materialStatus',))]
        pause_times = [self.field_times.get(key,-1e9) for key in ('state','pause','paused','isPaused')]
        if runout and not error['active']:
            error = {'code':116,'key':'116','message':'Филамент закончился','active':True}
            error_code = 116
        state_code = integer(d.get("state"))
        self_test = number(d.get("withSelfTest"))
        paused = state_code==5 or (state_code==1 and any(integer(d.get(key))==1 for key in ('pause','paused','isPaused')))
        status_fresh = now-max(signal_times)<=self.stale_after and now-max(pause_times)<=self.stale_after
        if self.persistent_status:
            status_fresh = fresh and bool(signal_times) and max(signal_times)>-1e9 and max(pause_times)>-1e9
        self_test_complete = self_test is None or self_test in (0,100)
        unload_block_reason = ('Филамент закончился · выгрузка заблокирована другой ошибкой' if other_error else
                               'Филамент закончился · ждём завершения самопроверки' if not self_test_complete else
                               'Филамент закончился · ждём актуальные данные состояния' if not status_fresh else '')
        filename = d.get("printFileName")
        filename = filename if isinstance(filename, str) else None
        status = "unknown"
        if online:
            if error['active']:
                status = "error"
            elif self_test is not None and 1 <= self_test <= 99:
                status = "self_testing"
            elif state_code == 5 or (state_code == 1 and any(integer(d.get(key)) == 1 for key in ("pause", "paused", "isPaused"))):
                status = "paused"
            elif state_code == 1:
                status = "printing"
            elif state_code == 4:
                status = "stopped"
            elif state_code == 2:
                status = 'completed'
                progress = 100
            elif state_code == 3:
                status = 'failed'
            elif state_code in (6,7,8):
                status = {6:'pausing',7:'stopping',8:'restoring'}[state_code]
            elif progress == 100 and filename:
                status = "completed"
            elif state_code == 0:
                status = "preparing" if filename and progress is not None and progress < 100 else "idle"
        version_info = d.get("modelVersion")
        objects = d.get('objects',[])
        excluded = d.get('excluded_objects',[])
        for key in ('objects','excluded'):
            value = objects if key=='objects' else excluded
            if isinstance(value,str):
                try: value=json.loads(value)
                except ValueError: value=[]
            if not isinstance(value,list): value=[]
            if key=='objects': objects=value
            else: excluded=value
        objects = [{'name':item['name'],'excluded':item['name'] in excluded,'current':item['name']==d.get('current_object')}
                   for item in objects[:500] if isinstance(item,dict) and isinstance(item.get('name'),str)
                   and 0<len(item['name'])<=256 and not any(ord(c)<32 for c in item['name'])]
        match = re.search(r"DWIN sw ver:V?([^;]+)", version_info) if isinstance(version_info, str) else None
        return {
            "schema_version": 1,
            "printer": {
                "id": "k1max", "name": d.get("model") or "K1 Max", "model": d.get("model"), "ip": self.host,
                "firmware": match.group(1) if match else self.configured_firmware,
                "firmware_confirmed": bool(match),
            },
            "connection": {
                "online": online, "transport_open": self.transport_open,
                "stale": not fresh, "last_seen": self.last_seen,
                "age_seconds": round(age, 1) if age is not None else None,
                "last_error": self.last_error, "sessions": self.connection_count,
                "messages": self.message_count,
            },
            "status": status, "state_code": state_code,
            "temperatures": {
                "nozzle": number(d.get("nozzleTemp")), "nozzle_target": number(d.get("targetNozzleTemp")),
                "bed": number(d.get("bedTemp0")), "bed_target": number(d.get("targetBedTemp0")),
                "chamber": number(d.get("boxTemp")),
                "nozzle_sample": self.field_times.get('nozzleTemp'),
                "nozzle_fresh":fresh and now-self.field_times.get('nozzleTemp',float('-inf'))<=self.stale_after and
                               ((self.persistent_status and 'targetNozzleTemp' in self.field_times) or
                                now-self.field_times.get('targetNozzleTemp',float('-inf'))<=self.stale_after),
            },
            "print": {
                "file": filename, "progress_percent": progress,
                'job_id':str(d.get('printId') or d.get('printStartTime'))[:128] if d.get('printId') or d.get('printStartTime') else None,
                "elapsed_seconds": integer(d.get("printJobTime")),
                "remaining_seconds": integer(d.get("printLeftTime")),
                "layer": integer(d.get("layer")), "total_layers": integer(d.get("TotalLayer")),
            },
            "error_code": error_code,
            "error": error,
            'light_on':bool(integer(d.get('lightSw'))) if integer(d.get('lightSw')) in (0,1) else None,
            'speed_percent':number(d.get('curFeedratePct')),
            'filament_runout':runout,
            'unload_eligible':not other_error and status_fresh and self_test_complete,
            'unload_block_reason':unload_block_reason,
            'paused_confirmed':paused,
            'objects':objects,
            "camera": {"present": integer(d.get("video")) == 1, "webrtc": integer(d.get("webrtcSupport")) == 1},
            "observed_fields": sorted(d),
        }
