"""
Non-interactive CSV backtest — same AlgoEngine as main.py run_backtest(),
with detailed event log (datetime, spot, reason, FSM, position S/R).

Usage:
  python tools/run_csv_backtest.py
  python tools/run_csv_backtest.py --first put
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
from core.states import AlgoState, SRLevel, Candle, Position
from core.algo_engine import AlgoEngine

CSV_PATH = os.path.join(ROOT, "tools", "data", "nifty50_1min_1219.csv")
SR_LEVELS = [
    23085, 23120, 23165, 23205, 23252, 23290, 23335, 23372, 23410, 23452,
    23495, 23535, 23580, 23625, 23668, 23708, 23765, 23805, 23852, 23900,
    23956, 24000, 24045,24083,24122,24168,24206,
]
TOLERANCE = 10
ENTRY_MULT = config.ENTRY_FILTER_MULT  # 1.2


def load_candles(path: str) -> list[tuple[str, Candle]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ts = datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
            rows.append((
                r["datetime"],
                Candle(float(r["open"]), float(r["high"]),
                       float(r["low"]), float(r["close"]), timestamp=ts),
            ))
    return rows


class BTClient:
    def __init__(self):
        self.current_spot = 0.0
        self._c = 0
        self.idx = config.INDEX_CONFIG[config.ACTIVE_INDEX]

    def check_funds_before_buy(self, *_a, **_k):
        return True, 0.0, 0.0

    def get_spot_ltp(self):
        return self.current_spot

    def get_expiry_list(self):
        return [config.OPTION_EXPIRY]

    def get_option_security_id(self, s, o, e):
        return str(int(90000 + s) if o == "CE" else int(80000 + s))

    def buy_option(self, sid, k, side, sp):
        self._c += 1
        fill = round(self.current_spot * 0.005, 2)
        return Position(
            side=side, strike=k, security_id=sid, entry_price=fill,
            entry_spot=sp, order_id=f"BT{self._c:05d}",
            quantity=config.NUM_LOTS * self.idx["lot_size"],
        )

    def sell_option(self, pos, reason=""):
        return round(self.current_spot * 0.005, 2)

    def force_sell_all(self, state):
        state.call_pos = state.put_pos = None
        return 0.0


def run(first_side: str = "call") -> dict:
    config.TOLERANCE = TOLERANCE
    config.ACTIVE_INDEX = "NIFTY50"
    rows = load_candles(CSV_PATH)
    sr = [SRLevel(x) for x in SR_LEVELS]
    state = AlgoState(sr_levels=sr, index_key="NIFTY50", mode="BACKTEST")
    client = BTClient()
    engine = AlgoEngine(client, state)
    events: list[dict] = []
    cur_dt = [rows[0][0]]

    def snap(reason: str, action: str, side: str = "", strike: float = 0):
        c = state.call_pos
        p = state.put_pos
        events.append({
            "datetime": cur_dt[0],
            "action": action,
            "side": side,
            "strike": strike,
            "spot": client.current_spot,
            "reason": reason,
            "fsm": state.fsm.name,
            "call_R": c.own_resistance.level if c and c.own_resistance else None,
            "call_S": c.own_support.level if c and c.own_support else None,
            "call_sl": c.sl_level if c else None,
            "put_S": p.own_support.level if p and p.own_support else None,
            "put_R": p.own_resistance.level if p and p.own_resistance else None,
            "put_sl": p.sl_level if p else None,
        })

    _ec = engine._enter_call
    _ep = engine._enter_put
    _xc = engine._exit_call
    _xp = engine._exit_put

    def enter_call(spot, reason=""):
        _ec(spot, reason)
        if state.call_pos:
            snap(reason, "BUY", "CALL", state.call_pos.strike)

    def enter_put(spot, reason=""):
        _ep(spot, reason)
        if state.put_pos:
            snap(reason, "BUY", "PUT", state.put_pos.strike)

    def exit_call(spot, reason=""):
        k = state.call_pos.strike if state.call_pos else 0
        _xc(spot, reason)
        snap(reason, "SELL", "CALL", k)

    def exit_put(spot, reason=""):
        k = state.put_pos.strike if state.put_pos else 0
        _xp(spot, reason)
        snap(reason, "SELL", "PUT", k)

    engine._enter_call = enter_call
    engine._enter_put = enter_put
    engine._exit_call = exit_call
    engine._exit_put = exit_put

    client.current_spot = rows[0][1].close
    if first_side == "call":
        engine.manual_buy_call(rows[0][1].close)
    elif first_side == "put":
        engine.manual_buy_put(rows[0][1].close)

    for i, (dt, candle) in enumerate(rows):
        if i == 0:
            continue  # first candle already used for manual entry (same as run_bt.py)
        cur_dt[0] = dt
        client.current_spot = candle.close
        engine.on_candle_close(candle)

    buys = [e for e in events if e["action"] == "BUY"]
    sells = [e for e in events if e["action"] == "SELL"]
    puts = [e for e in buys if e["side"] == "PUT"]
    calls = [e for e in buys if e["side"] == "CALL"]

    return {
        "candles": len(rows),
        "from": rows[0][0],
        "to": rows[-1][0],
        "first_spot": rows[0][1].close,
        "total_events": len(events),
        "buys": len(buys),
        "sells": len(sells),
        "put_buys": len(puts),
        "call_buys": len(calls),
        "daily_pnl": state.daily_pnl,
        "events": events,
        "open_call": state.call_pos is not None,
        "open_put": state.put_pos is not None,
    }


def validate_put_events(events: list[dict], sr_levels: list[float]) -> list[dict]:
    """Flag PUT buys that don't match 1.2x strategy rules."""
    issues = []
    tol = TOLERANCE
    mult = ENTRY_MULT

    for i, e in enumerate(events):
        if e["action"] != "BUY" or e["side"] != "PUT":
            continue
        reason = e["reason"]
        close = e["spot"]
        prev = events[i - 1] if i else None

        if reason == "MANUAL":
            continue
        if reason == "AUTO_AFTER_CALL_SL":
            # Should be close <= R - 1.2*tol for some resistance R
            ok = any(close <= lv - mult * tol for lv in sr_levels if lv > close)
            if not ok and prev and prev.get("call_S"):
                ref = prev["call_S"]
                ok = close <= ref - mult * tol
            if not ok:
                issues.append({**e, "issue": "AUTO_AFTER_CALL_SL: close not <= R-1.2x tol"})
            continue
        if reason == "PUT_REENTRY_AFTER_SL":
            # Ladder-advanced support ref: close < S - 1.2x for some S above close
            ok = any(lv > close and close < lv - mult * tol for lv in sr_levels)
            if not ok:
                issues.append({
                    **e,
                    "issue": (f"PUT_REENTRY_AFTER_SL: close={close} not below any "
                              f"S-1.2x gate (S>{close})"),
                })
            continue
        if reason == "OPPOSITE_SIDE_BREAK":
            # Case-II: support below CALL R, close < S - 1.2*tol
            call_r = e.get("call_R") or (prev.get("call_R") if prev else None)
            if call_r is None:
                issues.append({**e, "issue": "OPPOSITE_SIDE_BREAK: no CALL R context"})
                continue
            broken = [
                s for s in sr_levels
                if s < call_r and s > close and close < s - mult * tol
            ]
            if not broken:
                issues.append({
                    **e,
                    "issue": (f"OPPOSITE_SIDE_BREAK: no support below CALL R={call_r} "
                              f"broken at S-1.2x tol (close={close})"),
                })
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", choices=["call", "put"], default="call")
    ap.add_argument("--json", action="store_true", help="write full log to data/bt_csv_output.json")
    args = ap.parse_args()

    if not os.path.exists(CSV_PATH):
        print(f"CSV not found: {CSV_PATH}")
        sys.exit(1)

    result = run(args.first)
    issues = validate_put_events(result["events"], SR_LEVELS)

    print("\n" + "=" * 70)
    print(f"CSV BACKTEST  (same engine as main.py)  first=MANUAL {args.first.upper()}")
    print("=" * 70)
    print(f"  CSV       : {CSV_PATH}")
    print(f"  Range     : {result['from']} -> {result['to']}  ({result['candles']} candles)")
    print(f"  First spot: {result['first_spot']:.2f}")
    print(f"  S/R levels: {len(SR_LEVELS)}  tolerance={TOLERANCE}  entry_mult={ENTRY_MULT}")
    print(f"  Trades    : {result['buys']} buys, {result['sells']} sells "
          f"(CALL buys={result['call_buys']} PUT buys={result['put_buys']})")
    print(f"  Est PnL   : Rs {result['daily_pnl']:,.2f}")
    print(f"  Open end  : CALL={result['open_call']} PUT={result['open_put']}")

    print("\n--- First 25 events ---")
    for e in result["events"][:25]:
        print(f"  {e['datetime']}  {e['action']:<4} {e['side']:<4} k={e['strike']:<7.0f} "
              f"spot={e['spot']:.2f}  {e['reason']:<22} FSM={e['fsm']}")

    print("\n--- All PUT entries (first 15) ---")
    put_n = 0
    for e in result["events"]:
        if e["action"] == "BUY" and e["side"] == "PUT":
            put_n += 1
            if put_n <= 15:
                print(f"  {e['datetime']}  spot={e['spot']:.2f}  {e['reason']:<22} "
                      f"CALL_R={e.get('call_R')}  FSM={e['fsm']}")

    print(f"\n--- PUT validation ({len(issues)} issues in {result['put_buys']} PUT buys) ---")
    if issues:
        for x in issues[:10]:
            print(f"  !! {x['datetime']} spot={x['spot']:.2f} {x['reason']}: {x['issue']}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")
    else:
        print("  All PUT entries match 1.2x Case-II / AUTO_AFTER_CALL_SL rules.")

    by_reason: dict[str, int] = {}
    for e in result["events"]:
        if e["action"] == "BUY":
            k = f"{e['side']}/{e['reason']}"
            by_reason[k] = by_reason.get(k, 0) + 1
    print("\n--- Buy counts by reason ---")
    for k, v in sorted(by_reason.items()):
        print(f"  {k}: {v}")

    print("=" * 70)

    if args.json:
        out = os.path.join(ROOT, "data", "bt_csv_output.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({**result, "issues": issues, "sr_levels": SR_LEVELS}, f,
                      indent=2, default=str)
        print(f"Full log -> {out}")


if __name__ == "__main__":
    main()
