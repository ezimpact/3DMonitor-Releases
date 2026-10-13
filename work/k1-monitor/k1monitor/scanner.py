"""Bounded LAN discovery: recognize printers from telemetry, not an open port."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import time
import uuid
import httpx

from websockets.asyncio.client import connect

from .client import STATUS_REQUEST
from .config import scan_network
from .telemetry import Telemetry


async def probe_creality(ip: str, *, port: int = 9999, timeout: float = 2.5):
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=0.4)
    except (OSError, TimeoutError):
        return None
    finally:
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except (OSError, TimeoutError):
                pass
    try:
        async with asyncio.timeout(timeout):
            async with connect(f"ws://{ip}:{port}/", subprotocols=["wsslicer"], compression=None,
                               ping_interval=None, proxy=None, close_timeout=0.2, max_size=262144) as ws:
                await ws.send(STATUS_REQUEST)
                state = Telemetry(ip, "unknown")
                while True:
                    raw = await ws.recv()
                    try:
                        data = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    if data.get("ModeCode") == "heart_beat":
                        await ws.send("ok")
                        continue
                    state.ingest(data)
                    model = state.data.get("model")
                    if isinstance(model, str) and model.upper().replace(" ", "").startswith(("K1", "K2")):
                        info = state.snapshot()["printer"]
                        return {"ip": ip, "model": model[:64], "firmware": info["firmware"], 'protocol':'creality','api_port':port}
    except Exception:
        return None


async def probe_printer(ip: str, *, port: int = 9999, timeout: float = 2.5):
    result=await probe_creality(ip,port=port,timeout=timeout)
    if result:return result
    async with httpx.AsyncClient(trust_env=False,timeout=httpx.Timeout(timeout,connect=.4),follow_redirects=False) as client:
        for api_port in (7125,80):
            try:
                reply=await client.get(f'http://{ip}:{api_port}/server/info');reply.raise_for_status()
                if len(reply.content)>262144:continue
                info=reply.json()['result']
                if not isinstance(info.get('moonraker_version'),str):continue
                reply=await client.get(f'http://{ip}:{api_port}/printer/info');reply.raise_for_status()
                printer=reply.json()['result']
                return {'ip':ip,'model':'Klipper · '+str(printer.get('hostname','Moonraker'))[:48],
                        'firmware':str(printer.get('software_version','unknown'))[:128],'protocol':'moonraker','api_port':api_port}
            except (httpx.HTTPError,ValueError,KeyError,TypeError):continue
        for api_port in (80,5000):
            try:
                reply=await client.get(f'http://{ip}:{api_port}/api/version');reply.raise_for_status()
                if len(reply.content)>262144:continue
                info=reply.json();label=str(info.get('text',''))
                protocol='octoprint' if label.startswith('OctoPrint') else 'prusalink' if 'PrusaLink' in label else None
                if protocol:return {'ip':ip,'model':label[:64],'firmware':str(info.get('firmware',info.get('server','unknown')))[:128],'protocol':protocol,'api_port':api_port}
            except (httpx.HTTPError,ValueError,KeyError,TypeError):continue
    return None


class NetworkScanner:
    def __init__(self, config):
        self.config = config
        self.task = None
        self.job = None

    def snapshot(self):
        if not self.job:
            return None
        registered = {item["ip"] for item in self.config.printers}
        return {**self.job, "results": [{**item, "added": item["ip"] in registered} for item in self.job["results"]]}

    async def start(self, cidr: str):
        network = scan_network(cidr)
        if self.task and not self.task.done():
            raise ValueError("Поиск уже выполняется")
        hosts = [str(ip) for ip in network.hosts()]
        self.job = {"id": uuid.uuid4().hex, "network": str(network), "state": "running", "checked": 0,
                    "total": len(hosts), "results": [], "elapsed_seconds": 0}
        self.task = asyncio.create_task(self._run(hosts), name="lan-discovery")
        return self.snapshot()

    async def _run(self, hosts):
        start = time.monotonic()
        limit = asyncio.Semaphore(24)

        async def check(ip):
            async with limit:
                result = await probe_printer(ip)
                if result:
                    self.job["results"].append(result)
                    self.job["results"].sort(key=lambda item: ipaddress.IPv4Address(item["ip"]))
                self.job["checked"] += 1
                self.job["elapsed_seconds"] = round(time.monotonic() - start, 1)
        try:
            await asyncio.gather(*(check(ip) for ip in hosts))
            self.job["state"] = "completed"
        except asyncio.CancelledError:
            self.job["state"] = "cancelled"
            raise
        except Exception:
            self.job["state"] = "failed"
            self.job["error"] = "Поиск прерван из-за сетевой ошибки. Проверьте подключение и повторите."

    async def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def suggested_networks(self):
        networks = {str(ipaddress.IPv4Network(item["ip"] + "/24", strict=False)) for item in self.config.printers}
        try:
            import ifaddr
            from .config import local_ip
            for adapter in ifaddr.get_adapters():
                for addr in adapter.ips:
                    if not isinstance(addr.ip, str):
                        continue
                    try:
                        local_ip(addr.ip)
                        # Large VPN/virtual subnets are never selected for sweeping.
                        prefix = max(22, addr.network_prefix)
                        networks.add(str(scan_network(f"{addr.ip}/{prefix}")))
                    except ValueError:
                        pass
        except OSError:
            pass
        configured = [str(ipaddress.IPv4Network(item["ip"] + "/24", strict=False)) for item in self.config.printers]
        return list(dict.fromkeys(configured + sorted(networks))) or ["192.168.4.0/24"]
