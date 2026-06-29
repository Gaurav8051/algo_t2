"""Append-only order/trade log shared by paper and live modes."""
from __future__ import annotations
import json
import logging
import os
import time

import config

log = logging.getLogger("algo.order_log")
_MAX = 500


def append(record: dict):
    record.setdefault("ts", time.time())
    os.makedirs(os.path.dirname(config.ORDER_LOG_FILE) or "data", exist_ok=True)
    rows: list[dict] = []
    if os.path.exists(config.ORDER_LOG_FILE):
        try:
            with open(config.ORDER_LOG_FILE, encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            rows = []
    rows.append(record)
    rows = rows[-_MAX:]
    try:
        with open(config.ORDER_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
    except Exception as e:
        log.error(f"order_log save: {e}")


def recent(n: int = 50) -> list[dict]:
    if not os.path.exists(config.ORDER_LOG_FILE):
        return []
    try:
        with open(config.ORDER_LOG_FILE, encoding="utf-8") as f:
            rows = json.load(f)
        return rows[-n:]
    except Exception:
        return []
