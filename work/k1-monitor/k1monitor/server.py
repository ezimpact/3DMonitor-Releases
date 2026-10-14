"""Local Windows dashboard, registry, discovery, controls and camera API."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import math
import re
from pathlib import Path
import secrets
import sys
from typing import Literal

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, SecretStr
import httpx

from . import __version__, __build__
from .camera import negotiate, open_mjpeg
from .remote import COOKIE
from .relay import MAX_FRAME
from .files import Thumbnails,safe_directory
from .activity import endpoint,same_endpoint
from .language import SUPPORTED, translate_markup
from .file_transfers import FileTransfers, gcode_name
from urllib.parse import quote
from starlette.background import BackgroundTask

STATIC = Path(__file__).parent / "static"


class PrinterCredentials(BaseModel):
    mode: Literal['none','apikey','digest']='none'
    api_key: SecretStr=SecretStr('')
    username: str=Field(default='',max_length=1024)
    password: SecretStr=SecretStr('')


class AddPrinter(BaseModel):
    ip: str = Field(max_length=32)
    name: str = Field(min_length=1, max_length=64)
    protocol: Literal['creality','moonraker','octoprint','prusalink']='creality'
    api_port: int | None=Field(default=None,ge=1,le=65535)
    camera_path: str=Field(default='',max_length=1024)
    unload_macro: str=Field(default='UNLOAD_FILAMENT',max_length=64)
    credentials: PrinterCredentials | None=None


class UpdatePrinter(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    ip: str | None = Field(default=None, max_length=32)
    protocol: Literal['creality','moonraker','octoprint','prusalink'] | None=None
    api_port: int | None=Field(default=None,ge=1,le=65535)
    camera_path: str | None=Field(default=None,max_length=1024)
    unload_macro: str | None=Field(default=None,max_length=64)
    credentials: PrinterCredentials | None=None


class ScanRequest(BaseModel):
    network: str = Field(max_length=32)


class PrinterOrderRequest(BaseModel):
    ids: list[str] = Field(max_length=32)

class LanguageRequest(BaseModel):
    language: Literal['ru','en']


class ControlRequest(BaseModel):
    action: str = Field(pattern="^(pause|resume|stop)$")
    request_id: str = Field(pattern="^[a-zA-Z0-9-]{8,64}$")
    expected_session: int = Field(ge=1)
    expected_file: str | None = Field(default=None, max_length=1024)
    expected_job: str | None = Field(default=None,max_length=128)
    expected_ip: str | None = Field(default=None,max_length=32)


class CameraOffer(BaseModel):
    sdp: str = Field(max_length=131072)

class StartPrintRequest(BaseModel):
    file_id: str = Field(pattern='^[a-f0-9]{32}$')
    revision: str = Field(pattern='^[a-f0-9]{64}$')
    request_id: str = Field(pattern='^[a-zA-Z0-9-]{8,64}$')
    expected_session: int = Field(ge=1)
    expected_ip: str = Field(max_length=32)
    calibrate: bool = True

class DeleteFilesRequest(BaseModel):
    file_ids: list[str] = Field(min_length=1,max_length=2000)
    revision: str = Field(pattern='^[a-f0-9]{64}$')
    request_id: str = Field(pattern='^[a-zA-Z0-9-]{8,64}$')
    expected_session: int = Field(ge=1)
    expected_ip: str = Field(max_length=32)

class TransferFileRequest(BaseModel):
    revision: str = Field(pattern='^[a-f0-9]{64}$')
    expected_session: int = Field(ge=1)
    expected_ip: str = Field(max_length=32)
    target_id: str = Field(min_length=1,max_length=64)
    target_session: int = Field(ge=1)
    target_ip: str = Field(max_length=32)
    request_id: str = Field(pattern='^[a-zA-Z0-9-]{8,64}$')

class StockRequest(BaseModel):
    action: str = Field(pattern='^(light|speed|exclude|nozzle|bed)$')
    value: bool | int | str
    request_id: str = Field(pattern='^[a-zA-Z0-9-]{8,64}$')
    expected_session: int = Field(ge=1)
    expected_ip: str = Field(max_length=32)
    expected_file: str | None = Field(default=None,max_length=1024)
    expected_job: str | None = Field(default=None,max_length=128)

class AutoUnloadRequest(BaseModel):
    enabled: bool | None = Field(default=None,strict=True)
    temperature: int | None = Field(default=None,ge=180,le=300,strict=True)


class FilamentRequest(BaseModel):
    expected_endpoint: str = Field(max_length=1200)
    enabled: bool | None = Field(default=None, strict=True)
    capacity_g: float | None = Field(default=None, ge=50, le=10000, strict=True)
    remaining_g: float | None = Field(default=None, ge=0, le=10000, strict=True)
    density: float | None = Field(default=None, ge=.5, le=3, strict=True)
    diameter: float | None = Field(default=None, ge=1, le=4, strict=True)


class PricingValues(BaseModel):
    currency: Literal['RUB','USD','EUR']
    price_per_kg: float | None = Field(default=None,ge=0,le=1e7,strict=True)
    mean_power_w: float = Field(default=0,ge=0,le=100000,strict=True)
    electricity_rate: float = Field(default=0,ge=0,le=1e6,strict=True)
    machine_hour_rate: float = Field(default=0,ge=0,le=1e7,strict=True)
    labor_hour_rate: float = Field(default=0,ge=0,le=1e7,strict=True)
    manual_work_cost: float = Field(default=0,ge=0,le=1e9,strict=True)
    extra_material_cost: float = Field(default=0,ge=0,le=1e9,strict=True)
    failure_percent: float = Field(default=0,ge=0,le=99.99,strict=True)
    extra_cost: float = Field(default=0,ge=0,le=1e9,strict=True)
    markup_percent: float = Field(default=0,ge=0,le=10000,strict=True)

class PricingRequest(PricingValues):
    expected_endpoint: str = Field(max_length=1200)

class CostCalculation(PricingValues):
    price_per_kg: float = Field(ge=0,le=1e7,strict=True)
    filament_g: float = Field(ge=0,le=1e7,strict=True)
    hours: float = Field(ge=0,le=87600,strict=True)
    labor_hours: float = Field(default=0,ge=0,le=87600,strict=True)

class TemperatureWatchRequest(BaseModel):
    expected_endpoint: str = Field(max_length=1200)
    enabled: bool = Field(strict=True)


class RemoteConfig(BaseModel):
    host: str = Field(max_length=253)


class PairRequest(BaseModel):
    code: str = Field(pattern="^[0-9]{6}$")
    name: str = Field(min_length=1, max_length=64)

class RenameDevice(BaseModel):
    name: str = Field(min_length=1,max_length=64)


class UpdatePreferences(BaseModel):
    auto_check: bool=Field(strict=True)
    token: SecretStr | None=None
    forget: bool=Field(default=False,strict=True)


class NotificationPreferences(BaseModel):
    windows: bool = Field(strict=True)
    telegram: bool = Field(strict=True)
    events: list[str] = Field(max_length=20)
    token: SecretStr | None = None

class NotificationTest(BaseModel):
    channel: Literal['windows','telegram']

def create_app(manager, remote=None, relay=None, *, csrf_token=None, activity=None, updates=None):
    token = csrf_token or secrets.token_urlsafe(32)
    thumbnails = Thumbnails()
    transfers = FileTransfers(manager)

    @asynccontextmanager
    async def lifespan(app):
        await manager.start()
        if activity:await activity.start()
        if updates:await updates.start()
        if relay:
            await relay.start()
        try:
            yield
        finally:
            if updates:await updates.stop()
            if activity:await activity.stop()
            if relay:
                await relay.stop()
            await manager.stop()

    app = FastAPI(title="3DMonitor", version=__version__, lifespan=lifespan)
    app.state.csrf_token = token
    app.state.file_transfers = transfers

    def dashboard_snapshot():
        data=manager.snapshot()
        if activity:
            for item in data['printers']:
                item['filament']=activity.filament.public(item['printer']['id'])
                item['temperature_watch']=activity.temperature_watch.public(item['printer']['id'])
                item['costing']=activity.costs.public(item['printer']['id'])
        return data

    def is_remote(connection):
        return remote is not None and bool(remote.host) and connection.url.hostname == remote.host

    def trusted_host(connection):
        # Serve must target our loopback socket. Uvicorn proxy headers are disabled.
        if not is_remote(connection):
            return connection.url.hostname in {"127.0.0.1", "localhost"}
        return (connection.client is not None and connection.client.host in {"127.0.0.1", "::1"}
                and bool(connection.headers.get("tailscale-user-login"))
                and connection.url.port in {None, 443})

    def expected_origin(connection):
        return f"https://{remote.host}" if is_remote(connection) else str(connection.base_url).rstrip("/").replace("ws://", "http://")

    def authorized(connection):
        return remote.authorize(connection.cookies.get(COOKIE, ""), connection.headers.get("tailscale-user-login", ""))

    def worker_allowed(connection, value):
        return (relay is not None and not is_remote(connection)
                and connection.client is not None and connection.client.host in {"127.0.0.1", "::1"}
                and secrets.compare_digest(value, relay.key))

    def socket_allowed(ws):
        origin = ws.headers.get("origin")
        return (trusted_host(ws) and (origin is None or origin == expected_origin(ws))
                and (not is_remote(ws) or authorized(ws)))

    def page_language(request=None):
        selected=request.cookies.get('3dm-language') if request else None
        return selected if selected in SUPPORTED else manager.language_settings.language

    def dashboard_markup(language):
        source=(STATIC/'dashboard.html').read_text(encoding='utf-8')
        if language == 'en':
            content=(STATIC/'support-en.html').read_text(encoding='utf-8')
            source=re.sub(r'<!-- SUPPORT_CONTENT_START -->.*?<!-- SUPPORT_CONTENT_END -->',lambda _:content,source,flags=re.S)
        return translate_markup(source,language).replace('__VERSION__',__version__)

    def page(name, request=None):
        language=page_language(request)
        source=translate_markup((STATIC / name).read_text(encoding="utf-8"),language)
        return HTMLResponse(source.replace("__CSRF_TOKEN__",token).replace('__LANGUAGE__',language))

    def remember_session(response, session_token, session):
        # Lax sends the cookie when opening a link from another site/app. Keep
        # the original expiry: migration must not extend a revoked/expired session.
        response.set_cookie(COOKIE, session_token, max_age=max(0, math.ceil(session["expires"] - remote.clock())),
                            expires=datetime.fromtimestamp(session["expires"], timezone.utc),
                            httponly=True, secure=True, samesite="lax", path="/")

    @app.middleware("http")
    async def local_headers(request: Request, call_next):
        if not trusted_host(request):
            return JSONResponse({"detail": "Подключитесь через Tailscale к адресу этого ПК"}, status_code=403)
        mobile = is_remote(request)
        path = request.url.path
        if mobile and path.endswith('/pricing') and request.method not in {'GET','HEAD'}:
            return JSONResponse({'detail':'Тарифы принтера доступны для изменения на ПК'},status_code=403)
        if mobile and path.endswith(('/filament','/temperature-watch')) and request.method not in {'GET','HEAD'}:
            return JSONResponse({'detail':'Настройки контроля температуры доступны на ПК' if path.endswith('/temperature-watch') else 'Настройки катушки доступны на ПК'},status_code=403)
        internal = path.startswith("/internal/")
        if internal and (mobile or (path != "/internal/camera-relay"
                                    and not worker_allowed(request, request.headers.get("x-k1-worker", "")))):
            return JSONResponse({"detail": "Нет доступа к процессу камеры"}, status_code=403)
        if mobile and (path.startswith("/api/v1/remote") or path.startswith("/api/v1/updates") or path.startswith('/api/v1/notifications') or path == '/api/v1/printer-order' or path == '/api/v1/language' and request.method != 'GET'):
            return JSONResponse({"detail": "Настройки подключения доступны на ПК"}, status_code=403)
        if not internal and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (not secrets.compare_digest(request.headers.get("x-k1-token", ""), token)
                    or (origin is not None and origin != expected_origin(request))):
                return JSONResponse({"detail": "Обновите страницу перед выполнением действия"}, status_code=403)
        if mobile and path not in {"/", "/demo", "/api/v1/auth/pair", "/api/v1/auth/session"} and not path.startswith("/static/") and not authorized(request):
            response = JSONResponse({"detail": "Подключите удалённое устройство по коду на ПК"}, status_code=401)
        else:
            response = await call_next(request)
        if mobile and response.status_code == 200 and path in {"/", "/demo", "/api/v1/auth/session"}:
            session_token = request.cookies.get(COOKIE, "")
            session = remote.session(session_token, request.headers.get("tailscale-user-login", ""))
            if session:
                remember_session(response, session_token, session)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Only the isolated CAD worker needs the Emscripten binding compiler.
        # The dashboard keeps JavaScript eval disabled; workers load same-origin code.
        scripts = "'self' 'unsafe-eval'" if path=='/static/model-preview-worker.js' else "'self'"
        response.headers["Content-Security-Policy"] = f"default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self'; script-src {scripts}; worker-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Принтер не найден"}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Never echo raw request inputs: a malformed form can contain secrets.
        errors=[{key:item[key] for key in ('loc','msg','type')} for item in exc.errors()]
        return JSONResponse({'detail':errors},status_code=422)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(OSError)
    async def io_error(request, exc):
        return JSONResponse({"detail": "Не удалось записать настройки. Проверьте доступ к папке программы."}, status_code=500)

    @app.get("/", include_in_schema=False)
    async def dashboard(request: Request):
        mobile = is_remote(request)
        if mobile and not authorized(request):
            return page("pair.html",request)
        language=page_language(request)
        html = dashboard_markup(language).replace("__CSRF_TOKEN__", token).replace('__LANGUAGE__',language)
        return HTMLResponse(html.replace("__ACCESS_MODE__", "remote" if mobile else "local").replace("__API_BASE__", "/api/v1"))

    @app.get('/api/v1/language')
    async def language_status():
        return {'language':manager.language_settings.language,'supported':list(SUPPORTED)}

    @app.put('/api/v1/language')
    async def language_update(body: LanguageRequest):
        manager.language_settings.save(body.language)
        return {'language':manager.language_settings.language}

    @app.get("/demo", include_in_schema=False)
    async def old_demo_link():
        return RedirectResponse("/", status_code=303)

    def alerts():
        if not activity:raise HTTPException(409,'Уведомления недоступны в этом режиме')
        return activity.notifications

    @app.get('/api/v1/notifications')
    async def notification_status():return alerts().public()

    @app.put('/api/v1/notifications')
    async def notification_config(body:NotificationPreferences):
        return await alerts().configure(body.windows,body.telegram,body.events,body.token.get_secret_value() if body.token else None)

    @app.post('/api/v1/notifications/pair')
    async def notification_pair():return await alerts().begin_pairing()

    @app.delete('/api/v1/notifications/chats/{chat_id}')
    async def notification_remove_chat(chat_id:int):return await alerts().remove_chat(chat_id)

    @app.post('/api/v1/notifications/test')
    async def notification_test(body:NotificationTest):return await alerts().test(body.channel)

    @app.get('/api/v1/printers/{printer_id}/history')
    async def print_history(printer_id:str,offset:int=0,refresh:bool=False):
        if not activity:raise HTTPException(409,'История недоступна в этом режиме')
        if not 0<=offset<=100000:raise HTTPException(422,'Некорректная страница истории')
        if refresh:await activity.refresh(printer_id)
        return activity.history(printer_id,offset)

    @app.get('/api/v1/printers/{printer_id}/history/{record_id}/file')
    async def history_file(printer_id:str,record_id:str):
        if not activity:raise HTTPException(409,'История недоступна в этом режиме')
        monitor=manager.get(printer_id);entry=dict(manager.entry(printer_id))
        record=activity.store.record(printer_id,record_id)
        if not record:raise HTTPException(404,'Запись истории не найдена для этого принтера')
        if not same_endpoint(record['endpoint'],endpoint(entry)):raise HTTPException(409,'Запись относится к прежнему адресу или протоколу принтера. Выберите файл из актуального списка G-code.')
        snapshot=monitor.snapshot()
        if not snapshot['connection']['online'] or snapshot['connection']['stale']:raise HTTPException(409,'Принтер недоступен. Дождитесь подключения.')
        if not snapshot['controls']['start']:raise HTTPException(409,'Принтер занят или не готов к запуску печати.')
        if entry.get('protocol')=='octoprint' and record.get('file_origin')!='local':raise HTTPException(409,'Не удалось подтвердить локальное хранилище этой записи OctoPrint. Выберите файл из актуального списка G-code.')
        catalog=await printer_files(printer_id)
        if manager.get(printer_id) is not monitor or not same_endpoint(endpoint(manager.entry(printer_id)),record['endpoint']):raise HTTPException(409,'Подключение принтера изменилось. Обновите историю.')
        path=record['file'].removeprefix('/usr/data/printer_data/gcodes/')
        file=next((f for f in catalog['files'] if (f.get('relative_path') or f['name'])==path),None)
        if not file:raise HTTPException(404,'G-code из этой записи не найден на принтере: возможно, файл удалён или переименован.')
        if not catalog['can_start']:raise HTTPException(409,'Принтер занят или не готов к запуску печати.')
        return {'catalog':catalog,'file_id':file['id']}

    @app.get("/api/v1/remote/status")
    async def remote_status():
        return remote.status() if remote else {"url": None, "code": None, "devices": []}

    @app.post("/api/v1/remote/config")
    async def remote_config(body: RemoteConfig):
        if not remote:
            raise HTTPException(409, "Доступ с удалённого устройства не включён в этой сборке")
        return remote.configure(body.host)

    @app.post("/api/v1/remote/pair-code")
    async def pair_code():
        if not remote:
            raise HTTPException(409, "Доступ с удалённого устройства не включён в этой сборке")
        return remote.pair_code()

    @app.delete("/api/v1/remote/devices/{device_id}")
    async def revoke_device(device_id: str):
        if not remote:
            raise HTTPException(409, "Доступ с удалённого устройства не включён в этой сборке")
        return remote.revoke(device_id)

    @app.patch('/api/v1/remote/devices/{device_id}')
    async def rename_device(device_id:str,body:RenameDevice):
        if not remote:raise HTTPException(409,'Удалённое подключение недоступно')
        return remote.rename(device_id,body.name)

    @app.post("/api/v1/auth/pair")
    async def pair_phone(body: PairRequest, request: Request):
        if not is_remote(request):
            raise HTTPException(403, "Откройте адрес Tailscale на удалённом устройстве")
        try:
            session = remote.pair(body.code, body.name, request.headers["tailscale-user-login"])
        except ValueError as exc:
            raise HTTPException(401, str(exc)) from exc
        response = JSONResponse({"paired": True})
        remember_session(response, session, remote.session(session, request.headers["tailscale-user-login"]))
        return response

    @app.get("/api/v1/auth/session")
    async def phone_session(request: Request):
        # A Strict cookie can be absent on the initial external navigation but
        # present on this same-origin fetch. No new code/device is created.
        return {"paired": is_remote(request) and authorized(request)}

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request):
        if is_remote(request):
            remote.logout(request.cookies.get(COOKIE, ""))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        return response

    @app.get("/api/v1/status")
    async def status():
        return dashboard_snapshot()

    @app.post('/api/v1/cost/calculate')
    async def print_cost_calculate(body:CostCalculation):
        from .print_costs import calculate
        return calculate(body.model_dump())

    @app.get('/api/v1/printers/{printer_id}/pricing')
    async def print_pricing_status(printer_id:str):
        manager.get(printer_id)
        if not activity:raise HTTPException(409,'Тарифы недоступны в этом режиме')
        return activity.costs.public(printer_id)

    @app.put('/api/v1/printers/{printer_id}/pricing')
    async def print_pricing_config(printer_id:str,body:PricingRequest):
        manager.get(printer_id)
        if not activity:raise HTTPException(409,'Тарифы недоступны в этом режиме')
        if not same_endpoint(body.expected_endpoint,endpoint(manager.entry(printer_id))):
            raise ValueError('Подключение принтера изменилось')
        return activity.costs.configure(printer_id,body.model_dump(exclude={'expected_endpoint'}))

    @app.get('/api/v1/printers/{printer_id}/filament')
    async def filament_status(printer_id:str):
        manager.get(printer_id)
        if not activity:raise HTTPException(409,'Учёт катушек недоступен в этом режиме')
        return activity.filament.public(printer_id)

    @app.put('/api/v1/printers/{printer_id}/filament')
    async def filament_config(printer_id:str,body:FilamentRequest):
        if not activity:raise HTTPException(409,'Учёт катушек недоступен в этом режиме')
        if not same_endpoint(body.expected_endpoint,endpoint(manager.entry(printer_id))):
            raise ValueError('Подключение принтера изменилось')
        changes=body.model_dump(exclude={'expected_endpoint'},exclude_unset=True)
        if any(value is None and key!='remaining_g' for key,value in changes.items()):
            raise ValueError('Некорректная настройка катушки')
        return activity.filament.configure(printer_id,changes)

    @app.get('/api/v1/printers/{printer_id}/temperature-watch')
    async def temperature_watch_status(printer_id:str):
        manager.get(printer_id)
        if not activity:raise HTTPException(409,'Контроль температуры недоступен в этом режиме')
        return activity.temperature_watch.public(printer_id)

    @app.put('/api/v1/printers/{printer_id}/temperature-watch')
    async def temperature_watch_config(printer_id:str,body:TemperatureWatchRequest):
        manager.get(printer_id)
        if not activity:raise HTTPException(409,'Контроль температуры недоступен в этом режиме')
        if not same_endpoint(body.expected_endpoint,endpoint(manager.entry(printer_id))):
            raise ValueError('Подключение принтера изменилось')
        return activity.temperature_watch.configure(printer_id,body.enabled)

    @app.put('/api/v1/printer-order')
    async def printer_order(body: PrinterOrderRequest):
        return {'ids': await manager.set_printer_order(body.ids)}

    @app.post("/api/v1/printers", status_code=201)
    async def add(body: AddPrinter):
        options=body.model_dump(exclude={'ip','name'},exclude_none=True,exclude_unset=True)
        return await manager.add(body.ip, body.name, **options)

    @app.patch("/api/v1/printers/{printer_id}")
    async def update_printer(printer_id: str, body: UpdatePrinter):
        if not body.model_dump(exclude_none=True):
            raise HTTPException(422, "Укажите название или IP принтера")
        return await manager.update(printer_id, **body.model_dump(exclude_none=True))

    @app.delete("/api/v1/printers/{printer_id}")
    async def remove(printer_id: str):
        await manager.remove(printer_id)
        return {"removed": True}

    @app.post("/api/v1/scan", status_code=202)
    async def scan(body: ScanRequest):
        return await manager.scanner.start(body.network)

    @app.delete("/api/v1/scan")
    async def cancel_scan():
        await manager.scanner.cancel()
        return manager.scanner.snapshot()

    @app.post("/api/v1/printers/{printer_id}/control", status_code=202)
    async def control(printer_id: str, body: ControlRequest):
        options={'expected_job':body.expected_job} if body.expected_job is not None else {}
        if body.expected_ip is not None:options['expected_ip']=body.expected_ip
        return await manager.get(printer_id).control(body.action, body.request_id, body.expected_session, body.expected_file,**options)

    @app.get('/api/v1/printers/{printer_id}/files')
    async def printer_files(printer_id: str,directory: str=''):
        if not safe_directory(directory):raise HTTPException(400,'Некорректный путь папки')
        monitor=manager.get(printer_id)
        session=monitor.state.connection_count
        catalog=await monitor.list_files(refresh=True)
        directories=catalog.directory_entries(directory);directory_notice=''
        if hasattr(monitor,'list_directory'):
            try:directories=await monitor.list_directory(directory)
            except httpx.HTTPError:
                directory_notice='API папок не ответил. Показаны папки из списка G-code; пустые папки могут отсутствовать.'
        if manager.get(printer_id) is not monitor or monitor.state.connection_count!=session:raise HTTPException(409,'Подключение принтера изменилось. Обновите список.')
        return {'printer':manager.entry(printer_id),'files':catalog.public(),'revision':catalog.revision,
                'session':session,'can_start':monitor.snapshot()['controls']['start'],'directory':directory,'directories':directories,'directory_notice':directory_notice}

    @app.post('/api/v1/printers/{printer_id}/files/start',status_code=202)
    async def start_print(printer_id: str,body: StartPrintRequest):
        return await manager.get(printer_id).start_print(**body.model_dump())

    @app.post('/api/v1/printers/{printer_id}/files/delete',status_code=202)
    async def delete_files(printer_id: str,body: DeleteFilesRequest):
        return await manager.get(printer_id).delete_files(**body.model_dump())

    @app.get('/api/v1/printers/{printer_id}/files/{file_id}/download')
    async def download_file(printer_id:str,file_id:str,revision:str,session:int,ip:str):
        async with transfers.slots:
            try:file,size,digest,name=await transfers.download(printer_id,file_id,revision,session,ip)
            except (httpx.HTTPError,TimeoutError):raise HTTPException(502,'Не удалось скачать файл с принтера') from None
        async def chunks():
            try:
                while chunk:=file.read(64*1024):yield chunk
            finally:file.close()
        return StreamingResponse(chunks(),media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name,safe=''),'Content-Length':str(size)},background=BackgroundTask(file.close))

    @app.post('/api/v1/printers/{printer_id}/files/upload')
    async def upload_file(printer_id:str,request:Request,filename:str,expected_session:int,expected_ip:str,request_id:str):
        import re
        if not re.fullmatch(r'[a-zA-Z0-9-]{8,64}',request_id):raise HTTPException(422,'Некорректный идентификатор отправки')
        gcode_name(filename);transfers.selected(printer_id,expected_session,expected_ip)
        async with transfers.slots:
            file,size,digest=await transfers.receive(request)
            try:return await transfers.upload(printer_id,filename,file,size,digest,expected_session,expected_ip,request_id)
            finally:file.close()

    @app.post('/api/v1/printers/{printer_id}/files/{file_id}/transfer')
    async def transfer_file(printer_id:str,file_id:str,body:TransferFileRequest):
        if printer_id==body.target_id:raise HTTPException(409,'Выберите другой принтер')
        target=transfers.selected(body.target_id,body.target_session,body.target_ip)
        async with transfers.slots:
            try:file,size,digest,name=await transfers.download(printer_id,file_id,body.revision,body.expected_session,body.expected_ip)
            except (httpx.HTTPError,TimeoutError):raise HTTPException(502,'Не удалось скачать исходный файл') from None
            try:
                transfers.selected(body.target_id,body.target_session,body.target_ip,target)
                source=manager.get(printer_id)
                def check_source():
                    transfers.selected(printer_id,body.expected_session,body.expected_ip,source)
                    if source.files.revision!=body.revision:raise ValueError('Исходный список файлов изменился')
                return await transfers.upload(body.target_id,name,file,size,digest,body.target_session,body.target_ip,body.request_id,check_source)
            finally:file.close()

    @app.post('/api/v1/printers/{printer_id}/settings-command',status_code=202)
    async def stock_command(printer_id: str,body: StockRequest):
        return await manager.get(printer_id).stock_command(**body.model_dump())

    @app.put('/api/v1/printers/{printer_id}/auto-unload')
    async def auto_unload(printer_id: str,body: AutoUnloadRequest):
        if body.enabled is None and body.temperature is None:raise HTTPException(422,'Укажите настройку автовыгрузки')
        return await manager.set_auto_unload(printer_id,body.enabled,body.temperature)

    @app.get('/api/v1/printers/{printer_id}/objects')
    async def objects(printer_id: str):
        monitor=manager.get(printer_id)
        snapshot=await monitor.list_objects()
        if manager.get(printer_id) is not monitor:raise HTTPException(409,'Адрес принтера изменился')
        return snapshot

    @app.get('/api/v1/printers/{printer_id}/files/{file_id}/thumbnail')
    async def file_thumbnail(printer_id: str,file_id: str,session: int,revision: str):
        monitor=manager.get(printer_id)
        def check():
            if manager.get(printer_id) is not monitor or monitor.state.connection_count!=session or monitor.files.revision!=revision:
                raise HTTPException(409,'Список или подключение изменились')
        check()
        entry=monitor.files.get(file_id)
        if not entry or not entry['thumbnail'] and not hasattr(monitor,'thumbnail'):raise HTTPException(404,'Миниатюра отсутствует')
        try:content,mime=await monitor.thumbnail(entry['path']) if hasattr(monitor,'thumbnail') else await thumbnails.get(monitor.state.host,entry['thumbnail'])
        except (httpx.HTTPError,ValueError,TimeoutError):raise HTTPException(404,'Миниатюра недоступна') from None
        check()
        return Response(content,media_type=mime)

    @app.get('/api/v1/printers/{printer_id}/thumbnail')
    async def job_thumbnail(printer_id: str,file: str,session: int):
        monitor=manager.get(printer_id)
        def same_job():
            current=monitor.snapshot()
            return manager.get(printer_id) is monitor and current['print']['file']==file and current['connection']['sessions']==session and current['connection']['online'] and not current['connection']['stale']
        if not same_job():raise HTTPException(409,'Текущее задание изменилось или нет свежих данных')
        match=next((item for item in monitor.files.files if item['path']==file or item['name']==file),None)
        path=match['thumbnail'] if match and match['thumbnail'] else '/downloads/original/current_print_image.png'
        try:content,mime=await monitor.thumbnail(file) if hasattr(monitor,'thumbnail') else await thumbnails.get(monitor.state.host,path)
        except (httpx.HTTPError,ValueError,TimeoutError):raise HTTPException(404,'Миниатюра задания недоступна') from None
        if not same_job():raise HTTPException(409,'Текущее задание изменилось')
        return Response(content,media_type=mime)

    @app.post("/api/v1/printers/{printer_id}/camera/offer")
    async def camera_offer(printer_id: str, body: CameraOffer, request: Request):
        if is_remote(request):
            raise HTTPException(409, "Для удалённого устройства используйте поток камеры через ПК")
        ip = manager.entry(printer_id)["ip"]
        if manager.entry(printer_id).get('protocol','creality')!='creality':raise HTTPException(409,'Используйте настроенный поток MJPEG камеры')
        try:
            return await negotiate(ip, body.sdp)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise HTTPException(502, "Камера WebRTC недоступна. Можно попробовать MJPEG.") from exc

    @app.get("/api/v1/printers/{printer_id}/camera/mjpeg")
    async def mjpeg(printer_id: str, request: Request):
        if is_remote(request):
            raise HTTPException(409, "Для удалённого устройства используйте поток камеры через ПК")
        ip = manager.entry(printer_id)["ip"]
        try:
            monitor=manager.get(printer_id)
            url=monitor.snapshot()['camera'].get('path') if manager.entry(printer_id).get('protocol','creality')!='creality' else None
            if manager.entry(printer_id).get('protocol','creality')!='creality' and not url:raise ValueError('Камера не настроена')
            client, response, content_type = await open_mjpeg(ip,url)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Камера MJPEG недоступна") from exc

        async def chunks():
            try:
                async for chunk in response.aiter_bytes(chunk_size=32768):
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        return StreamingResponse(chunks(), media_type=content_type)

    def update_service():
        if updates is None:raise HTTPException(409,'Обновления недоступны в этом режиме')
        return updates

    @app.get('/api/v1/updates')
    async def update_status():return update_service().status()

    @app.put('/api/v1/updates')
    async def update_preferences(body: UpdatePreferences):
        return await update_service().configure(body.auto_check,body.token.get_secret_value() if body.token is not None else None,body.forget)

    @app.post('/api/v1/updates/check')
    async def update_check():return await update_service().check()

    @app.post('/api/v1/updates/download')
    async def update_download():return update_service().begin_download()

    @app.post('/api/v1/updates/install')
    async def update_install():
        updater=update_service()
        if any(item.get('filament_runout') and item.get('auto_unload',{}).get('enabled') is not False or item.get('command',{} ) and item['command'].get('phase')=='pending' for item in manager.snapshot()['printers']):
            raise HTTPException(409,'Дождитесь завершения текущего действия принтера перед обновлением')
        if transfers.slots._value<2 or any(lock.locked() for lock in transfers.locks.values()):
            raise HTTPException(409,'Дождитесь завершения передачи файлов перед обновлением')
        updater.launch_install()
        return updater.status()

    @app.get("/health")
    async def health():
        printers = manager.snapshot()["printers"]
        return {"app": "K1Monitor", "display_name": "3DMonitor", "server": "running", "version": __version__, "build": __build__, "printers": len(printers),
                "printers_online": sum(item["connection"]["online"] for item in printers)}

    @app.get("/internal/camera-relay", include_in_schema=False)
    async def worker_page():
        return page("camera-worker.html")

    @app.post("/internal/camera-relay/{printer_id}/offer", include_in_schema=False)
    async def worker_offer(printer_id: str, body: CameraOffer, request: Request):
        generation = request.headers.get("x-k1-generation", "")
        source = relay.source(printer_id, generation)
        answer = await negotiate(source.ip, body.sdp)
        relay.source(printer_id, generation)
        return answer

    @app.post("/internal/camera-relay/{printer_id}/frame", include_in_schema=False)
    async def worker_frame(printer_id: str, request: Request):
        relay.source(printer_id, request.headers.get("x-k1-generation", ""))
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_FRAME:
                raise HTTPException(413, "Слишком большой кадр камеры")
            chunks.append(chunk)
        relay.frame(printer_id, request.headers.get("x-k1-generation", ""), b"".join(chunks))
        return {"received": True}

    @app.websocket("/internal/camera-relay/worker")
    async def worker_socket(ws: WebSocket):
        protocol = "k1-worker." + (relay.key if relay else "")
        if (not trusted_host(ws) or not worker_allowed(ws, ws.headers.get("sec-websocket-protocol", "").removeprefix("k1-worker."))
                or relay.worker_connected):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol=protocol)
        relay.worker_connected = True
        relay.worker_last_seen = relay.clock()

        async def configurations():
            while True:
                relay.changed.clear()
                await ws.send_json(relay.configuration())
                await relay.changed.wait()

        sender = asyncio.create_task(configurations())
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > 2048:
                    await ws.close(code=1009)
                    break
                import json
                message = json.loads(raw)
                relay.worker_last_seen = relay.clock()
                if message.get("type") == "error":
                    printer_id = message.get("id", "")
                    try: relay.source(printer_id, message.get("generation", ""))
                    except KeyError: continue
                    relay.fail(printer_id, str(message.get("message", "Камера недоступна")))
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            sender.cancel()
            try: await sender
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError): pass
            relay.worker_connected = False
            for printer_id in relay.sources:
                relay.fail(printer_id, "Процесс передачи камеры отключился")

    @app.websocket("/api/v1/printers/{printer_id}/camera/relay")
    async def camera_stream(ws: WebSocket, printer_id: str):
        if not socket_allowed(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = None
        try:
            if not relay:
                raise ValueError("Передача камеры через ПК недоступна в этой сборке")
            manager.get(printer_id)
            queue = await relay.subscribe(printer_id)

            async def disconnected():
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return

            reader = asyncio.create_task(disconnected())
            try:
                while not reader.done():
                    if is_remote(ws) and not authorized(ws):
                        await ws.close(code=1008)
                        return
                    try: item = await asyncio.wait_for(queue.get(), timeout=1)
                    except TimeoutError: continue
                    if isinstance(item, bytes):
                        await ws.send_bytes(item)
                    else:
                        await ws.send_json(item)
                        await ws.close(code=1011)
                        return
            finally:
                reader.cancel()
                try: await reader
                except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError): pass
        except (KeyError, ValueError, OSError) as exc:
            await ws.send_json({"type": "error", "message": "Принтер не найден" if isinstance(exc, KeyError) else str(exc)})
            await ws.close(code=1011)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if queue:
                relay.unsubscribe(printer_id, queue)

    @app.websocket("/api/v1/events")
    async def events(ws: WebSocket):
        if not socket_allowed(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while True:
                if is_remote(ws) and not authorized(ws):
                    await ws.close(code=1008)
                    return
                await ws.send_json(dashboard_snapshot())
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=1)
                except TimeoutError:
                    continue
        except WebSocketDisconnect:
            pass

    model_vendor=Path(sys.executable).parent/'ModelPreview' if getattr(sys,'frozen',False) else STATIC/'vendor/model-preview'
    app.mount('/static/vendor/model-preview',StaticFiles(directory=model_vendor,check_dir=False),name='model-preview-vendor')
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
