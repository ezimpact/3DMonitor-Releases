"""Pairing and persistent browser sessions behind a localhost Tailscale Serve proxy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time

COOKIE = "__Host-k1-session"
SESSION_SECONDS = 30 * 24 * 60 * 60
PAIR_SECONDS = 300


def remote_host(value):
    value = value.strip().lower().rstrip(".")
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if not re.fullmatch(label + r"\." + label + r"\.ts\.net", value):
        raise ValueError("Укажите DNS-имя этого ПК в Tailscale, например pc.tail12345.ts.net")
    return value


class RemoteAccess:
    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = path, clock
        self.host = ""
        self.devices = []
        self.code = ""
        self.code_expires = 0
        self.attempts = 0
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            self.host = remote_host(raw["host"]) if raw.get("host") else ""
            for item in raw.get("devices", []):
                if (re.fullmatch(r"[0-9a-f]{64}", str(item.get("hash", "")))
                        and isinstance(item.get("expires"), (int, float))
                        and isinstance(item.get("identity"), str)
                        and isinstance(item.get("name"), str)
                        and re.fullmatch(r"[0-9a-f]{16}", str(item.get("id", "")))):
                    self.devices.append(item)

    def save(self, host, devices):
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump({"schema_version": 1, "host": host, "devices": devices}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
        self.host, self.devices = host, devices

    def configure(self, host):
        host = remote_host(host)
        # A new tailnet/hostname requires pairing again.
        self.save(host, self.devices if self.host == host else [])
        self.code = ""
        return self.status()

    def status(self):
        now = self.clock()
        return {"url": f"https://{self.host}/" if self.host else None,
                "code": self.code if now < self.code_expires else None,
                "code_expires": self.code_expires if self.code and now < self.code_expires else None,
                "devices": [{key: item[key] for key in ("id", "name", "expires")}
                            for item in self.devices if item["expires"] > now]}

    def pair_code(self):
        if not self.host:
            raise ValueError("Сначала запустите Enable-Remote.cmd из папки программы")
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        self.code_expires = self.clock() + PAIR_SECONDS
        self.attempts = 0
        return self.status()

    def pair(self, code, name, identity):
        now = self.clock()
        if not self.code or now >= self.code_expires:
            raise ValueError("Код недействителен. Создайте новый код на ПК.")
        self.attempts += 1
        correct = secrets.compare_digest(code, self.code)
        if not correct:
            if self.attempts >= 5:
                self.code = ""
            raise ValueError("Код неверный или истёк. Проверьте код на ПК.")
        name = name.strip()
        if not name or len(name) > 64 or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ValueError("Укажите название устройства от 1 до 64 символов")
        devices = [item for item in self.devices if item["expires"] > now]
        if len(devices) >= 16:
            raise ValueError("Удалите старое подключение на ПК: максимум 16 устройств")
        token = secrets.token_urlsafe(32)
        devices.append({"id": secrets.token_hex(8), "hash": hashlib.sha256(token.encode()).hexdigest(),
                        "identity": identity, "name": name, "expires": now + SESSION_SECONDS})
        self.save(self.host, devices)
        self.code = ""
        return token

    def session(self, token, identity):
        if not token or len(token) > 128 or not identity:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        return next((item for item in self.devices if item["expires"] > self.clock() and item["identity"] == identity
                     and secrets.compare_digest(item["hash"], digest)), None)

    def authorize(self, token, identity):
        return self.session(token, identity) is not None

    def revoke(self, device_id):
        self.save(self.host, [item for item in self.devices if item["id"] != device_id])
        return self.status()

    def rename(self,device_id,name):
        if any(ord(c)<32 or ord(c)==127 for c in name):raise ValueError('Название устройства не должно содержать управляющие символы')
        name=name.strip()
        if not 1<=len(name)<=64 or any(ord(c)<32 or ord(c)==127 for c in name):raise ValueError('Название устройства: 1–64 символа без управляющих символов')
        if not any(item['id']==device_id and item['expires']>self.clock() for item in self.devices):raise ValueError('Привязанное устройство не найдено')
        self.save(self.host,[{**item,'name':name} if item['id']==device_id else item for item in self.devices])
        return self.status()

    def logout(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.save(self.host, [item for item in self.devices if not secrets.compare_digest(item["hash"], digest)])
