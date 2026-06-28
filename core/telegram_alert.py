"""
core/telegram_alert.py — Send trade alerts via Telegram Bot.

Setup:
  1. Open Telegram, message @BotFather -> /newbot -> copy token
  2. Message @userinfobot -> copy your chat_id
  3. Paste both into config.py:
       TELEGRAM_BOT_TOKEN = "7123456789:AAExxx..."
       TELEGRAM_CHAT_ID   = "123456789"

If either is blank, all alert calls are silently ignored.
"""

from __future__ import annotations
import logging
import threading
import urllib.request
import urllib.parse
import json

import config

log = logging.getLogger("algo.telegram")


def _send(text: str):
    """Fire-and-forget HTTP POST to Telegram. Runs in background thread."""
    token   = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return   # Telegram not configured

    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "HTML",
    }).encode()

    def _post():
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    log.warning(f"Telegram HTTP {resp.status}")
        except Exception as e:
            log.warning(f"Telegram send failed: {e}")

    threading.Thread(target=_post, daemon=True).start()


def alert_buy(side: str, strike: float, fill: float, spot: float,
              index: str, qty: int, reason: str = "", mode: str = "LIVE"):
    emoji = "🟢" if side == "CALL" else "🔴"
    _send(
        f"{emoji} <b>{mode} BUY {side}</b>\n"
        f"Index  : {index}\n"
        f"Strike : {strike}\n"
        f"Fill   : Rs {fill:.2f}\n"
        f"Spot   : {spot:.2f}\n"
        f"Qty    : {qty}\n"
        f"Reason : {reason or 'MANUAL'}"
    )


def alert_sell(side: str, strike: float, fill: float, spot: float,
               pnl: float, index: str, qty: int, reason: str = "", mode: str = "LIVE"):
    emoji = "🔴" if pnl < 0 else "🟢"
    _send(
        f"{emoji} <b>{mode} SELL {side}</b>\n"
        f"Index  : {index}\n"
        f"Strike : {strike}\n"
        f"Fill   : Rs {fill:.2f}\n"
        f"Spot   : {spot:.2f}\n"
        f"PnL    : Rs {pnl:,.2f}\n"
        f"Reason : {reason}"
    )


def alert_sl_activated(side: str, sl_level: float, spot: float, index: str):
    _send(
        f"⚠️ <b>SL ACTIVATED — {side}</b>\n"
        f"Index    : {index}\n"
        f"SL Level : {sl_level:.2f}\n"
        f"Spot     : {spot:.2f}"
    )


def alert_pnl_limit(pnl: float, limit_type: str):
    emoji = "🏆" if limit_type == "PROFIT" else "🛑"
    _send(
        f"{emoji} <b>DAY P&L LIMIT HIT</b>\n"
        f"Type : {limit_type}\n"
        f"PnL  : Rs {pnl:,.2f}"
    )


def alert_session_start(index: str, mode: str, expiry: str):
    _send(
        f"🚀 <b>ALGO STARTED</b>\n"
        f"Index  : {index}\n"
        f"Mode   : {mode}\n"
        f"Expiry : {expiry}"
    )


def alert_new_session_day(index: str, session_date, carried_unrealized: float):
    """
    Sent when the engine detects a new trading day's 09:15 market-open
    boundary while already running (NOT the same as alert_session_start,
    which fires when the program itself launches). Lets the user know the
    daily P&L limit has reset/reseeded and, if non-zero, that today's limit
    is starting from yesterday's carried-over open-position exposure.
    """
    note = (f"Carried unrealized: Rs {carried_unrealized:,.2f}"
            if carried_unrealized != 0 else "No overnight position carried.")
    _send(
        f"🔔 <b>NEW TRADING DAY</b>\n"
        f"Index : {index}\n"
        f"Date  : {session_date}\n"
        f"{note}"
    )


def alert_session_end(pnl: float, trades: int):
    emoji = "✅" if pnl >= 0 else "❌"
    _send(
        f"{emoji} <b>SESSION ENDED</b>\n"
        f"Realised PnL : Rs {pnl:,.2f}\n"
        f"Total Trades : {trades}"
    )


def alert_candle_filter(filter_type: str, side: str, spot: float):
    _send(
        f"🕯️ <b>CANDLE FILTER — {filter_type}</b>\n"
        f"Side  : {side}\n"
        f"Spot  : {spot:.2f}\n"
        f"Action: Waiting 1 more candle for confirmation"
    )


def test_alert():
    """Run this to verify Telegram is configured correctly."""
    _send("✅ Nifty Algo Trader — Telegram connected successfully!")
    print("Test alert sent. Check your Telegram.")
