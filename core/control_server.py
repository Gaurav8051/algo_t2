"""
TCP control server — lets local dashboard connect to cloud main.py without
stopping the algo.  JSON line protocol (one request → one response per line).
"""
from __future__ import annotations
import json
import logging
import socket
import threading
from typing import Any, Callable

import config

log = logging.getLogger("algo.control")


class ControlServer:
    def __init__(self,
                 get_snapshot: Callable[[], dict],
                 run_command: Callable[[dict], dict],
                 port: int | None = None,
                 bind: str | None = None):
        self._get_snapshot = get_snapshot
        self._run_command  = run_command
        self._port         = port or config.CONTROL_PORT
        self._bind         = bind or config.CONTROL_BIND
        self._stop         = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._serve, daemon=True, name="ControlServer")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self._bind, self._port))
            srv.listen(8)
            srv.settimeout(1.0)
            log.info(f"Control server listening on {self._bind}:{self._port}")
        except OSError as e:
            log.error(f"Control server bind failed: {e}")
            return
        while not self._stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()
        srv.close()

    def _handle(self, conn: socket.socket, addr):
        with conn:
            conn.settimeout(30.0)
            try:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    data += chunk
                line = data.split(b"\n", 1)[0]
                req = json.loads(line.decode("utf-8"))
                resp = self._dispatch(req)
            except Exception as e:
                resp = {"ok": False, "error": str(e)}
            try:
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except OSError:
                pass

    def _dispatch(self, req: dict) -> dict:
        token = req.get("token", "")
        if config.CONTROL_TOKEN and token != config.CONTROL_TOKEN:
            return {"ok": False, "error": "invalid token"}

        action = req.get("action", "")
        if action == "ping":
            return {"ok": True, "message": "pong"}
        if action == "get_snapshot":
            return {"ok": True, "data": self._get_snapshot()}
        if action == "command":
            cmd = req.get("cmd", {})
            result = self._run_command(cmd)
            return {"ok": True, **result}
        return {"ok": False, "error": f"unknown action: {action}"}
