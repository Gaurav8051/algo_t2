"""
core/session.py — Session persistence for delivery/positional trading.

When PRODUCT_TYPE = "DELIVERY", positions can remain open overnight.
On next day startup, this module:
  1. Reads saved positions from data/session.json
  2. Cross-checks with live Dhan positions to confirm they still exist
  3. Restores AlgoState without asking for first-trade input
  4. Also handles resuming S/R levels from the previous session
"""

from __future__ import annotations
import json
import logging
import os
from datetime import date

import config
from core.states import AlgoState, FSMState, Position, SRLevel

log = logging.getLogger("algo.session")


def save_session(state: AlgoState, mode: str):
    """Save current positions and S/R to disk after every candle close."""
    data = {
        "date":        str(date.today()),
        "mode":        mode,
        "index":       state.index_key,
        "daily_pnl":   state.daily_pnl,
        "sr_levels":   [[sr.level, sr.tolerance] for sr in state.sr_levels],
        "expiry":      config.OPTION_EXPIRY,
        "global_tolerance": config.TOLERANCE,
        "num_lots":    config.NUM_LOTS,
        "call_pos":    state.call_pos.to_dict() if state.call_pos else None,
        "put_pos":     state.put_pos.to_dict()  if state.put_pos  else None,
        "fsm":         state.fsm.name,
        "pending_put_reentry": (
            [state.pending_put_reentry.level, state.pending_put_reentry.tolerance]
            if state.pending_put_reentry else None
        ),
        "pending_call_reentry": (
            [state.pending_call_reentry.level, state.pending_call_reentry.tolerance]
            if state.pending_call_reentry else None
        ),
        "call_disabled": state.call_disabled,
        "put_disabled":  state.put_disabled,
    }
    try:
        with open(config.SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.error(f"Session save error: {e}")


def load_session(mode: str) -> dict | None:
    """
    Load session file. Returns dict if valid session from same mode/index exists,
    else None (start fresh).
    """
    if not os.path.exists(config.SESSION_FILE):
        return None
    try:
        with open(config.SESSION_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.warning(f"Session load error: {e}")
        return None

    # Only restore if same mode and same index (clear stale file on mismatch)
    if data.get("mode") != mode:
        log.info(f"Session mode mismatch ({data.get('mode')} vs {mode}) — clearing stale session")
        clear_session()
        return None
    if data.get("index") != config.ACTIVE_INDEX:
        log.info(f"Session index mismatch ({data.get('index')} vs "
                 f"{config.ACTIVE_INDEX}) — clearing stale session")
        clear_session()
        return None

    log.info(f"Found previous session from {data.get('date')}")
    return data


def restore_state(state: AlgoState, session: dict, live_positions: list[dict]) -> bool:
    """
    Restore AlgoState from session data.
    Cross-checks against live_positions from Dhan to ensure positions still exist.
    Returns True if any positions were restored.
    """
    live_sids = {str(p.get("securityId", "")) for p in live_positions}
    restored  = False

    for key in ("call_pos", "put_pos"):
        pos_dict = session.get(key)
        if pos_dict is None:
            continue
        sid = str(pos_dict.get("security_id", ""))
        if sid not in live_sids and live_sids:
            log.warning(f"Saved {key} (sec={sid}) not found in live positions — skipping")
            continue
        pos = Position.from_dict(pos_dict)
        if key == "call_pos":
            state.call_pos = pos
        else:
            state.put_pos = pos
        log.info(f"Restored {key}: {pos}")
        restored = True

    if session.get("expiry"):
        config.OPTION_EXPIRY = session["expiry"]
        log.info(f"Restored expiry: {config.OPTION_EXPIRY}")
    if session.get("global_tolerance") is not None:
        config.TOLERANCE = float(session["global_tolerance"])
    if session.get("num_lots") is not None:
        config.NUM_LOTS = int(session["num_lots"])

    # Restore S/R if not already set
    if not state.sr_levels and session.get("sr_levels"):
        state.sr_levels = sorted([SRLevel(r[0], r[1]) for r in session["sr_levels"]])
        log.info(f"Restored S/R levels: {state.sorted_levels()}")

    # Restore PnL counter (carry over realised PnL)
    if session.get("daily_pnl"):
        # Don't restore yesterday's PnL to today — it's a new day
        from datetime import date
        if session.get("date") == str(date.today()):
            state.daily_pnl = session["daily_pnl"]

    for key in ("pending_put_reentry", "pending_call_reentry"):
        raw = session.get(key)
        if raw:
            setattr(state, key, SRLevel(*raw))
            log.info(f"Restored {key}: {getattr(state, key)}")

    state.call_disabled = session.get("call_disabled", False)
    state.put_disabled  = session.get("put_disabled", False)

    if restored:
        _sync_fsm(state)

    return restored


def clear_session():
    """Remove session file (called after force_sell_all or end of day)."""
    if os.path.exists(config.SESSION_FILE):
        os.remove(config.SESSION_FILE)
        log.info("Session cleared")


def _sync_fsm(state: AlgoState):
    if state.has_call() and state.has_put(): state.fsm = FSMState.BOTH
    elif state.has_call():                   state.fsm = FSMState.CALL_ONLY
    elif state.has_put():                    state.fsm = FSMState.PUT_ONLY
    else:                                    state.fsm = FSMState.NO_POSITION
