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

def _launch_dashboard_window(remote_host: str = ""):
    """Open local GUI dashboard (Windows). Falls back to CLI dashboard."""
    if platform.system() != "Windows":
        log.info("Server mode: run 'python dashboard_gui.py --remote <server-ip>' locally.")
        return
    py   = sys.executable
    base = os.path.dirname(os.path.abspath(__file__))
    gui  = os.path.join(base, "dashboard_gui.py")
    cli  = os.path.join(base, "dashboard.py")
    target = gui if os.path.exists(gui) else cli
    extra = f" --remote {remote_host}" if remote_host else ""
    cmd  = f'start powershell -NoExit -Command "& \'{py}\' \'{target}\'{extra}"'
    try:
        subprocess.Popen(cmd, shell=True)
        log.info(f"Dashboard opened: {os.path.basename(target)}")
    except Exception as e:
        log.warning(f"Could not auto-open dashboard: {e}")
        log.info("Manually run: python dashboard_gui.py")


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
                   last_tick: float = 0.0, unrealised: float = 0.0,
                   order_history: list | None = None):
    from core import order_log as olog
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
        "call_disabled":  state.call_disabled,
        "put_disabled":   state.put_disabled,
        "global_tolerance": config.TOLERANCE,
        "num_lots":       config.NUM_LOTS,
        "pnl_limit_mode": config.PNL_LIMIT_MODE,
        "profit_target":  config.DAILY_PROFIT_TARGET,
        "loss_limit":     config.DAILY_LOSS_LIMIT,
        "order_history":  order_history if order_history is not None else olog.recent(30),
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
_RUNTIME: dict = {}   # engine, state, client, mode — set when loop starts


def _request_shutdown(reason: str):
    _SHUTDOWN["requested"] = True
    _SHUTDOWN["reason"]    = reason


def process_commands(engine, state: AlgoState, client, mode: str):
    from core.command_handler import execute_command
    if not os.path.exists(config.CMD_FILE):
        return
    try:
        with open(config.CMD_FILE, encoding="utf-8") as f:
            cmd = json.load(f)
        os.remove(config.CMD_FILE)
    except Exception as e:
        log.error(f"Command read error: {e}")
        return
    result = execute_command(engine, state, client, mode, cmd,
                             shutdown_cb=_request_shutdown)
    if result.get("error"):
        log.warning(f"Command rejected: {result['error']}")
    else:
        log.info(f"Command OK: {result.get('message', cmd.get('action'))}")


def _build_snapshot_dict(state: AlgoState, mode: str, client, paper_client=None) -> dict:
    tick   = getattr(client, "current_spot", 0.0)
    unreal = _unrealised_pnl(state, client, paper_client)
    write_snapshot(state, mode, tick, unreal)
    try:
        with open(config.SNAPSHOT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _run_command_sync(cmd: dict) -> dict:
    from core.command_handler import execute_command
    rt = _RUNTIME
    if not rt:
        return {"error": "algo not running"}
    return execute_command(rt["engine"], rt["state"], rt["client"], rt["mode"], cmd,
                           shutdown_cb=_request_shutdown)


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
    return {"1": "paper", "2": "live", "3": "backtest"}.get(c, "paper")


def _run_setup_wizard(client, mode: str, engine, state: AlgoState,
                      live_positions: list | None = None) -> bool:
    """Wizard + optional session restore. Returns False if user cancelled."""
    from core.setup_wizard import pick_index, run_full_wizard
    from core.session import load_session, restore_state, clear_session, save_session

    # Index must be chosen before session restore (one index at a time)
    config.ACTIVE_INDEX = pick_index()
    state.index_key = config.ACTIVE_INDEX

    session = load_session(mode)
    if session:
        print(f"\n  Previous {mode.upper()} session ({session.get('date')}) "
              f"for {session.get('index')}.")
        cp = "CALL " if session.get("call_pos") else ""
        pp = "PUT" if session.get("put_pos") else ""
        print(f"  Positions: {cp}{pp or '(none)'}")
        if input("  Restore? (yes/no): ").strip().lower() == "yes":
            if restore_state(state, session, live_positions or []):
                log.info("Session restored — skipping wizard.")
                return True
            print("  Restore failed.")
        else:
            clear_session()

    result = run_full_wizard(client, mode, index_key=config.ACTIVE_INDEX)
    if result is None:
        return False
    levels, _expiry, _lots, first_side = result
    state.sr_levels = levels
    state.index_key = config.ACTIVE_INDEX
    save_session(state, mode)
    spot = client.get_spot_ltp()
    if first_side == "call":
        engine.manual_buy_call(spot)
    else:
        engine.manual_buy_put(spot)
    save_session(state, mode)
    return True


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

    if reason == "STOP":
        log.info("STOP — algo exiting (no open positions)")
        clear_session()
        write_snapshot(state, mode, tick, 0.0)
        trades = len(paper_client.trades) if paper_client else 0
        tg.alert_session_end(state.daily_pnl, trades)
        log.info(f"Algo stopped. Realised PnL: Rs{state.daily_pnl:,.2f}")
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
    from core.process_lock import update, release
    from core.control_server import ControlServer

    _RUNTIME.update(engine=engine, state=state, client=client, mode=mode,
                    paper_client=paper_client)

    def _snap():
        return _build_snapshot_dict(state, mode, client, paper_client)

    ctrl = ControlServer(get_snapshot=_snap, run_command=_run_command_sync)
    ctrl.start()

    last_snap = 0.0
    try:
        while True:
            process_commands(engine, state, client, mode)
            update(has_positions=state.has_call() or state.has_put())
            if _SHUTDOWN["requested"]:
                _graceful_shutdown(engine, state, client, mode, paper_client,
                                   _SHUTDOWN["reason"])
                break
            now = time.time()
            if now - last_snap >= 10:
                _build_snapshot_dict(state, mode, client, paper_client)
                from core.session import save_session
                save_session(state, mode)
                last_snap = now
            time.sleep(1)
    except KeyboardInterrupt:
        log.warning(f"Ctrl+C — shutting down {mode.upper()}")
        _graceful_shutdown(engine, state, client, mode, paper_client, "CTRL_C")
    finally:
        ctrl.stop()
        release()
        _RUNTIME.clear()


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
    from core.process_lock import acquire
    import core.telegram_alert as tg

    acquire("paper")
    ctx    = DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)
    client = PaperClient(ctx)

    try:
        spot = client.get_spot_ltp()
        log.info(f"PAPER connected. Spot={spot:.2f}" if spot > 0
                 else "PAPER connected (market closed — spot=0)")
    except Exception as e:
        log.warning(f"Spot fetch failed: {e}")
        spot = 0.0

    state  = AlgoState(index_key=config.ACTIVE_INDEX, mode="PAPER")
    engine = AlgoEngine(client, state)

    if not _run_setup_wizard(client, "paper", engine, state):
        from core.process_lock import release
        release()
        sys.exit(0)

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
    from core.process_lock import acquire
    import core.dhan_api as _api
    import core.telegram_alert as tg

    acquire("live")
    ctx  = DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)
    dhan = _api.make_dhan(ctx)

    class LiveClient:
        current_spot = 0.0
        def get_spot_ltp(self): return _api.get_spot_ltp(dhan)
        def get_expiry_list(self, index_key: str | None = None):
            return _api.get_expiry_list(dhan, index_key or config.ACTIVE_INDEX)
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

    state  = AlgoState(index_key=config.ACTIVE_INDEX, mode="LIVE")
    engine = AlgoEngine(client, state)

    live_pos = _api.get_open_positions(dhan)
    if not _run_setup_wizard(client, "live", engine, state, live_positions=live_pos):
        from core.process_lock import release
        release()
        sys.exit(0)

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
    p.add_argument("--mode", choices=["live", "paper", "backtest"], default=None)
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
