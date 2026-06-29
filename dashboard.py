"""
dashboard.py — CLI remote control for the Algo Trader.

Local:  reads data/snapshot.json, writes data/command.json
Remote: python dashboard.py --remote <server-ip> [--port 8765]

GUI alternative: python dashboard_gui.py [--remote <ip>]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import config

SNAPSHOT = os.path.join(BASE, "data", "snapshot.json")
CMD_FILE = os.path.join(BASE, "data", "command.json")
SESSION  = os.path.join(BASE, "data", "session.json")


class Backend:
    def __init__(self, remote: str = "", port: int = 0, token: str = ""):
        self._remote = remote
        self._client = None
        if remote:
            from core.control_client import ControlClient
            self._client = ControlClient(remote, port or config.CONTROL_PORT, token)

    def get_snapshot(self):
        if self._client:
            return self._client.get_snapshot()
        if not os.path.exists(SNAPSHOT):
            return None
        with open(SNAPSHOT, encoding="utf-8") as f:
            return json.load(f)

    def send(self, cmd: dict) -> tuple[bool, str]:
        if self._client:
            return self._client.send_command(cmd)
        with open(CMD_FILE, "w", encoding="utf-8") as f:
            json.dump(cmd, f)
        return True, "queued"


def show_status(backend: Backend):
    s = backend.get_snapshot()
    if not s:
        print("\n  [No snapshot — is main.py running?]")
        return
    ago = time.time() - s.get("timestamp", time.time())
    fresh = ago < 15
    print()
    print("  " + "=" * 56)
    print(f"  MODE          : {s.get('mode','?').upper()}  |  INDEX: {s.get('index','?')}")
    print(f"  FSM           : {s.get('fsm','?')}  |  EXPIRY: {s.get('expiry','?')}")
    print(f"  CALL disabled : {s.get('call_disabled', False)}  |  PUT disabled: {s.get('put_disabled', False)}")
    print(f"  REALISED PnL  : Rs {s.get('daily_pnl',0):>10,.2f}")
    print(f"  UNREALISED    : Rs {s.get('unrealised_pnl',0):>10,.2f}")
    print(f"  TOTAL PnL     : Rs {s.get('total_pnl',0):>10,.2f}")
    print(f"  PnL LIMIT     : {s.get('pnl_limit_mode','none')}")
    sr = [f"{l[0]}(t={l[1]})" for l in s.get("sr_levels", [])]
    print(f"  S/R           : {', '.join(sr)}")
    for side in ("call", "put"):
        p = s.get(f"{side}_pos")
        if p:
            sl = f"ACTIVE@{p['sl_level']:.1f}" if p.get("sl_active") else "WAITING"
            print(f"  {side.upper():5} k={p['strike']} entry=Rs{p['entry_price']:.2f} SL={sl}")
    print(f"  Snapshot      : {'LIVE' if fresh else f'STALE {ago:.0f}s'}")
    print("  " + "=" * 56)


HELP = """
  TRADE
    buy call / buy put       manual entry (blocked if side disabled or duplicate)
    sell call / sell put     exit one leg
    sell all                 force-close all (confirm yes)

  DISABLE (strategy skips auto logic on disabled side)
    disable call / disable put / disable both
    enable call  / enable put  / enable all

  S/R & CONFIG
    add sr 23978 [tol]       add level (optional per-level tolerance)
    del sr 23978             delete level
    set tol 23956 9          per-level tolerance
    set global tol 11        global default tolerance
    set expiry 2026-07-03    option expiry
    set lots 2               lots per order

  PnL
    set pnl 5000             override realised daily PnL
    set profit 8000          profit target Rs
    set loss -4000           loss limit Rs
    set pnl mode none|profit|loss|both

  PAPER
    q-paper                  quit paper (close virtual positions, stop paper main.py)
  stop algo                stop main.py when flat (no open positions)

  OTHER
    status / history / help / quit
"""


def show_history(backend: Backend):
    s = backend.get_snapshot() or {}
    rows = s.get("order_history", [])
    if not rows:
        print("  No orders in log.")
        return
    print(f"\n  Last {min(30, len(rows))} orders:")
    for r in reversed(rows[-30:]):
        ts = time.strftime("%H:%M:%S", time.localtime(r.get("ts", 0)))
        print(f"  {ts}  {r.get('type','?'):4} {r.get('side','?'):4} k={r.get('strike','?')} "
              f"spot={r.get('spot','?')} pnl={r.get('pnl','')}  {r.get('reason','')}")


def run(backend: Backend):
    print("\n  NIFTY ALGO TRADER — CLI Dashboard v5")
    if backend._remote:
        print(f"  Remote: {backend._remote}:{config.CONTROL_PORT}")
    print("  Type 'help'. Closing dashboard does NOT stop the algo.\n")

    while True:
        try:
            raw = input("algo> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Dashboard closed.")
            break
        if not raw:
            continue
        p = raw.lower().split()
        cmd_key = " ".join(p[:3]) if len(p) >= 3 else " ".join(p[:2]) if len(p) >= 2 else p[0]

        if raw.lower() in ("help", "?", "h"):
            print(HELP)
        elif raw.lower() == "status":
            show_status(backend)
        elif raw.lower() == "history":
            show_history(backend)
        elif cmd_key == "buy call":
            ok, msg = backend.send({"action": "buy_call"})
            print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        elif cmd_key == "buy put":
            ok, msg = backend.send({"action": "buy_put"})
            print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        elif cmd_key == "sell call":
            ok, msg = backend.send({"action": "sell_call"})
            print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        elif cmd_key == "sell put":
            ok, msg = backend.send({"action": "sell_put"})
            print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        elif cmd_key == "sell all":
            if input("  Confirm sell ALL? (yes/no): ").strip().lower() == "yes":
                backend.send({"action": "force_sell"})
        elif cmd_key == "set pnl" and len(p) >= 3 and p[2] == "mode":
            backend.send({"action": "set_pnl_mode", "value": p[3] if len(p) > 3 else "none"})
        elif cmd_key == "set pnl":
            try:
                backend.send({"action": "set_pnl", "value": float(p[2])})
            except (IndexError, ValueError):
                print("  Usage: set pnl 5000")
        elif cmd_key == "set global tol":
            try:
                backend.send({"action": "set_global_tol", "value": float(p[3])})
            except (IndexError, ValueError):
                print("  Usage: set global tol 11")
        elif cmd_key == "set pnl mode":
            backend.send({"action": "set_pnl_mode", "value": p[3] if len(p) > 3 else "none"})
        elif cmd_key == "add sr":
            try:
                c = {"action": "add_sr", "level": float(p[2])}
                if len(p) > 3:
                    c["tolerance"] = float(p[3])
                backend.send(c)
            except (IndexError, ValueError):
                print("  Usage: add sr 23978 [tol]")
        elif cmd_key == "del sr":
            backend.send({"action": "del_sr", "level": float(p[2])})
        elif cmd_key == "set tol":
            backend.send({"action": "set_tol", "level": float(p[2]), "tolerance": float(p[3])})
        elif cmd_key == "set expiry":
            backend.send({"action": "set_expiry", "value": p[2]})
        elif cmd_key == "set profit":
            backend.send({"action": "set_profit", "value": float(p[2])})
        elif cmd_key == "set loss":
            backend.send({"action": "set_loss", "value": float(p[2])})
        elif cmd_key == "set lots":
            backend.send({"action": "set_lots", "value": int(p[2])})
        elif raw == "disable call":
            backend.send({"action": "disable_call"})
        elif raw == "disable put":
            backend.send({"action": "disable_put"})
        elif raw == "disable both":
            backend.send({"action": "disable_both"})
        elif raw == "enable call":
            backend.send({"action": "enable_call"})
        elif raw == "enable put":
            backend.send({"action": "enable_put"})
        elif raw == "enable all":
            backend.send({"action": "enable_all"})
        elif raw in ("q-paper", "qpaper"):
            snap = backend.get_snapshot() or {}
            if snap.get("mode", "").lower() != "paper":
                print("  Q-paper only in PAPER mode.")
            elif input("  Confirm Q-paper? (yes/no): ").strip().lower() == "yes":
                backend.send({"action": "q_paper"})
        elif raw in ("stop algo", "stop"):
            snap = backend.get_snapshot() or {}
            if snap.get("call_pos") or snap.get("put_pos"):
                print("  Close all positions first: sell all")
            elif input("  Stop algo process (flat)? (yes/no): ").strip().lower() == "yes":
                backend.send({"action": "stop_algo"})
        elif raw in ("quit", "exit", "q"):
            break
        else:
            print(f"  Unknown: '{raw}'  (type help)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default="", help="Cloud server IP")
    ap.add_argument("--port", type=int, default=config.CONTROL_PORT)
    ap.add_argument("--token", default=config.CONTROL_TOKEN)
    args = ap.parse_args()
    run(Backend(args.remote, args.port, args.token))


if __name__ == "__main__":
    main()
