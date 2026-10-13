"""Entry point for Python and the packaged Windows executable."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
import time
from urllib.request import ProxyHandler, build_opener
import webbrowser

import uvicorn

from k1monitor.config import ConfigStore
from k1monitor.updates import Updates
from k1monitor.manager import PrinterManager
from k1monitor.server import create_app
from k1monitor.remote import RemoteAccess
from k1monitor.browser_worker import BrowserWorker
from k1monitor.relay import CameraRelay
from k1monitor import __version__
from k1monitor.service_control import ServiceControl
from k1monitor.tray import WindowsTray, InstanceLease
from k1monitor.language import LanguageSettings


def app_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def report_error(message):
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    else:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "3DMonitor", 0x10)


def already_running(url):
    try:
        with build_opener(ProxyHandler({})).open(url + "health", timeout=0.5) as response:
            return json.load(response).get("app") == "K1Monitor"
    except (OSError, ValueError):
        return False


def open_when_ready(url):
    opener = build_opener(ProxyHandler({}))
    for _ in range(60):
        try:
            with opener.open(url + "health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.5)


async def diagnose(monitor, seconds, log_dir):
    await monitor.start()
    try:
        await asyncio.sleep(seconds)
        snapshot = monitor.snapshot()
        report = log_dir / "diagnostic.json"
        report.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        if sys.stdout is not None:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            print(f"Report: {report}")
        return 0 if any(item["connection"]["online"] for item in snapshot["printers"]) else 2
    finally:
        await monitor.stop()


def run_service(factory,control):
    """Every cycle fully closes its lifespan before creating the next server."""
    while True:
        server=factory()
        control.attach(server)
        server.run()
        if not control.next_cycle():return


def serve(config_path,args,url,initial_config,initial_remote):
    control=ServiceControl();previous=None;csrf_token=None;first=True
    language=LanguageSettings(config_path.resolve().parent/'data/language.json')
    def factory():
        nonlocal previous,csrf_token,first
        config=initial_config if first else ConfigStore(config_path,portable=args.config is None)
        remote=initial_remote if first else RemoteAccess(config_path.resolve().parent/'remote.json')
        first=False
        monitor=PrinterManager(config)
        monitor.language_settings=language
        if previous:
            for key,item in monitor.monitors.items():
                if key in previous.monitors:item.state.connection_count=previous.monitors[key].state.connection_count
        previous=monitor
        relay=CameraRelay(monitor,BrowserWorker(config_path.resolve().parent/'cache'/f'camera-worker-{args.port}'),url)
        from k1monitor.activity import ActivityStore,ActivityService
        from k1monitor.notifications import Notifications
        store=ActivityStore(config_path.resolve().parent/'data/print-history.sqlite3')
        notifications=Notifications(config_path.resolve().parent/'notifications.json',store,tray.notify if tray.registered else None,translate=language.translate)
        activity=ActivityService(monitor,notifications,relay)
        updates=Updates(config_path.resolve().parent,control=control,port=args.port,install_supported=bool(getattr(sys,"frozen",False)) and args.config is None)
        app=create_app(monitor,remote,relay,csrf_token=csrf_token,activity=activity,updates=updates)
        # Existing browser tabs remain usable across a local service restart;
        # incremented printer sessions still reject old command selections.
        csrf_token=app.state.csrf_token
        return uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,log_config=None,
            proxy_headers=False,access_log=False,ws='websockets-sansio',timeout_graceful_shutdown=10))
    tray=WindowsTray(control,lambda:webbrowser.open(url),title=f'3DMonitor {__version__}',translate=language.translate)
    try:
        if not args.no_tray:
            try:
                tray.start()
                if sys.platform=='win32':logging.info('Windows tray: %s',tray.inspect())
            except RuntimeError:logging.exception('Cannot start Windows tray')
        if not args.no_browser:threading.Thread(target=open_when_ready,args=(url,),daemon=True).start()
        run_service(factory,control)
    finally:tray.stop()


def main():
    parser = argparse.ArgumentParser(description="3DMonitor: Windows printer manager")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true",help="Disable the Windows notification icon")
    parser.add_argument("--diagnose", type=int, metavar="SECONDS")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        config_path = args.config or app_directory() / "config.json"
        config = ConfigStore(config_path, portable=args.config is None)
        remote = RemoteAccess(config_path.resolve().parent / "remote.json")
        if not 1024 <= args.port <= 65535:
            raise ValueError("Port must be between 1024 and 65535")
        if args.diagnose is not None and not 1 <= args.diagnose <= 120:
            raise ValueError("Diagnostic duration must be between 1 and 120 seconds")
    except (OSError, KeyError, ValueError) as exc:
        report_error(f"Ошибка конфигурации: {exc}")
        return 1
    url = f"http://127.0.0.1:{args.port}/"
    if args.diagnose is None and already_running(url):
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    lease=None
    if args.diagnose is None:
        try:
            lease=InstanceLease(args.port)
            if not lease.owned:
                lease.close()
                if not args.no_browser:open_when_ready(url)
                return 0
        except OSError as exc:
            report_error(f'Не удалось проверить запуск 3DMonitor: {exc}')
            return 1
    log_dir = config_path.resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(log_dir / "monitor.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.diagnose is not None:
        return asyncio.run(diagnose(PrinterManager(config), args.diagnose, log_dir))
    if sys.stdout is not None:
        print(f"3DMonitor {__version__}: {url}\nPrinters: {len(config.printers)}\nPress Ctrl+C to stop.", flush=True)
    try:
        serve(config_path,args,url,config,remote)
    except SystemExit as exc:
        if exc.code:
            report_error(f"Не удалось запустить панель на порту {args.port}. Закройте предыдущую версию программы или используйте --port с другим номером.")
        return int(exc.code or 0)
    except (OSError,KeyError,ValueError) as exc:
        report_error(f'Не удалось перезапустить 3DMonitor: {exc}')
        return 1
    finally:
        if lease:lease.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
