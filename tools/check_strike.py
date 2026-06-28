"""
tools/check_strike.py — Verify security_id for a strike.

Usage:
    python tools/check_strike.py 24000 CE 2026-06-30
    python tools/check_strike.py 23900 PE 2026-06-30 BANKNIFTY
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if len(sys.argv) < 4:
    print("Usage: python tools/check_strike.py <strike> <CE|PE> <YYYY-MM-DD> [INDEX]")
    sys.exit(1)

strike = float(sys.argv[1])
opt    = sys.argv[2].upper()
expiry = sys.argv[3]

import config
if len(sys.argv) > 4:
    config.ACTIVE_INDEX = sys.argv[4].upper()

from dhanhq import DhanContext
from core.dhan_api import make_dhan, get_option_security_id

ctx  = DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)
dhan = make_dhan(ctx)
try:
    sid = get_option_security_id(dhan, strike, opt, expiry)
    print(f"\n  Index       : {config.ACTIVE_INDEX}")
    print(f"  Strike      : {strike}")
    print(f"  Option Type : {opt}")
    print(f"  Expiry      : {expiry}")
    print(f"  security_id : {sid}\n")
except Exception as e:
    print(f"Error: {e}")
