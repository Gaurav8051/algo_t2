"""
backtest_harness/run_bt.py
───────────────────────────────────────────────────────────────────────────
Reusable backtest harness — drives the REAL core/algo_engine.py and
core/sr_engine.py (the exact same code used by main.py for paper/live
trading) over a 1-minute OHLC CSV, so you can sanity-check signal logic
against your own S/R levels and historical data without touching the
live engine code at all.

USAGE
  1. Drop your 1-min CSV next to this script (or set CSV_PATH below).
     Required columns: datetime,open,high,low,close
     datetime format:  YYYY-MM-DD HH:MM:SS
  2. Edit the "USER SETTINGS" block below: S/R levels, tolerance, index,
     expiry, and your manual first trade (direction + entry point).
  3. Run:  python3 run_bt.py
  4. Read the console report, and/or open bt_output.json for the full
     structured event log (every BUY/SELL with timestamps and prices).

IMPORTANT CAVEAT — read before trusting any P&L number this prints:
This harness reports INDEX-POINT P&L (Nifty/BankNifty/etc points gained
or lost on the underlying's move between entry and exit), NOT real
option-premium P&L. There is no real option-pricing model here (no
strike distance, no time decay, no implied volatility) — building one
requires a separate, deliberate pricing module. Index-point P&L only
tells you whether the SIGNAL LOGIC (entries/exits/SL placement/
reversals) is directionally sound; it does NOT tell you what you'd
actually have made or lost in Rupees. Treat the trade list and timing
as the trustworthy part of this report, and the point-total as a rough
direction indicator only.
"""
import sys, os, csv, json
from datetime import datetime

# Make the package's core/ and config.py importable regardless of which
# directory this script is actually run from.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PKG_ROOT)
import config
from core.states import AlgoState, SRLevel, Candle, Position
from core.algo_engine import AlgoEngine

# ─────────────────────────── USER SETTINGS ─────────────────────────────────
CSV_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nifty50_1min.csv")
TOLERANCE  = 10            # global tolerance (index points) applied to all levels below
LEVELS     = [23056,23094,23133,23172,23218,23262,23298,23433,23472,23508,23545,
              23582,23628,23666,23701,23744,23792,23828,23870,23908,23956,24000,
              24042,24089,24123,24156,24192]
ACTIVE_INDEX = "NIFTY50"   # must be a key in config.INDEX_CONFIG
OPTION_EXPIRY = "2026-06-30"

# Manual first trade — REQUIRED. The engine does nothing while flat; your
# spec requires the very first trade of any run to be placed manually.
FIRST_TRADE_SIDE = "PUT"          # "PUT" or "CALL"
FIRST_TRADE_AT_FIRST_CANDLE = True  # True: enter at candle[0].close
                                     # False: set FIRST_TRADE_SPOT below instead
FIRST_TRADE_SPOT = None             # only used if FIRST_TRADE_AT_FIRST_CANDLE=False
# ─────────────────────────────────────────────────────────────────────────────

config.TOLERANCE   = TOLERANCE
config.ACTIVE_INDEX = ACTIVE_INDEX
STEP = config.INDEX_CONFIG[ACTIVE_INDEX]["strike_step"]
LOT  = config.INDEX_CONFIG[ACTIVE_INDEX]["lot_size"]
EXPIRY = OPTION_EXPIRY

# ── Fake broker client: records trades, returns index-point "fill" markers ──
class BTClient:
    def __init__(self):
        self.current_spot = 0.0
        self.current_dt = ""
        self._n = 0
        self.events = []   # full structured log

    def get_option_security_id(self, strike, opt_type, expiry):
        return f"{int(strike)}{opt_type}"

    def buy_option(self, security_id, strike, side, entry_spot):
        self._n += 1
        pos = Position(side=side, strike=strike, security_id=security_id,
                        entry_price=entry_spot,  # store SPOT as "entry_price" proxy for point-tracking
                        entry_spot=entry_spot, order_id=f"BT{self._n:05d}",
                        quantity=LOT)
        self.events.append({"n": self._n, "dt": self.current_dt, "type": "BUY", "side": side,
                             "strike": strike, "entry_spot": entry_spot})
        return pos

    def sell_option(self, pos, reason=""):
        exit_spot = self.current_spot
        # index-point move in the option's favor (call: spot up good; put: spot down good)
        pts = (exit_spot - pos.entry_spot) if pos.side == "CALL" else (pos.entry_spot - exit_spot)
        self.events.append({"dt": self.current_dt, "type": "SELL", "side": pos.side, "strike": pos.strike,
                             "entry_spot": pos.entry_spot, "exit_spot": exit_spot,
                             "pts": round(pts, 2), "reason": reason})
        return exit_spot  # returned value used by engine as "exit_px"; we recompute pnl below ourselves

    def force_sell_all(self, state):
        total = 0.0
        for pos in [state.call_pos, state.put_pos]:
            if pos:
                self.sell_option(pos, "FORCE")
        state.call_pos = state.put_pos = None
        return total

    def get_position_ltp(self, pos):
        # Backtest has no real option-premium model; treat "ltp" as spot
        # so unrealized-P&L seeding at day boundaries is at least directional
        # (this path is also skipped entirely in BACKTEST mode by the engine
        # itself, so it should never actually be called here).
        return self.current_spot


def load_candles(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ts = datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
            rows.append({
                "datetime": r["datetime"],
                "candle": Candle(float(r["open"]), float(r["high"]),
                                  float(r["low"]), float(r["close"]), timestamp=ts)
            })
    return rows


def main():
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: CSV not found at {CSV_PATH}")
        print("Edit CSV_PATH at the top of this script, or place your CSV "
              "in the backtest_harness/ folder.")
        sys.exit(1)

    rows = load_candles(CSV_PATH)
    print(f"Loaded {len(rows)} candles, {rows[0]['datetime']} -> {rows[-1]['datetime']}")

    levels = [SRLevel(x) for x in LEVELS]
    state  = AlgoState(sr_levels=levels, index_key=ACTIVE_INDEX, mode="BACKTEST")
    client = BTClient()
    engine = AlgoEngine(client, state)

    first_spot = (rows[0]["candle"].close if FIRST_TRADE_AT_FIRST_CANDLE
                  else FIRST_TRADE_SPOT)
    if first_spot is None:
        print("ERROR: FIRST_TRADE_AT_FIRST_CANDLE is False but FIRST_TRADE_SPOT "
              "was not set. Edit the USER SETTINGS block.")
        sys.exit(1)

    client.current_spot = first_spot
    client.current_dt = rows[0]["datetime"]
    if FIRST_TRADE_SIDE.upper() == "PUT":
        engine.manual_buy_put(first_spot)
        pos = state.put_pos
    else:
        engine.manual_buy_call(first_spot)
        pos = state.call_pos
    print(f"Manual first trade: {FIRST_TRADE_SIDE.upper()} at spot={first_spot} "
          f"(strike={pos.strike if pos else 'N/A'}, "
          f"support={pos.own_support if pos else '?'}, "
          f"resistance={pos.own_resistance if pos else '?'})")

    sig_log = []
    prev_state = FIRST_TRADE_SIDE.upper()

    for i, row in enumerate(rows):
        if i == 0:
            continue  # candle 0 already consumed as the manual entry trigger
        c = row["candle"]
        client.current_spot = c.close
        client.current_dt = row["datetime"]
        had_call_before = state.call_pos is not None
        had_put_before  = state.put_pos is not None

        engine.on_candle_close(c)

        cur_state = ("CALL" if state.call_pos else "") + ("PUT" if state.put_pos else "") or "FLAT"
        if cur_state != prev_state or (state.call_pos and not had_call_before) or (state.put_pos and not had_put_before):
            sig_log.append({
                "i": i, "dt": row["datetime"], "close": c.close,
                "prev_state": prev_state, "new_state": cur_state,
            })
        prev_state = cur_state

    print(f"\nTotal BUY events: {sum(1 for e in client.events if e['type']=='BUY')}")
    print(f"Total SELL events: {sum(1 for e in client.events if e['type']=='SELL')}")
    print(f"\nFinal open position: CALL={state.call_pos}, PUT={state.put_pos}")
    print(f"\n--- Full event log ---")
    for e in client.events:
        print(e)
    print(f"\n--- State transition log (when call/put appears/disappears) ---")
    for s in sig_log:
        print(s)

    # ── Day-by-day summary ──────────────────────────────────────────────────
    print(f"\n--- Day-by-day trade count ---")
    by_day = {}
    for e in client.events:
        day = e["dt"][:10]
        by_day.setdefault(day, []).append(e)
    for day in sorted(by_day):
        evs = by_day[day]
        buys = sum(1 for e in evs if e["type"] == "BUY")
        sells = sum(1 for e in evs if e["type"] == "SELL")
        pts = sum(e.get("pts", 0) for e in evs if e["type"] == "SELL")
        print(f"  {day}: {buys} buys, {sells} sells, net index-pts on sells={pts:+.2f}")

    total_pts = sum(e.get("pts", 0) for e in client.events if e["type"] == "SELL")
    print(f"\nTOTAL net index-points across all closed trades: {total_pts:+.2f}")
    print("(This is NOT real option P&L -- see header caveat. It tells us the signal")
    print(" direction was net favorable/unfavorable, not what you'd have actually made.)")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_output.json")
    json.dump({"events": client.events, "transitions": sig_log},
               open(out_path, "w"), indent=2, default=str)
    print(f"\nFull structured log written to: {out_path}")

if __name__ == "__main__":
    main()
