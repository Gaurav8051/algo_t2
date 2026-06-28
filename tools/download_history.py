"""
tools/download_history.py — Download 1-min index OHLC for backtesting.

Usage:
    python tools/download_history.py 2025-06-01 2025-06-20
    python tools/download_history.py 2025-06-01 2025-06-20 BANKNIFTY

Saves to: data/nifty_1min.csv  (or banknifty_1min.csv etc.)
"""
import sys, os, csv, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if len(sys.argv) < 3:
    print("Usage: python tools/download_history.py FROM_DATE TO_DATE [INDEX]")
    print("Example: python tools/download_history.py 2025-06-01 2025-06-20")
    sys.exit(1)

from_date, to_date = sys.argv[1], sys.argv[2]

import config
if len(sys.argv) > 3:
    config.ACTIVE_INDEX = sys.argv[3].upper()

from dhanhq import DhanContext
from core.dhan_api import make_dhan, download_minute_data

os.makedirs("data", exist_ok=True)
ctx  = DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)
dhan = make_dhan(ctx)

print(f"Downloading {config.ACTIVE_INDEX} 1-min data: {from_date} -> {to_date} ...")
resp = download_minute_data(dhan, from_date, to_date)

if resp.get("status") != "success":
    print(f"Error: {resp}"); sys.exit(1)

d      = resp["data"]
opens  = d.get("open",      [])
highs  = d.get("high",      [])
lows   = d.get("low",       [])
closes = d.get("close",     [])
vols   = d.get("volume",    [None]*len(opens))
ts     = d.get("timestamp", [])

name   = config.ACTIVE_INDEX.lower()
outf   = os.path.join("data", f"{name}_1min.csv")

with open(outf, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["datetime","open","high","low","close","volume"])
    for i in range(len(closes)):
        try:
            dt = datetime.datetime.fromtimestamp(int(ts[i])).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            dt = str(ts[i]) if ts else ""
        w.writerow([dt, opens[i], highs[i], lows[i], closes[i], vols[i]])

print(f"Saved {len(closes)} candles -> {outf}")
