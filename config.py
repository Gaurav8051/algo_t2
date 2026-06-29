"""
config.py  —  Master configuration for Nifty Algo Trader v5
              (Dhan API v2.2.0 | Delivery/Positional | Multi-Index)

DAILY ROUTINE:
  1. dhan.co -> Profile -> DhanHQ Trading APIs -> Generate Access Token -> paste below
  2. Update OPTION_EXPIRY if needed  (run: python tools/find_expiry.py)
  3. Update SR_LEVELS from your chart
  4. python main.py
"""

# ── Dhan credentials ─────────────────────────────────────────────────────────
CLIENT_ID    = "your_client_id_here"
ACCESS_TOKEN = "your_access_token_here"

# ── Telegram alerts (leave blank to disable) ──────────────────────────────────
TELEGRAM_BOT_TOKEN = ""   # from @BotFather
TELEGRAM_CHAT_ID   = ""   # from @userinfobot

# ── Active index ──────────────────────────────────────────────────────────────
# Choose: "NIFTY50" | "BANKNIFTY" | "SENSEX" | "CRUDE_MCX"
ACTIVE_INDEX = "NIFTY50"

# ── Index definitions ─────────────────────────────────────────────────────────
# exchange_seg  = segment string for ohlc_data() / option_chain() REST calls
# feed_seg      = MarketFeed constant name for WebSocket subscription
# opt_seg       = exchange segment for placing option orders
# under_sec_id  = int security_id for option_chain() / expiry_list()
INDEX_CONFIG = {
    "NIFTY50": {
        "name":         "NIFTY 50",
        "security_id":  "13",
        "exchange_seg": "IDX_I",      # REST market data segment
        "feed_seg":     "IDX",        # MarketFeed attribute name
        "opt_seg":      "NSE_FNO",
        "strike_step":  50,
        "lot_size":     65,
        "market_close": "15:30",
        "under_sec_id": 13,
    },
    "BANKNIFTY": {
        "name":         "NIFTY BANK",
        "security_id":  "25",
        "exchange_seg": "IDX_I",
        "feed_seg":     "IDX",
        "opt_seg":      "NSE_FNO",
        "strike_step":  100,
        "lot_size":     15,
        "market_close": "15:30",
        "under_sec_id": 25,
    },
    "SENSEX": {
        "name":         "SENSEX",
        "security_id":  "1",
        "exchange_seg": "IDX_I",      # BSE index also uses IDX_I on Dhan
        "feed_seg":     "IDX",
        "opt_seg":      "BSE_FNO",
        "strike_step":  100,
        "lot_size":     10,
        "market_close": "15:30",
        "under_sec_id": 1,
    },
    "CRUDE_MCX": {
        "name":         "CRUDE OIL",
        "security_id":  "267640",     # MCX Crude Oil (standard contract)
        "exchange_seg": "MCX_COMM",   # REST data segment for MCX commodities
        "feed_seg":     "MCX",        # MarketFeed.MCX for websocket
        "opt_seg":      "MCX_FNO",
        "strike_step":  50,
        "lot_size":     100,
        "market_close": "23:30",
        "under_sec_id": 267640,
        # expiry_list may need alternate segment / contract id on Dhan
        "expiry_segments": ["MCX_COMM", "MCX_FNO"],
        "expiry_sec_ids":  [267640, 481575],
    },
}

# ── Option expiry ─────────────────────────────────────────────────────────────
OPTION_EXPIRY = "2026-06-30"   # run: python tools/find_expiry.py

# ── Strike selection ──────────────────────────────────────────────────────────
OTM_SKIP = 1   # skip 1st OTM, buy 2nd OTM

# ── Product type ──────────────────────────────────────────────────────────────
# "DELIVERY" = hold overnight (positional F&O — uses dhan.MARGIN)
# "INTRA"    = auto-square at 3:20 PM (uses dhan.INTRA)
PRODUCT_TYPE = "DELIVERY"

# ── Order sizing ──────────────────────────────────────────────────────────────
NUM_LOTS = 1

# ── S/R levels ────────────────────────────────────────────────────────────────
# Format: [level, tolerance_or_None]
# None  = use global TOLERANCE below
SR_LEVELS: list[list] = [
    [23810, None],
    [23846, None],
    [23890, None],
    [23945, None],
    [24010, None],
    [24045, None],
    [24066, 7],
    [24084, None],
    [24115, None],
    [24135, 7],
    [24165, None],
    [24226, None],
    [24248, 7],
    [24283, None],
    [24330, None],
]

# ── Tolerance ─────────────────────────────────────────────────────────────────
TOLERANCE         = 11    # global default (index points)
ENTRY_FILTER_MULT = 1.2
NEAR_SR_MULT      = 1.5

# ── Day P&L limits ────────────────────────────────────────────────────────────
DAILY_PROFIT_TARGET =  6000.0
DAILY_LOSS_LIMIT    = -3000.0

# PnL limit mode: "none" | "profit" | "loss" | "both"
#   none   — no auto force-exit on PnL (default until you set targets)
#   profit — exit when daily_pnl >= DAILY_PROFIT_TARGET
#   loss   — exit when daily_pnl <= DAILY_LOSS_LIMIT
#   both   — either target triggers force-exit
PNL_LIMIT_MODE = "none"

# Whether the daily P&L limit force-exits a position, per run mode.
PNL_LIMIT_ENABLED_FOR_MODE = {
    "LIVE":     True,
    "PAPER":    True,
    "BACKTEST": False,
}

# ── Remote control (local dashboard → cloud server) ───────────────────────────
CONTROL_PORT   = 8765          # TCP port on server (open in Oracle security list)
CONTROL_BIND   = "0.0.0.0"     # listen on all interfaces
CONTROL_TOKEN  = ""            # optional shared secret; blank = no auth
LOCK_FILE      = "data/algo.lock"
ORDER_LOG_FILE = "data/order_log.json"

# ── Candle interval ───────────────────────────────────────────────────────────
CANDLE_INTERVAL_SEC = 60

# ── File paths ────────────────────────────────────────────────────────────────
LOG_FILE         = "logs/algo.log"
SNAPSHOT_FILE    = "data/snapshot.json"
CMD_FILE         = "data/command.json"
SESSION_FILE     = "data/session.json"
PAPER_TRADE_FILE        = "data/paper_trades.json"
PAPER_LAST_SNAPSHOT_FILE = "data/paper_last_snapshot.json"

# ── Server-only secrets (optional) ───────────────────────────────────────────
# Create config_local.py on the server with CLIENT_ID / ACCESS_TOKEN so
# routine code deploys (scp core/*.py) never wipe your Dhan credentials.
try:
    import config_local as _local
    for _k in ("CLIENT_ID", "ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN",
               "TELEGRAM_CHAT_ID", "CONTROL_TOKEN"):
        if hasattr(_local, _k):
            globals()[_k] = getattr(_local, _k)
except ImportError:
    pass
