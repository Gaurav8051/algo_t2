"""
main.py — Nifty Algo Trader v5  (Dhan API v2.2.0)

  python main.py                 -> interactive mode picker
  python main.py --mode paper    -> paper trading
  python main.py --mode live     -> live trading
  python main.py --mode backtest -> offline CSV

On Windows: opens a second PowerShell window for dashboard automatically.
On Linux (server): dashboard.py runs over SSH from your local machine.

DELIVERY mode: positions persist overnight; next session auto-resumes.
"""

from __future__ import annotations
import argparse, json, logging, os, platform, subprocess, sys, time
from datetime import date

import config
from core.states import AlgoState, FSMState, SRLevel

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(name)s] %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("algo.main")


# ═════════════════════════════════════════════════════════════════════════════
# Dashboard auto-launch (Windows only)
# ═════════════════════════════════════════════════════════════════════════════

def _launch_dashboard_window():
    """Open dashboard.py in a new PowerShell window (Windows only)."""
    if platform.system() != "Windows":
        log.info("Server mode: run 'python dashboard.py' in a separate SSH terminal.")
        return
    py   = sys.executable
    dash = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.py")
    cmd  = f'start powershell -NoExit -Command "& \'{py}\' \'{dash}\'"'
    try:
        subprocess.Popen(cmd, shell=True)
        log.info("Dashboard window opened automatically.")
    except Exception as e:
        log.warning(f"Could not auto-open dashboard: {e}")
        log.info("Manually run in second terminal: python dashboard.py")


# ═════════════════════════════════════════════════════════════════════════════
# Snapshot  (written every 10s and after every candle)
# ═════════════════════════════════════════════════════════════════════════════

def _pos_snap(pos):
    if pos is None: return None
    return {
        "side": pos.side, "strike": pos.strike, "security_id": pos.security_id,
        "entry_price": pos.entry_price, "entry_spot": pos.entry_spot,
        "sl_level": pos.sl_level, "sl_active": pos.sl_active,
        "own_support": str(pos.own_support), "own_resistance": str(pos.own_resistance),
        "candle_filter_active": pos.candle_filter.active,
        "filter_type": pos.candle_filter.filter_type,
    }


def write_snapshot(state: AlgoState, mode: str,
                   last_tick: float = 0.0, unrealised: float = 0.0):
    snap = {
        "mode":           mode,
        "index":          state.index_key,
        "fsm":            state.fsm.name,
        "last_close":     state.last_candle.close if state.last_candle else None,
        "last_tick":      last_tick,
        "daily_pnl":      state.daily_pnl,
        "unrealised_pnl": unrealised,
        "total_pnl":      state.daily_pnl + unrealised,
        "sr_levels":      [[sr.level, sr.tolerance] for sr in state.sr_levels],
        "call_pos":       _pos_snap(state.call_pos),
        "put_pos":        _pos_snap(state.put_pos),
        "timestamp":      time.time(),
        "product_type":   config.PRODUCT_TYPE,
        "expiry":         config.OPTION_EXPIRY,
    }
    try:
        with open(config.SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2)
    except Exception as e:
        log.error(f"Snapshot error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# Command processor
# ═════════════════════════════════════════════════════════════════════════════

_SHUTDOWN = {"requested": False, "reason": ""}


def process_commands(engine, state: AlgoState, client, mode: str):
    if not os.path.exists(config.CMD_FILE): return
    try:
        with open(config.CMD_FILE, encoding="utf-8") as f:
            cmd = json.load(f)
        os.remove(config.CMD_FILE)
    except Exception as e:
        log.error(f"Command read error: {e}"); return

    action = cmd.get("action", "")
    spot   = state.last_candle.close if state.last_candle else client.get_spot_ltp()
    try:
        if   action == "buy_call":   engine.manual_buy_call(spot)
        elif action == "buy_put":    engine.manual_buy_put(spot)
        elif action == "force_sell": engine.manual_force_sell()
        elif action == "set_pnl":    engine.manual_set_pnl(float(cmd["value"]))
        elif action == "add_sr":     engine.add_sr(float(cmd["level"]), cmd.get("tolerance"))
        elif action == "del_sr":     engine.delete_sr(float(cmd["level"]))
        elif action == "set_tol":    engine.modify_sr_tol(float(cmd["level"]), float(cmd["tolerance"]))
        elif action == "set_profit": config.DAILY_PROFIT_TARGET = float(cmd["value"])
        elif action == "set_loss":   config.DAILY_LOSS_LIMIT    = float(cmd["value"])
        elif action == "set_expiry": config.OPTION_EXPIRY       = cmd["value"]
        elif action == "set_index":  config.ACTIVE_INDEX        = cmd["value"]
        elif action == "q_paper":
            if mode != "paper":
                log.warning("Q-paper ignored — only valid in PAPER mode")
            else:
                log.info("Q-paper received — graceful paper shutdown queued")
                _SHUTDOWN["requested"] = True
                _SHUTDOWN["reason"]    = "Q_PAPER"
        else: log.warning(f"Unknown command: {action}")
    except Exception as e:
        log.error(f"Command error ({action}): {e}", exc_info=True)


# ═════════════════════════════════════════════════════════════════════════════
# Shared setup helpers
# ═════════════════════════════════════════════════════════════════════════════

def _pick_mode() -> str:
    print("\n+============================================+")
    print("|   NIFTY ALGO TRADER  (Dhan v2.2.0)        |")
    print("+============================================+")
    print("|  [1] paper     - Real ticks, fake orders   |")
    print("|  [2] live      - Real orders on Dhan       |")
    print("|  [3] backtest  - Offline CSV               |")
    print("+============================================+")
    c = input("Select (1/2/3): ").strip()
    return {"1":"paper","2":"live","3":"backtest"}.get(c, "paper")


def _pick_index() -> str:
    options = list(config.INDEX_CONFIG.keys())
    print("\n-- Index Selection --")
    for i, k in enumerate(options):
        print(f"  [{i+1}] {k}  ({config.INDEX_CONFIG[k]['name']})")
    cur = config.ACTIVE_INDEX
    inp = input(f"\nCurrent={cur}  Enter to keep or type number/name: ").strip()
    if not inp: return cur
    if inp.isdigit() and 1 <= int(inp) <= len(options):
        return options[int(inp)-1]
    if inp.upper() in options:
        return inp.upper()
    return cur


def _confirm_expiry(client) -> str:
    print("\n-- Option Expiries --")
    try:
        expiries = client.get_expiry_list()
        for i, e in enumerate(expiries[:8]):
            print(f"  [{i}] {e}")
    except Exception as ex:
        log.warning(f"Expiry list failed: {ex}")
    cur = config.OPTION_EXPIRY
    inp = input(f"\nCurrent={cur}  Enter to keep or type date (YYYY-MM-DD): ").strip()
    return inp if inp else cur


def _confirm_sr() -> list[SRLevel]:
    levels = sorted([SRLevel(r[0], r[1]) for r in config.SR_LEVELS])
    print("\n-- S/R Levels --")
    for sr in levels:
        t = f"  T={sr.tolerance}" if sr.tolerance else ""
        print(f"  {sr.level}{t}")
    if input("\nPress Enter to use these, or type 'edit': ").strip().lower() == "edit":
        while True:
            raw = input("Levels comma-separated (e.g. 23800,23850): ").strip()
            # Tolerate stray double-commas, trailing/leading commas, and
            # extra whitespace -- a single typo in a long list used to
            # crash the whole program with a raw traceback (ValueError on
            # float('')). Now: skip blank entries, validate each remaining
            # one individually, and report exactly which token was bad so
            # the user can fix just that part and retry, instead of losing
            # the whole input and restarting main.py.
            tokens = [t.strip() for t in raw.split(",")]
            tokens = [t for t in tokens if t != ""]  # drop empty entries
            if not tokens:
                print("  No valid numbers entered — try again.")
                continue
            parsed, bad = [], []
            for t in tokens:
                try:
                    parsed.append(float(t))
                except ValueError:
                    bad.append(t)
            if bad:
                print(f"  Could not parse: {bad!r} — these were ignored. "
                      f"Check for typos (stray commas, letters, etc).")
                if not parsed:
                    print("  Nothing valid was entered — try again.")
                    continue
                if input(f"  Proceed with the {len(parsed)} valid level(s) "
                          f"parsed? (yes/no): ").strip().lower() != "yes":
                    continue
            levels = sorted(SRLevel(x) for x in parsed)
            break
    return levels


def _first_trade(engine, client) -> bool:
    """Returns True if a trade was placed (False = skip)."""
    print("\n-- First Trade --")
    print("  call / put / skip  (also: c / p / buy call / buy put)")
    raw    = input("> ").strip().lower().replace("buy ", "")
    if raw in ("c",): raw = "call"
    if raw in ("p",): raw = "put"
    spot   = client.get_spot_ltp()
    log.info(f"Spot at first trade: {spot:.2f}")
    if   "call" in raw: engine.manual_buy_call(spot); return True
    elif "put"  in raw: engine.manual_buy_put(spot);  return True
    log.info("Skipping first trade — use 'buy call'/'buy put' in dashboard.")
    return False


def _check_creds():
    if "your_client_id" in config.CLIENT_ID or "your_access_token" in config.ACCESS_TOKEN:
        log.error("Credentials not set in config.py"); sys.exit(1)


def _unrealised_pnl(state: AlgoState, client, paper_client=None) -> float:
    if paper_client is not None:
        return paper_client.unrealised_pnl(state)
    getter = getattr(client, "unrealised_pnl", None)
    if getter is not None:
        return getter(state)
    return 0.0


def _save_paper_last_snapshot(state: AlgoState, client, paper_client, mode: str):
    """Persist final paper-trading snapshot when quitting via Q-paper."""
    tick   = getattr(client, "current_spot", 0.0)
    unreal = _unrealised_pnl(state, client, paper_client)
    snap = {
        "quit_reason": "Q_PAPER",
        "mode": mode,
        "index": state.index_key,
        "fsm": state.fsm.name,
        "daily_pnl": state.daily_pnl,
        "unrealised_pnl": unreal,
        "total_pnl": state.daily_pnl + unreal,
        "sr_levels": [[sr.level, sr.tolerance] for sr in state.sr_levels],
        "call_pos": _pos_snap(state.call_pos),
        "put_pos":  _pos_snap(state.put_pos),
        "trade_count": len(paper_client.trades) if paper_client else 0,
        "timestamp": time.time(),
    }
    try:
        with open(config.PAPER_LAST_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2)
        log.info(f"Paper last snapshot saved → {config.PAPER_LAST_SNAPSHOT_FILE}")
    except Exception as e:
        log.error(f"Could not save paper last snapshot: {e}")


def _graceful_shutdown(engine, state: AlgoState, client, mode: str,
                       paper_client=None, reason: str = "CTRL_C"):
    import core.telegram_alert as tg
    from core.session import save_session, clear_session

    tick   = getattr(client, "current_spot", 0.0)
    unreal = _unrealised_pnl(state, client, paper_client)

    if reason == "Q_PAPER" and mode == "paper":
        log.info("Q-paper: closing all virtual positions and saving final snapshot")
        spot = state.last_candle.close if state.last_candle else tick
        if state.call_pos:
            engine._exit_call(spot, "Q_PAPER")
        if state.put_pos:
            engine._exit_put(spot, "Q_PAPER")
        _save_paper_last_snapshot(state, client, paper_client, mode)
        clear_session()
        write_snapshot(state, mode, tick, 0.0)
        trades = len(paper_client.trades) if paper_client else 0
        tg.alert_session_end(state.daily_pnl, trades)
        log.info(f"Paper session ended. Realised PnL: Rs{state.daily_pnl:,.2f}")
        return

    if config.PRODUCT_TYPE.upper() == "DELIVERY":
        save_session(state, mode)
        write_snapshot(state, mode, tick, unreal)
        trades = len(paper_client.trades) if paper_client else 0
        tg.alert_session_end(state.daily_pnl, trades)
        log.info(
            f"DELIVERY shutdown ({reason}) — positions LEFT OPEN for next session. "
            f"Realised=Rs{state.daily_pnl:,.2f}  Unrealised=Rs{unreal:,.2f}"
        )
        return

    log.warning(f"INTRA shutdown ({reason}) — force-closing all positions")
    engine.manual_force_sell()
    write_snapshot(state, mode, 0, 0)
    trades = len(paper_client.trades) if paper_client else 0
    tg.alert_session_end(state.daily_pnl, trades)
    log.info(f"Realised PnL: Rs{state.daily_pnl:,.2f}")


def _run_loop(engine, state, client, mode: str, paper_client=None):
    """Main loop: process commands every 1s, refresh snapshot every 10s."""
    last_snap = 0.0
    try:
        while True:
            process_commands(engine, state, client, mode)
            if _SHUTDOWN["requested"]:
                _graceful_shutdown(engine, state, client, mode, paper_client,
                                   _SHUTDOWN["reason"])
                break
            now = time.time()
            if now - last_snap >= 10:
                tick   = getattr(client, "current_spot", 0.0)
                unreal = _unrealised_pnl(state, client, paper_client)
                write_snapshot(state, mode, tick, unreal)
                from core.session import save_session
                save_session(state, mode)
                last_snap = now
            time.sleep(1)
    except KeyboardInterrupt:
        log.warning(f"Ctrl+C — shutting down {mode.upper()}")
        _graceful_shutdown(engine, state, client, mode, paper_client, "CTRL_C")


# ═════════════════════════════════════════════════════════════════════════════
# PAPER MODE
# ═════════════════════════════════════════════════════════════════════════════

def run_paper():
    log.info("MODE: PAPER")
    _check_creds()
    from dhanhq import DhanContext
    from core.paper_api import PaperClient
    from core.algo_engine import AlgoEngine
    from core.market_feed import start_feed
    from core.session import load_session, restore_state
    import core.telegram_alert as tg

    # Index selection
    config.ACTIVE_INDEX = _pick_index()
    log.info(f"Active index: {config.ACTIVE_INDEX}")

    ctx    = DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)
    client = PaperClient(ctx)

    try:
        spot = client.get_spot_ltp()
        if spot > 0:
            log.info(f"PAPER connected. Nifty spot={spot:.2f}")
        else:
            log.info(
                "PAPER connected. Spot=0 (market is closed — normal outside 9:15-15:30 IST).\n"
                "  Startup proceeds normally. Live prices will stream when market opens.\n"
                "  Paper trade fills outside market hours use fallback price (0.5% of spot)."
            )
    except Exception as e:
        log.warning(f"Spot fetch failed: {e} — continuing anyway (market may be closed)")
        spot = 0.0

    config.OPTION_EXPIRY = _confirm_expiry(client)
    sr_levels = _confirm_sr()
    state     = AlgoState(sr_levels=sr_levels, index_key=config.ACTIVE_INDEX, mode="PAPER")
    engine    = AlgoEngine(client, state)

    # Try to restore previous session (delivery carry-forward)
    session = load_session("paper")
    if session:
        print(f"\n  Previous session found ({session.get('date')}).")
        print("  Positions: "
              + ("CALL " if session.get("call_pos") else "")
              + ("PUT "  if session.get("put_pos")  else "")
              + ("(none)" if not session.get("call_pos") and not session.get("put_pos") else ""))
        if input("  Restore these positions? (yes/no): ").strip().lower() == "yes":
            restored = restore_state(state, session, [])
            if restored:
                log.info("Session restored. Skipping first-trade prompt.")
            else:
                _first_trade(engine, client)
        else:
            _first_trade(engine, client)
    else:
        _first_trade(engine, client)

    # Patch candle close
    _orig = engine.on_candle_close
    def _patched(c):
        _orig(c)
        unreal = client.unrealised_pnl(state)
        write_snapshot(state, "paper", client.current_spot, unreal)
        from core.session import save_session
        save_session(state, "paper")
        print(f"  [PAPER] {state.fsm.name:<12}"
              f"  Tick={client.current_spot:.2f}"
              f"  Real=Rs{state.daily_pnl:,.2f}"
              f"  Unreal=Rs{unreal:,.2f}"
              f"  Total=Rs{state.daily_pnl+unreal:,.2f}"
              f"  Trades={len(client.trades)}")
    engine.on_candle_close = _patched

    write_snapshot(state, "paper", client.current_spot)
    start_feed(ctx, engine, spot_setter=lambda p: setattr(client, "current_spot", p))
    tg.alert_session_start(config.ACTIVE_INDEX, "PAPER", config.OPTION_EXPIRY)

    _launch_dashboard_window()
    log.info("\nPAPER TRADING ACTIVE — Ctrl+C to stop (DELIVERY: keeps positions) | dashboard: Q-paper\n")
    _run_loop(engine, state, client, "paper", paper_client=client)


# ═════════════════════════════════════════════════════════════════════════════
# LIVE MODE
# ═════════════════════════════════════════════════════════════════════════════

def run_live():
    log.info("MODE: LIVE (real orders)")
    _check_creds()
    from dhanhq import DhanContext
    from core.algo_engine import AlgoEngine
    from core.market_feed import start_feed
    from core.session import load_session, restore_state
    import core.dhan_api as _api
    import core.telegram_alert as tg

    config.ACTIVE_INDEX = _pick_index()

    ctx  = DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)
    dhan = _api.make_dhan(ctx)

    class LiveClient:
        current_spot = 0.0
        def get_spot_ltp(self): return _api.get_spot_ltp(dhan)
        def get_expiry_list(self): return _api.get_expiry_list(dhan)
        def get_option_security_id(self, s, o, e):
            return _api.get_option_security_id(dhan, s, o, e)
        def check_funds_before_buy(self, security_id, strike, side):
            return _api.check_funds_before_buy(dhan, security_id, strike, side)
        def buy_option(self, sid, k, side, sp):
            return _api.buy_option(dhan, sid, k, side, sp)
        def sell_option(self, pos, reason=""):
            return _api.sell_option(dhan, pos, reason=reason)
        def force_sell_all(self, state):
            return _api.force_sell_all(dhan, state)
        def get_position_ltp(self, pos):
            opt_seg = config.INDEX_CONFIG[config.ACTIVE_INDEX]["opt_seg"]
            return _api.get_option_ltp(dhan, pos.security_id, opt_seg)
        def unrealised_pnl(self, state):
            total = 0.0
            for pos in [state.call_pos, state.put_pos]:
                if pos is None:
                    continue
                try:
                    ltp = self.get_position_ltp(pos)
                    total += (ltp - pos.entry_price) * pos.quantity
                except Exception as e:
                    log.debug(f"unrealised_pnl skip {pos.side}: {e}")
            return total

    client = LiveClient()

    try:
        spot = client.get_spot_ltp()
        if spot > 0:
            log.info(f"LIVE connected. Nifty spot={spot:.2f}")
        else:
            log.warning(
                "Spot=0 from Dhan — market may be closed or connectivity issue.\n"
                "  If market is open (9:15-15:30 IST), check internet and Dhan API status."
            )
    except Exception as e:
        log.error(f"Dhan connection failed: {e}\nCheck CLIENT_ID, ACCESS_TOKEN, IP whitelist.")
        sys.exit(1)

    try:
        _api.verify_sdk_constants(dhan)
    except RuntimeError as e:
        log.error(f"SDK constant check failed — refusing to start LIVE trading.\n{e}")
        sys.exit(1)

    config.OPTION_EXPIRY = _confirm_expiry(client)
    sr_levels = _confirm_sr()
    state     = AlgoState(sr_levels=sr_levels, index_key=config.ACTIVE_INDEX, mode="LIVE")
    engine    = AlgoEngine(client, state)

    # Session restore
    session = load_session("live")
    if session:
        live_pos = _api.get_open_positions(dhan)
        print(f"\n  Previous session found. Live positions on exchange: {len(live_pos)}")
        if input("  Restore? (yes/no): ").strip().lower() == "yes":
            restored = restore_state(state, session, live_pos)
            if not restored: _first_trade(engine, client)
        else:
            _first_trade(engine, client)
    else:
        _first_trade(engine, client)

    last_tick = [spot]
    _orig = engine.on_candle_close
    def _patched(c):
        _orig(c)
        unreal = client.unrealised_pnl(state)
        write_snapshot(state, "live", last_tick[0], unreal)
        from core.session import save_session
        save_session(state, "live")
    engine.on_candle_close = _patched

    write_snapshot(state, "live", spot)
    def _set_last_tick(p):
        last_tick[0] = p
    start_feed(ctx, engine, spot_setter=_set_last_tick)
    tg.alert_session_start(config.ACTIVE_INDEX, "LIVE", config.OPTION_EXPIRY)

    _launch_dashboard_window()
    log.info("\nLIVE TRADING ACTIVE — Ctrl+C to stop (DELIVERY: keeps positions open)\n")
    _run_loop(engine, state, client, "live")


# ═════════════════════════════════════════════════════════════════════════════
# BACKTEST MODE
# ═════════════════════════════════════════════════════════════════════════════

def run_backtest():
    log.info("MODE: BACKTEST")
    import csv
    from core.states import Candle
    from core.algo_engine import AlgoEngine

    config.ACTIVE_INDEX = _pick_index()
    step = config.INDEX_CONFIG[config.ACTIVE_INDEX]["strike_step"]

    default = os.path.join("data", "nifty_1min.csv")
    path    = input(f"\nCSV path [{default}]: ").strip() or default
    if not os.path.exists(path):
        print(f"File not found: {path}")
        print("Download: python tools/download_history.py 2025-06-01 2025-06-20")
        sys.exit(1)

    candles = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = None
            if row.get("datetime"):
                try:
                    from datetime import datetime as _dtcls
                    ts = _dtcls.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass  # leave ts=None if the column is missing/malformed — boundary check just won't fire
            candles.append(Candle(float(row["open"]), float(row["high"]),
                                  float(row["low"]),  float(row["close"]), timestamp=ts))
    print(f"Loaded {len(candles)} candles.")

    class BT:
        current_spot = 0.0; _c = 0; trades = []
        idx = config.INDEX_CONFIG[config.ACTIVE_INDEX]
        def get_spot_ltp(self): return self.current_spot
        def get_expiry_list(self): return [config.OPTION_EXPIRY]
        def get_option_security_id(self, s, o, e):
            return str(int(90000+s) if o=="CE" else int(80000+s))
        def buy_option(self, sid, k, side, sp):
            from core.states import Position, CandleFilterState
            self._c += 1; fill = round(self.current_spot*0.005, 2)
            self.trades.append({"type":"BUY","side":side,"k":k,"fill":fill,"spot":self.current_spot})
            return Position(side=side,strike=k,security_id=sid,entry_price=fill,
                            entry_spot=sp,order_id=f"BT{self._c:05d}",
                            quantity=config.NUM_LOTS*self.idx["lot_size"])
        def sell_option(self, pos, reason=""):
            fill = round(self.current_spot*0.005, 2)
            self.trades.append({"type":"SELL","side":pos.side,"k":pos.strike,
                                 "fill":fill,"spot":self.current_spot,"reason":reason})
            return fill
        def force_sell_all(self, state):
            total = 0.0
            for pos in [state.call_pos, state.put_pos]:
                if pos:
                    f = self.sell_option(pos,"FORCE"); total += (f-pos.entry_price)*pos.quantity
            state.call_pos = state.put_pos = None; return total

    bt     = BT()
    sr     = _confirm_sr()
    inp    = input(f"\nExpiry [{config.OPTION_EXPIRY}] Enter to keep: ").strip()
    if inp: config.OPTION_EXPIRY = inp
    state  = AlgoState(sr_levels=sr, index_key=config.ACTIVE_INDEX, mode="BACKTEST")
    engine = AlgoEngine(bt, state)

    bt.current_spot = candles[0].close
    first = input(f"\nFirst candle={candles[0].close:.2f}  [call/put/skip]: ").strip().lower()
    if "call" in first: engine.manual_buy_call(candles[0].close)
    elif "put" in first: engine.manual_buy_put(candles[0].close)

    for c in candles:
        bt.current_spot = c.close
        engine.on_candle_close(c)

    print("\n" + "="*58)
    print(f"BACKTEST  {config.ACTIVE_INDEX}  Candles={len(candles)}")
    print(f"Trades={len(bt.trades)}  Est PnL=Rs{state.daily_pnl:,.2f}")
    print("Trade Log:")
    for t in bt.trades:
        print(f"  {t['type']:<4} {t['side']:<4} k={t['k']}"
              f"  spot={t['spot']:.2f}  fill~Rs{t['fill']:.2f}"
              f"  {t.get('reason','')}")
    print("="*58)


# ═════════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["live","paper","backtest"], default=None)
    args = p.parse_args()
    mode = args.mode or _pick_mode()

    log.info("="*60)
    log.info(f"NIFTY ALGO TRADER v5  MODE={mode.upper()}")
    log.info("="*60)

    if   mode == "paper":    run_paper()
    elif mode == "live":     run_live()
    elif mode == "backtest": run_backtest()


if __name__ == "__main__":
    main()
