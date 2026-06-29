"""TCP client for remote dashboard → cloud algo control."""
from __future__ import annotations
import json
import socket
from typing import Any

import config


class ControlClient:
    def __init__(self, host: str, port: int | None = None, token: str = ""):
        self.host  = host
        self.port  = port or config.CONTROL_PORT
        self.token = token or config.CONTROL_TOKEN
        self.last_error: str = ""

    def _request(self, payload: dict, timeout: float = 10.0) -> dict:
        self.last_error = ""
        payload.setdefault("token", self.token)
        raw = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout) as s:
                s.sendall(raw)
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as e:
            self.last_error = f"{type(e).__name__}: {e}"
            raise
        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8"))

    def ping(self) -> bool:
        try:
            r = self._request({"action": "ping"}, timeout=3.0)
            return r.get("ok", False)
        except OSError:
            return False

    def get_snapshot(self) -> dict | None:
        try:
            r = self._request({"action": "get_snapshot"})
            if r.get("ok"):
                return r.get("data")
            self.last_error = r.get("error", "get_snapshot failed")
        except OSError:
            pass
        return None

    def send_command(self, cmd: dict) -> tuple[bool, str]:
        try:
            r = self._request({"action": "command", "cmd": cmd})
            if r.get("ok"):
                return True, r.get("message", "ok")
            return False, r.get("error", "failed")
        except OSError as e:
            return False, str(e)
