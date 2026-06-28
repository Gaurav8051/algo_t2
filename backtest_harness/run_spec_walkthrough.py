"""
run_spec_walkthrough.py — Scripted walk-through of Case-1 and Case-II
using the user's exact S/R levels (23805…24045) and tolerance=11.

Drives the REAL algo_engine.py — no mocks of strategy logic.

NOTE on opposite-side triggers (corrected):
  PUT while CALL open: only Case-II support break (below CALL's R, 1.0x tol).
  CALL while PUT open: PUT's R + 1.2x tol, or resistance break (Case-II mirror).
  R-1.2x PUT entry applies only AFTER CALL SL (auto-reversal), not at 23952.

Run:  python backtest_harness/run_spec_walkthrough.py
"""
from __future__ import annotations
import os
import sys
from datetime import datetime, timedelta

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG_ROOT)

import config
from core.states import AlgoState, SRLevel, Candle, Position
from core.algo_engine import AlgoEngine

# ── User S/R from spec ────────────────────────────────────────────────────────
SPEC_LEVELS = [
    23085, 23120, 23165, 23205, 23252, 23290, 23335, 23372, 23410, 23452,
    23495, 23535, 23580, 23625, 23668, 23708, 23765, 23805, 23852, 23900,
    23956, 24000, 24045,
]
TOLERANCE   = 11
BASE_DT     = datetime(2026, 6, 28, 9, 15)


def _candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(o, h, l, c, timestamp=BASE_DT + timedelta(minutes=i))


class WalkClient:
    """Minimal broker: records events, uses spot as index-point proxy."""

    def __init__(self):
        self.current_spot = 0.0
        self._n = 0
        self.events: list[dict] = []

    def check_funds_before_buy(self, security_id, strike, side):
        return True, 0.0, 0.0

    def get_option_security_id(self, strike, opt_type, expiry):
        return f"{int(strike)}{opt_type}"

    def buy_option(self, security_id, strike, side, entry_spot):
        self._n += 1
        lot = config.INDEX_CONFIG[config.ACTIVE_INDEX]["lot_size"]
        pos = Position(
            side=side, strike=strike, security_id=security_id,
            entry_price=entry_spot, entry_spot=entry_spot,
            order_id=f"WT{self._n:05d}", quantity=lot,
        )
        self.events.append({
            "n": self._n, "type": "BUY", "side": side,
            "strike": strike, "spot": entry_spot,
        })
        return pos

    def sell_option(self, pos, reason=""):
        exit_spot = self.current_spot
        pts = ((exit_spot - pos.entry_spot) if pos.side == "CALL"
               else (pos.entry_spot - exit_spot))
        self.events.append({
            "type": "SELL", "side": pos.side, "strike": pos.strike,
            "entry_spot": pos.entry_spot, "exit_spot": exit_spot,
            "pts": round(pts, 2), "reason": reason,
        })
        return exit_spot

    def force_sell_all(self, state):
        for pos in [state.call_pos, state.put_pos]:
            if pos:
                self.sell_option(pos, "FORCE")
        state.call_pos = state.put_pos = None
        return 0.0

    def get_position_ltp(self, pos):
        return self.current_spot


def _engine() -> tuple[AlgoEngine, AlgoState, WalkClient]:
    config.TOLERANCE = TOLERANCE
    config.ACTIVE_INDEX = "NIFTY50"
    levels = [SRLevel(x) for x in SPEC_LEVELS]
    state  = AlgoState(sr_levels=levels, index_key="NIFTY50", mode="BACKTEST")
    client = WalkClient()
    return AlgoEngine(client, state), state, client


def _pos_str(state: AlgoState) -> str:
    parts = []
    if state.call_pos:
        p = state.call_pos
        sl = f"SL@{p.sl_level:.0f}" if p.sl_active else "SL=WAIT"
        parts.append(f"CALL k={p.strike:.0f} R={p.own_resistance} S={p.own_support} {sl}")
    if state.put_pos:
        p = state.put_pos
        sl = f"SL@{p.sl_level:.0f}" if p.sl_active else "SL=WAIT"
        parts.append(f"PUT k={p.strike:.0f} S={p.own_support} R={p.own_resistance} {sl}")
    return " | ".join(parts) if parts else "FLAT"


def _run_phase(title: str, candles: list[tuple[str, Candle]],
               engine: AlgoEngine, state: AlgoState, client: WalkClient,
               first_side: str | None = None, first_spot: float | None = None):
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)

    client.events.clear()
    if first_side and first_spot is not None:
        client.current_spot = first_spot
        if first_side == "CALL":
            engine.manual_buy_call(first_spot)
        else:
            engine.manual_buy_put(first_spot)
        print(f"  >> MANUAL {first_side} @ spot={first_spot:.2f}")
        print(f"     {_pos_str(state)}")

    for label, c in candles:
        client.current_spot = c.close
        before = (state.call_pos is not None, state.put_pos is not None)
        engine.on_candle_close(c)
        after = (state.call_pos is not None, state.put_pos is not None)
        changed = " ***" if before != after else ""
        ev = client.events[-1] if client.events and client.events[-1].get("type") in ("BUY", "SELL") else None
        ev_txt = ""
        if ev and ev.get("type") == "BUY" and after != before:
            ev_txt = f"  [{ev['type']} {ev['side']} k={ev['strike']}]"
        elif ev and ev.get("type") == "SELL":
            ev_txt = (f"  [{ev['type']} {ev['side']} pts={ev.get('pts','?')} "
                      f"reason={ev.get('reason','')}]")
        print(f"  C={c.close:>8.2f}  FSM={state.fsm.name:<10}  {label:<36}{changed}{ev_txt}")
        print(f"           {_pos_str(state)}")

    print(f"\n  Events: {len(client.events)}")
    for e in client.events:
        print(f"    {e}")
    return client.events


def run_case1():
    """Case-1: Manual CALL, trail SL, auto PUT, PUT SL, optional false-breakdown."""
    engine, state, client = _engine()

    # Phase A — CALL-only until SL, then auto PUT at R-1.2x, PUT SL, CALL at R+1.2x
    phase_a = [
        ("Below R 23956 — no PUT (CALL only)",          _candle(1,  23920, 23930, 23918, 23952)),
        ("Close > 23956 — CALL SL @ 23945",             _candle(2,  23952, 23958, 23951, 23957)),
        ("Sideways above SL 23945",                     _candle(3,  23957, 23962, 23950, 23960)),
        ("Close > 24000 — CALL SL trails to 23989",     _candle(4,  23990, 24007, 23988, 24005)),
        ("Hold above SL 23989",                         _candle(5,  24000, 24006, 23995, 24000)),
        ("CALL SL + auto PUT @ R-1.2x (24000)",         _candle(6,  23995, 24000, 23980, 23985)),
        ("No CALL yet (need R+1.2x = 23969.2)",         _candle(7,  23980, 23982, 23945, 23948)),
        ("Still below 23969.2 — no CALL",               _candle(8,  23948, 23950, 23940, 23942)),
        ("PUT SL + auto CALL @ 23956+1.2x",            _candle(9,  23942, 23972, 23940, 23970)),
    ]
    _run_phase("CASE-1 Phase A: CALL trail -> SL -> auto PUT -> PUT SL",
               phase_a, engine, state, client,
               first_side="CALL", first_spot=23924.0)

    # Phase B — false breakdown: PUT held, CALL on breakout
    engine2, state2, client2 = _engine()
    phase_b = [
        ("CALL SL + auto PUT",                          _candle(1,  23995, 24000, 23980, 23985)),
        ("False breakdown — only to 23978",             _candle(2,  23985, 23988, 23975, 23978)),
        ("Rally toward 24000",                          _candle(3,  23978, 23995, 23976, 23992)),
        ("Close > 24013.2 — auto CALL (no CALL pos)",   _candle(4,  23992, 24018, 23990, 24015)),
    ]
    # Seed: manual CALL then fast-forward to PUT-only state
    client2.current_spot = 23924.0
    engine2.manual_buy_call(23924.0)
    for label, c in [
        ("R broken", _candle(0, 23952, 23958, 23951, 23957)),
        ("Trail SL", _candle(0, 23990, 24007, 23988, 24005)),
        ("CALL SL+PUT", _candle(0, 23995, 24000, 23980, 23985)),
    ]:
        client2.current_spot = c.close
        engine2.on_candle_close(c)
    print(f"\n  [Seeded PUT-only state: {_pos_str(state2)}]")
    _run_phase("CASE-1 Phase B: False breakdown -> CALL on 24000+1.2x tol breakout",
               phase_b[1:], engine2, state2, client2)


def run_case2():
    """Case-II: Manual CALL, market falls without hitting R, support break → PUT."""
    engine, state, client = _engine()
    candles = [
        ("Drift down — R 23956 not hit on CALL",        _candle(1,  23920, 23940, 23915, 23937)),
        ("Support 23900 broken — opposite PUT",         _candle(2,  23937, 23940, 23885, 23888)),
        ("Recovery — PUT SL may activate",              _candle(3,  23888, 23955, 23880, 23950)),
    ]
    _run_phase("CASE-II: CALL open, support break -> PUT (no R hit on CALL)",
               candles, engine, state, client,
               first_side="CALL", first_spot=23924.0)


def run_case2_mirror():
    """
    Case-II mirror: manual PUT, market rises without PUT SL, resistance
    23956 broken at +1.0x tol (23967) -> opposite CALL.
    Bounce path (R+1.2x = 23969.2) is a separate, later threshold.
    """
    engine, state, client = _engine()
    candles = [
        ("Drift up — PUT S 23900 SL not hit",           _candle(1,  23924, 23932, 23920, 23930)),
        ("Still below 23956+11 — no CALL yet",          _candle(2,  23930, 23945, 23928, 23945)),
        ("Approach R 23956, not broken +11",            _candle(3,  23945, 23960, 23942, 23955)),
        ("Close > 23967 — Case-II mirror CALL",         _candle(4,  23955, 23970, 23953, 23968)),
        ("CALL in BOTH; PUT unchanged",                 _candle(5,  23968, 23975, 23965, 23972)),
    ]
    _run_phase("CASE-II Mirror: PUT open, R 23956+11 break -> CALL",
               candles, engine, state, client,
               first_side="PUT", first_spot=23924.0)


def run_case2_mirror_bounce_only():
    """
    Sub-case: rally stays below 23967 but eventually crosses R+1.2x (23969.2).
    No 1.0x resistance break — only bounce path fires CALL.
    """
    engine, state, client = _engine()
    candles = [
        ("Rally but below 23967",                       _candle(1,  23924, 23950, 23922, 23950)),
        ("Still below 23969.2 bounce threshold",        _candle(2,  23950, 23965, 23948, 23965)),
        ("Close >= 23969.2 — bounce CALL (1.2x path)",  _candle(3,  23965, 23972, 23963, 23970)),
    ]
    _run_phase("CASE-II Mirror alt: CALL at R+1.2x (23969.2) only",
               candles, engine, state, client,
               first_side="PUT", first_spot=23924.0)


def run_case1_put_mirror():
    """PUT manual: support break activates PUT SL ladder (not opposite entry)."""
    engine, state, client = _engine()
    candles = [
        ("Drift up, S not hit yet",                     _candle(1,  23920, 23935, 23915, 23930)),
        ("Close < 23900 — PUT SL activated",            _candle(2,  23930, 23935, 23895, 23898)),
        ("Hold above PUT SL",                           _candle(3,  23898, 23910, 23890, 23905)),
    ]
    _run_phase("CASE-1 Mirror (manual PUT first): support break -> SL",
               candles, engine, state, client,
               first_side="PUT", first_spot=23924.0)


def main():
    print("\nSPEC WALK-THROUGH BACKTEST")
    print(f"S/R levels : {SPEC_LEVELS}")
    print(f"Tolerance  : {TOLERANCE}")
    print(f"2nd OTM    : CALL->24000, PUT->23900 at spot~23924")

    run_case1()
    run_case2()
    run_case2_mirror()
    run_case2_mirror_bounce_only()
    run_case1_put_mirror()

    print("\n" + "=" * 72)
    print("  EXPECTED CHECKLIST")
    print("  [x] Case-1A: CALL only at 23952; PUT after CALL SL at 23985")
    print("  [x] Case-1A: no CALL at 23948/23942; PUT SL + CALL at 23970 (23956+1.2x)")
    print("  [x] Case-1B: trapped PUT; CALL at 24015 (24000+1.2x11)")
    print("  [x] Case-II:   PUT only at 23888 (23900-11 support break)")
    print("  [x] Case-II mirror: CALL at 23968 (23956+11 resistance break)")
    print("  [x] Case-II mirror alt: CALL at 23970 (23956+1.2x11 bounce)")
    print("=" * 72)


if __name__ == "__main__":
    main()
