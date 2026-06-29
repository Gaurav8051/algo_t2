"""
dashboard_gui.py — Graphical control panel for the algo trader.

Local (same machine): reads data/snapshot.json, writes data/command.json
Remote (Oracle cloud): TCP to server port 8765

Usage:
  python dashboard_gui.py
  python dashboard_gui.py --remote 129.154.xxx.xxx
  python dashboard_gui.py --remote 129.154.xxx.xxx --port 8765 --token mysecret
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import config

SNAPSHOT = os.path.join(BASE, "data", "snapshot.json")
CMD_FILE = os.path.join(BASE, "data", "command.json")


class DashboardBackend:
    """Local file IPC or remote TCP control."""

    def __init__(self, remote: str = "", port: int = 0, token: str = ""):
        self.remote = remote.strip()
        self.port = port or config.CONTROL_PORT
        self.last_error = ""
        self._client = None
        if self.remote:
            from core.control_client import ControlClient
            self._client = ControlClient(self.remote, self.port, token)

    def connection_hint(self) -> str:
        """Human-readable reason when snapshot is unavailable."""
        if self._client:
            err = getattr(self._client, "last_error", "") or self.last_error
            lines = [
                f"REMOTE mode → {self.remote}:{self.port}",
                "",
                "Cannot reach the algo on your Oracle server.",
                f"Error: {err or 'connection timed out'}",
                "",
                "Fix (pick one):",
                "  A) Open TCP port 8765 in Oracle Cloud → Security List → Ingress",
                "  B) SSH tunnel (no firewall change needed):",
                f"       ssh -i key.pem -L 8765:localhost:8765 ubuntu@{self.remote}",
                "       python dashboard_gui.py --remote 127.0.0.1",
                "",
                "Works 24/7 including pre-market — not tied to market hours.",
            ]
            return "\n".join(lines)
        if not os.path.exists(SNAPSHOT):
            return "\n".join([
                "LOCAL mode — looking for: data/snapshot.json on THIS PC",
                "",
                "main.py is running on your SERVER (92.4.86.179), not on this laptop.",
                "This PC has no snapshot file, so the dashboard shows 'No connection'.",
                "",
                "Fix — start dashboard in REMOTE mode:",
                "  python dashboard_gui.py --remote 92.4.86.179",
                "",
                "Or double-click:  start_dashboard.bat",
                "",
                "Pre-market S/R edits, disable sides, etc. work anytime via remote.",
            ])
        return "Could not read data/snapshot.json"

    def get_snapshot(self) -> dict | None:
        self.last_error = ""
        if self._client:
            snap = self._client.get_snapshot()
            if snap is None:
                self.last_error = getattr(self._client, "last_error", "")
            return snap
        if not os.path.exists(SNAPSHOT):
            return None
        try:
            with open(SNAPSHOT, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.last_error = str(e)
            return None

    def send(self, cmd: dict) -> tuple[bool, str]:
        if self._client:
            return self._client.send_command(cmd)
        try:
            os.makedirs(os.path.dirname(CMD_FILE), exist_ok=True)
            with open(CMD_FILE, "w", encoding="utf-8") as f:
                json.dump(cmd, f)
            return True, str(cmd)
        except Exception as e:
            return False, str(e)

    def ping(self) -> bool:
        if self._client:
            return self._client.ping()
        return os.path.exists(SNAPSHOT)


class AlgoDashboard(tk.Tk):
    def __init__(self, backend: DashboardBackend):
        super().__init__()
        self.backend = backend
        self.title("Nifty Algo Trader — Control Dashboard v5")
        self.geometry("920x680")
        self.minsize(780, 560)
        self._build_ui()
        self.after(500, self._refresh)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)
        self.lbl_conn = ttk.Label(top, text="Connecting...", font=("Segoe UI", 10, "bold"))
        self.lbl_conn.pack(side=tk.LEFT)
        ttk.Button(top, text="Refresh", command=self._refresh).pack(side=tk.RIGHT, padx=4)
        ttk.Button(top, text="Quit Dashboard", command=self._on_close).pack(side=tk.RIGHT)

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # ── Status tab ──
        tab_status = ttk.Frame(nb, padding=8)
        nb.add(tab_status, text="Status")
        self.txt_status = scrolledtext.ScrolledText(tab_status, height=22, font=("Consolas", 10))
        self.txt_status.pack(fill=tk.BOTH, expand=True)

        # ── Trade tab ──
        tab_trade = ttk.Frame(nb, padding=8)
        nb.add(tab_trade, text="Trade Control")
        bf = ttk.LabelFrame(tab_trade, text="Force Entry (duplicate blocked)", padding=8)
        bf.pack(fill=tk.X, pady=4)
        ttk.Button(bf, text="Buy CALL", command=lambda: self._cmd({"action": "buy_call"})).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Buy PUT", command=lambda: self._cmd({"action": "buy_put"})).pack(side=tk.LEFT, padx=4)

        sf = ttk.LabelFrame(tab_trade, text="Force Exit", padding=8)
        sf.pack(fill=tk.X, pady=4)
        ttk.Button(sf, text="Sell CALL", command=lambda: self._cmd({"action": "sell_call"})).pack(side=tk.LEFT, padx=4)
        ttk.Button(sf, text="Sell PUT", command=lambda: self._cmd({"action": "sell_put"})).pack(side=tk.LEFT, padx=4)
        ttk.Button(sf, text="Sell ALL", command=self._sell_all).pack(side=tk.LEFT, padx=4)

        df = ttk.LabelFrame(tab_trade, text="Disable / Enable Strategy Side", padding=8)
        df.pack(fill=tk.X, pady=4)
        for label, action in [
            ("Disable CALL", "disable_call"), ("Disable PUT", "disable_put"),
            ("Disable Both", "disable_both"),
            ("Enable CALL", "enable_call"), ("Enable PUT", "enable_put"),
            ("Enable All", "enable_all"),
        ]:
            ttk.Button(df, text=label, command=lambda a=action: self._cmd({"action": a})).pack(side=tk.LEFT, padx=2, pady=2)

        pf = ttk.LabelFrame(tab_trade, text="Paper Mode", padding=8)
        pf.pack(fill=tk.X, pady=4)
        ttk.Button(pf, text="Q-Paper (close virtual & stop paper)", command=self._qpaper).pack(side=tk.LEFT)

        # ── Config tab ──
        tab_cfg = ttk.Frame(nb, padding=8)
        nb.add(tab_cfg, text="S/R & Config")
        cfg_row = ttk.Frame(tab_cfg)
        cfg_row.pack(fill=tk.X, pady=4)
        ttk.Button(cfg_row, text="Add S/R", command=self._add_sr).pack(side=tk.LEFT, padx=4)
        ttk.Button(cfg_row, text="Delete S/R", command=self._del_sr).pack(side=tk.LEFT, padx=4)
        ttk.Button(cfg_row, text="Set S/R Tol", command=self._set_sr_tol).pack(side=tk.LEFT, padx=4)
        ttk.Button(cfg_row, text="Global Tol", command=self._set_global_tol).pack(side=tk.LEFT, padx=4)
        ttk.Button(cfg_row, text="Set Expiry", command=self._set_expiry).pack(side=tk.LEFT, padx=4)
        ttk.Button(cfg_row, text="Set Lots", command=self._set_lots).pack(side=tk.LEFT, padx=4)

        pnl_row = ttk.Frame(tab_cfg)
        pnl_row.pack(fill=tk.X, pady=8)
        ttk.Label(pnl_row, text="PnL limits:").pack(side=tk.LEFT)
        for label, mode in [("None", "none"), ("Profit", "profit"), ("Loss", "loss"), ("Both", "both")]:
            ttk.Button(pnl_row, text=label,
                       command=lambda m=mode: self._cmd({"action": "set_pnl_mode", "value": m})).pack(side=tk.LEFT, padx=3)
        ttk.Button(pnl_row, text="Set Profit Target", command=self._set_profit).pack(side=tk.LEFT, padx=8)
        ttk.Button(pnl_row, text="Set Loss Limit", command=self._set_loss).pack(side=tk.LEFT, padx=4)
        ttk.Button(pnl_row, text="Override PnL", command=self._set_pnl).pack(side=tk.LEFT, padx=4)

        # ── History tab ──
        tab_hist = ttk.Frame(nb, padding=8)
        nb.add(tab_hist, text="Order History")
        self.txt_hist = scrolledtext.ScrolledText(tab_hist, font=("Consolas", 9))
        self.txt_hist.pack(fill=tk.BOTH, expand=True)

        self.lbl_footer = ttk.Label(self, text="Dashboard only — closing this window does NOT stop the algo.",
                                    font=("Segoe UI", 9))
        self.lbl_footer.pack(fill=tk.X, padx=8, pady=4)

    def _cmd(self, cmd: dict):
        ok, msg = self.backend.send(cmd)
        if ok:
            self.lbl_conn.config(text=f"Sent: {cmd.get('action', cmd)}")
        else:
            messagebox.showerror("Command failed", msg)

    def _sell_all(self):
        if messagebox.askyesno("Confirm", "Force sell ALL open positions?"):
            self._cmd({"action": "force_sell"})

    def _qpaper(self):
        snap = self.backend.get_snapshot() or {}
        if snap.get("mode", "").lower() != "paper":
            messagebox.showwarning("Q-Paper", "Only available in PAPER mode.")
            return
        if messagebox.askyesno("Q-Paper", "Close all virtual positions and stop paper session?"):
            self._cmd({"action": "q_paper"})

    def _add_sr(self):
        lv = simpledialog.askfloat("Add S/R", "Level:")
        if lv is None:
            return
        tol = simpledialog.askfloat("Tolerance", "Per-level tolerance (Cancel=global):", initialvalue=config.TOLERANCE)
        cmd = {"action": "add_sr", "level": lv}
        if tol is not None:
            cmd["tolerance"] = tol
        self._cmd(cmd)

    def _del_sr(self):
        lv = simpledialog.askfloat("Delete S/R", "Level to remove:")
        if lv is not None:
            self._cmd({"action": "del_sr", "level": lv})

    def _set_sr_tol(self):
        lv = simpledialog.askfloat("S/R", "Level:")
        tol = simpledialog.askfloat("Tolerance", "New tolerance:")
        if lv is not None and tol is not None:
            self._cmd({"action": "set_tol", "level": lv, "tolerance": tol})

    def _set_global_tol(self):
        v = simpledialog.askfloat("Global tolerance", "Points:", initialvalue=config.TOLERANCE)
        if v is not None:
            self._cmd({"action": "set_global_tol", "value": v})

    def _set_expiry(self):
        v = simpledialog.askstring("Expiry", "YYYY-MM-DD:")
        if v:
            self._cmd({"action": "set_expiry", "value": v})

    def _set_lots(self):
        v = simpledialog.askinteger("Lots", "Lots per order:", initialvalue=config.NUM_LOTS, minvalue=1)
        if v:
            self._cmd({"action": "set_lots", "value": v})

    def _set_profit(self):
        v = simpledialog.askfloat("Profit target", "Rs:", initialvalue=config.DAILY_PROFIT_TARGET)
        if v is not None:
            self._cmd({"action": "set_profit", "value": v})

    def _set_loss(self):
        v = simpledialog.askfloat("Loss limit", "Rs (negative):", initialvalue=config.DAILY_LOSS_LIMIT)
        if v is not None:
            self._cmd({"action": "set_loss", "value": v})

    def _set_pnl(self):
        v = simpledialog.askfloat("Override PnL", "Realised daily PnL Rs:")
        if v is not None:
            self._cmd({"action": "set_pnl", "value": v})

    def _format_status(self, s: dict) -> str:
        ago = time.time() - s.get("timestamp", 0)
        fresh = "LIVE" if ago < 15 else f"STALE ({ago:.0f}s)"
        lines = [
            f"{'='*56}",
            f"  MODE          : {s.get('mode','?').upper()}   [{fresh}]",
            f"  INDEX         : {s.get('index','?')}",
            f"  EXPIRY        : {s.get('expiry','?')}",
            f"  FSM           : {s.get('fsm','?')}",
            f"  PRODUCT       : {s.get('product_type','?')}",
            f"  LOTS          : {s.get('num_lots','?')}",
            f"  GLOBAL TOL    : {s.get('global_tolerance','?')}",
            f"  CALL DISABLED : {s.get('call_disabled', False)}",
            f"  PUT DISABLED  : {s.get('put_disabled', False)}",
            f"  LAST CANDLE   : {s.get('last_close','?')}",
            f"  LIVE TICK     : {s.get('last_tick','?')}",
            f"  REALISED PnL  : Rs {s.get('daily_pnl',0):,.2f}",
            f"  UNREALISED    : Rs {s.get('unrealised_pnl',0):,.2f}",
            f"  TOTAL PnL     : Rs {s.get('total_pnl',0):,.2f}",
            f"  PnL LIMIT     : {s.get('pnl_limit_mode','none')}  "
            f"(profit={s.get('profit_target')}, loss={s.get('loss_limit')})",
            f"  S/R LEVELS    :",
        ]
        for lv, tol in s.get("sr_levels", []):
            lines.append(f"    {lv}  tol={tol}")
        for side in ("call", "put"):
            p = s.get(f"{side}_pos")
            if p:
                sl = f"ACTIVE @ {p['sl_level']:.1f}" if p.get("sl_active") else "WAITING"
                lines += ["", f"  {side.upper()} POSITION:",
                          f"    Strike    : {p['strike']}",
                          f"    Entry     : Rs {p['entry_price']:.2f} @ spot {p['entry_spot']:.2f}",
                          f"    SL        : {sl}",
                          f"    R / S     : {p.get('own_resistance')} / {p.get('own_support')}"]
        lines.append(f"{'='*56}")
        return "\n".join(lines)

    def _format_history(self, s: dict) -> str:
        rows = s.get("order_history", [])
        if not rows:
            return "No orders logged yet."
        lines = [f"{'TIME':<12} {'TYPE':<5} {'SIDE':<5} {'STRIKE':<8} {'SPOT':<10} {'PnL':<10} REASON"]
        for r in reversed(rows[-40:]):
            ts = time.strftime("%H:%M:%S", time.localtime(r.get("ts", 0)))
            lines.append(
                f"{ts:<12} {r.get('type','?'):<5} {r.get('side','?'):<5} "
                f"{r.get('strike','?'):<8} {r.get('spot','?'):<10} "
                f"{r.get('pnl',''):<10} {r.get('reason','')}"
            )
        return "\n".join(lines)

    def _refresh(self):
        def work():
            snap = self.backend.get_snapshot()
            self.after(0, lambda: self._apply_snap(snap))
        threading.Thread(target=work, daemon=True).start()
        self.after(5000, self._refresh)

    def _apply_snap(self, snap: dict | None):
        if snap is None:
            mode = f"REMOTE → {self.backend.remote}" if self.backend.remote else "LOCAL (this PC)"
            self.lbl_conn.config(
                text=f"Not connected [{mode}] — see Status tab for fix")
            self.txt_status.delete("1.0", tk.END)
            self.txt_status.insert(tk.END, self.backend.connection_hint())
            return
        mode = snap.get("mode", "?").upper()
        self.lbl_conn.config(text=f"Connected | {mode} | {snap.get('index','?')} | FSM={snap.get('fsm','?')}")
        self.txt_status.delete("1.0", tk.END)
        self.txt_status.insert(tk.END, self._format_status(snap))
        self.txt_hist.delete("1.0", tk.END)
        self.txt_hist.insert(tk.END, self._format_history(snap))

    def _on_close(self):
        if messagebox.askokcancel("Close Dashboard", "Close dashboard? (Algo keeps running on server)"):
            self.destroy()


def main():
    ap = argparse.ArgumentParser(description="Algo Trader GUI Dashboard")
    ap.add_argument("--remote", default=os.environ.get("ALGO_REMOTE", ""),
                    help="Server IP (required when main.py runs on Oracle cloud)")
    ap.add_argument("--port", type=int, default=config.CONTROL_PORT)
    ap.add_argument("--token", default=config.CONTROL_TOKEN)
    args = ap.parse_args()

    remote = args.remote.strip()
    if not remote and not os.path.exists(SNAPSHOT):
        print("\n  main.py is not running on THIS computer.")
        print("  Enter your Oracle server IP (e.g. 92.4.86.179)")
        print("  Or press Enter to open in local-only mode (needs data/snapshot.json).\n")
        try:
            inp = input("  Server IP: ").strip()
            if inp:
                remote = inp
        except EOFError:
            pass

    backend = DashboardBackend(remote, args.port, args.token)
    if remote:
        print(f"  Dashboard → REMOTE {remote}:{args.port}")
        if not backend.ping():
            print(f"  Warning: cannot ping server yet ({getattr(backend._client, 'last_error', '?')})")
            print("  Open port 8765 on Oracle Cloud OR use SSH tunnel (see deploy/SERVER_OPS.md)")
    else:
        print("  Dashboard → LOCAL (data/snapshot.json on this PC)")

    app = AlgoDashboard(backend)
    app.mainloop()


if __name__ == "__main__":
    main()
