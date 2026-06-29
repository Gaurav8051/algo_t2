"""
Single-instance lock — enforces one trading mode/process at a time.

Rules:
  - Only one main.py instance may run (paper OR live).
  - Cannot start PAPER while LIVE is running with open positions.
  - Cannot start LIVE while PAPER is running (must Q-paper first).
  - Switching paper → live: Step 1 Q-paper, Step 2 start live.
  - Switching live → paper: STRICTLY PROHIBITED until all live positions closed.
"""
from __future__ import annotations
import json
import logging
import os
import sys
import time

import config

log = logging.getLogger("algo.lock")


def _read() -> dict | None:
    if not os.path.exists(config.LOCK_FILE):
        return None
    try:
        with open(config.LOCK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire(mode: str, has_positions: bool = False) -> None:
    os.makedirs(os.path.dirname(config.LOCK_FILE) or "data", exist_ok=True)
    old = _read()
    if old and _pid_alive(old.get("pid", 0)):
        old_mode = old.get("mode", "?")
        if old_mode != mode:
            if old_mode == "live" and old.get("has_positions"):
                log.error(
                    "LIVE trading is active with open positions.\n"
                    "  Switching to PAPER is PROHIBITED until all live positions are closed.\n"
                    "  From dashboard: sell all → confirm positions flat → stop live service."
                )
                sys.exit(1)
            if old_mode == "paper":
                log.error(
                    "PAPER trading is still running (pid=%s).\n"
                    "  Step 1: Open dashboard → type 'q-paper' → confirm yes\n"
                    "  Step 2: Wait for paper session to stop\n"
                    "  Step 3: Start live: python main.py --mode live",
                    old.get("pid"),
                )
                sys.exit(1)
            log.error(f"Another algo instance is running ({old_mode}, pid={old.get('pid')}).")
            sys.exit(1)
    data = {
        "mode": mode,
        "pid": os.getpid(),
        "has_positions": has_positions,
        "started": time.time(),
    }
    with open(config.LOCK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log.info(f"Process lock acquired: mode={mode} pid={os.getpid()}")


def update(has_positions: bool | None = None, mode: str | None = None):
    data = _read() or {}
    if data.get("pid") != os.getpid():
        return
    if has_positions is not None:
        data["has_positions"] = has_positions
    if mode is not None:
        data["mode"] = mode
    data["updated"] = time.time()
    try:
        with open(config.LOCK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.debug(f"lock update: {e}")


def release():
    data = _read()
    if data and data.get("pid") == os.getpid():
        try:
            os.remove(config.LOCK_FILE)
            log.info("Process lock released")
        except OSError:
            pass
