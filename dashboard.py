"""
dashboard.py — Remote control dashboard for the Algo Trader.

HOW IT WORKS:
  - main.py (running on server or locally) writes  data/snapshot.json  every 10s
  - dashboard.py reads that file for display
  - Commands typed here are written to  data/command.json
  - main.py reads and executes commands within 1 second

ON WINDOWS (local desktop):
  main.py opens this dashboard automatically in a second PowerShell window.
  If it didn't open, run manually:
      cd C:\\home\\algo_v5
      python dashboard.py

ON ORACLE CLOUD SERVER (remote):
  Open a SECOND SSH terminal to your server:
      ssh ubuntu@<your-server-ip>
      cd ~/algo_v5
      python dashboard.py
  You can close and reopen this dashboard anytime.
  The algo keeps running whether dashboard is open or not.

COMMANDS:
  status              live positions, PnL, FSM state
  buy call            manual CALL entry at current spot
  buy put             manual PUT entry at current spot
  sell all            force-close all open positions
  set pnl 5000        override daily PnL to Rs5000
  add sr 23978        add S/R level (global tolerance)
  add sr 23978 8      add S/R level with tolerance=8
  del sr 23978        delete S/R level
  set tol 23956 9     set per-level tolerance for 23956
  set expiry 2026-07-03   change option expiry live
  set profit 8000     change profit target
  set loss -4000      change loss limit
  clear session       delete saved session (start fresh next time)
  help                show this list
  quit                exit dashboard (algo keeps running)
"""

import json
import os
import sys
import time

# Paths match what main.py writes
BASE      = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT  = os.path.join(BASE, "data", "snapshot.json")
CMD_FILE  = os.path.join(BASE, "data", "command.json")
SESSION   = os.path.join(BASE, "data", "session.json")


# ─── Command sender ───────────────────────────────────────────────────────────

def send(cmd: dict):
    try:
        with open(CMD_FILE, "w", encoding="utf-8") as f:
            json.dump(cmd, f)
        print(f"  >> Sent: {cmd}")
    except Exception as e:
        print(f"  !! Could not write command: {e}")


# ─── Status display ───────────────────────────────────────────────────────────

def show_status():
    if not os.path.exists(SNAPSHOT):
        print("\n  [No snapshot found]")
        print("  Is main.py running?")
        if os.name == "nt":
            print("  Run in Terminal 1:  python main.py")
        else:
            print("  Run on server:  python main.py --mode paper  (or live)")
        return

    try:
        with open(SNAPSHOT, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        print("  [Snapshot unreadable — will retry]")
        return

    ago = time.time() - s.get("timestamp", time.time())
    fresh = ago < 15

    print()
    print("  " + "=" * 56)
    print(f"  {'MODE':<14}: {s.get('mode','?').upper()}  |  "
          f"INDEX: {s.get('index','?')}  |  "
          f"EXPIRY: {s.get('expiry','?')}")
    print(f"  {'FSM STATE':<14}: {s.get('fsm','?')}")
    print(f"  {'PRODUCT':<14}: {s.get('product_type','?')}")
    print(f"  {'LAST CANDLE':<14}: {s.get('last_close','?')}  "
          f"(closes every 1 min)")
    print(f"  {'LIVE TICK':<14}: {s.get('last_tick','?')}  "
          f"(updates every 10s)")
    print(f"  {'REALISED PnL':<14}: Rs {s.get('daily_pnl',0):>10,.2f}")
    print(f"  {'UNREALISED':<14}: Rs {s.get('unrealised_pnl',0):>10,.2f}  "
          f"(mark-to-market)")
    print(f"  {'TOTAL PnL':<14}: Rs {s.get('total_pnl',0):>10,.2f}")
    sr = [str(l[0]) for l in s.get("sr_levels", [])]
    print(f"  {'S/R LEVELS':<14}: {', '.join(sr)}")

    for side in ["call", "put"]:
        p = s.get(f"{side}_pos")
        if p:
            sl = (f"ACTIVE @ {p['sl_level']:.1f}"
                  if p["sl_active"] else "WAITING (S/R not broken yet)")
            print()
            print(f"  {side.upper()} POSITION:")
            print(f"    Strike     : {p['strike']}")
            print(f"    Entry Prem : Rs {p['entry_price']:.2f}")
            print(f"    Entry Spot : {p['entry_spot']:.2f}")
            print(f"    SL Status  : {sl}")
            print(f"    Resistance : {p['own_resistance']}")
            print(f"    Support    : {p['own_support']}")
            if p.get("candle_filter_active"):
                print(f"    *** CANDLE FILTER ACTIVE: {p['filter_type']} "
                      f"— waiting 1 more candle ***")

    status_line = ("LIVE (fresh)" if fresh
                   else f"STALE — {ago:.0f}s old "
                        f"{'(main.py not running?)' if ago > 60 else ''}")
    print()
    print(f"  Snapshot : {status_line}")
    print("  " + "=" * 56)
    print()


# ─── Help text ────────────────────────────────────────────────────────────────

HELP = """
  COMMANDS:
  status                show live positions and PnL
  buy call              manual CALL entry
  buy put               manual PUT entry
  sell all              force-close all positions (asks confirm)
  set pnl 5000          override daily PnL value
  add sr 23978          add S/R level (global tolerance)
  add sr 23978 8        add S/R level with tolerance=8
  del sr 23978          delete S/R level
  set tol 23956 9       set per-level tolerance for 23956
  set expiry 2026-07-03  change option expiry live
  set profit 8000       change daily profit target
  set loss -4000        change daily loss limit
  clear session         delete saved session file
  q-paper               quit paper mode (close virtual positions, save snapshot)
  help / ?              show this list
  quit / exit           close dashboard (algo keeps running)
"""


# ─── Main loop ────────────────────────────────────────────────────────────────

def run():
    print()
    print("  +====================================================+")
    print("  |   NIFTY ALGO TRADER  —  DASHBOARD  (v5)           |")
    print("  |   Dhan API v2.2.0  |  Delivery/Positional Mode    |")
    print("  +====================================================+")
    print("  Type 'help' for commands.  Type 'status' to see state.")
    print("  Closing this window does NOT stop the algo.\n")

    while True:
        try:
            raw = input("algo> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Dashboard closed. Algo keeps running.")
            break

        if not raw:
            continue

        p   = raw.lower().split()
        cmd = " ".join(p[:2]) if len(p) >= 2 else p[0] if p else ""

        # ── Display ──────────────────────────────────────────────────────────
        if raw.lower() in ("help", "?", "h"):
            print(HELP)

        elif raw.lower() == "status":
            show_status()

        # ── Trading commands ─────────────────────────────────────────────────
        elif cmd == "buy call":
            send({"action": "buy_call"})

        elif cmd == "buy put":
            send({"action": "buy_put"})

        elif cmd == "sell all":
            confirm = input("  Confirm FORCE SELL ALL positions? (yes/no): ").strip().lower()
            if confirm == "yes":
                send({"action": "force_sell"})
            else:
                print("  Cancelled.")

        # ── P&L ──────────────────────────────────────────────────────────────
        elif cmd == "set pnl":
            try:
                send({"action": "set_pnl", "value": float(p[2])})
            except (IndexError, ValueError):
                print("  Usage: set pnl 5000")

        # ── S/R management ───────────────────────────────────────────────────
        elif cmd == "add sr":
            try:
                level = float(p[2])
                tol   = float(p[3]) if len(p) > 3 else None
                send({"action": "add_sr", "level": level, "tolerance": tol})
            except (IndexError, ValueError):
                print("  Usage: add sr 23978   OR   add sr 23978 8")

        elif cmd == "del sr":
            try:
                send({"action": "del_sr", "level": float(p[2])})
            except (IndexError, ValueError):
                print("  Usage: del sr 23978")

        elif cmd == "set tol":
            try:
                send({"action": "set_tol", "level": float(p[2]),
                      "tolerance": float(p[3])})
            except (IndexError, ValueError):
                print("  Usage: set tol 23956 9")

        # ── Config changes ────────────────────────────────────────────────────
        elif cmd == "set expiry":
            try:
                send({"action": "set_expiry", "value": p[2]})
            except IndexError:
                print("  Usage: set expiry 2026-07-03")

        elif cmd == "set profit":
            try:
                send({"action": "set_profit", "value": float(p[2])})
            except (IndexError, ValueError):
                print("  Usage: set profit 8000")

        elif cmd == "set loss":
            try:
                send({"action": "set_loss", "value": float(p[2])})
            except (IndexError, ValueError):
                print("  Usage: set loss -4000")

        # ── Session ───────────────────────────────────────────────────────────
        elif raw.lower() == "clear session":
            confirm = input("  Delete saved session file? (yes/no): ").strip().lower()
            if confirm == "yes":
                if os.path.exists(SESSION):
                    os.remove(SESSION)
                    print("  Session cleared. Next startup will start fresh.")
                else:
                    print("  No session file found.")

        elif raw.lower() in ("q-paper", "qpaper"):
            if not os.path.exists(SNAPSHOT):
                print("  No snapshot — is main.py running in paper mode?")
            else:
                try:
                    with open(SNAPSHOT, encoding="utf-8") as f:
                        mode = json.load(f).get("mode", "").lower()
                except Exception:
                    mode = ""
                if mode != "paper":
                    print(f"  Q-paper only works when algo is in PAPER mode (current: {mode or '?'})")
                else:
                    confirm = input("  Quit paper trading and close all virtual positions? (yes/no): ").strip().lower()
                    if confirm == "yes":
                        send({"action": "q_paper"})
                        print("  Q-paper sent. Paper session will stop within ~1 second.")
                    else:
                        print("  Cancelled.")

        # ── Exit ──────────────────────────────────────────────────────────────
        elif raw.lower() in ("quit", "exit", "q"):
            print("  Dashboard closed. Algo keeps running on server.")
            break

        else:
            print(f"  Unknown command: '{raw}'  (type 'help')")


if __name__ == "__main__":
    run()
