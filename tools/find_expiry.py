"""
tools/find_expiry.py — List Nifty option expiries from Dhan.

Run from project root:
    python tools/find_expiry.py
    python tools/find_expiry.py BANKNIFTY
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from dhanhq import DhanContext
from core.dhan_api import make_dhan, get_expiry_list

index_key = sys.argv[1].upper() if len(sys.argv) > 1 else config.ACTIVE_INDEX
if index_key not in config.INDEX_CONFIG:
    print(f"Unknown index: {index_key}. Choose from: {list(config.INDEX_CONFIG.keys())}")
    sys.exit(1)

config.ACTIVE_INDEX = index_key
ctx  = DhanContext(config.CLIENT_ID, config.ACCESS_TOKEN)
dhan = make_dhan(ctx)

try:
    expiries = get_expiry_list(dhan, index_key)
    print(f"\nExpiries for {index_key} ({len(expiries)} total):\n")
    for i, e in enumerate(expiries):
        print(f"  [{i:2d}]  {e}")
    print(f"\nSet in config.py:  OPTION_EXPIRY = \"{expiries[0] if expiries else '?'}\"")
except Exception as e:
    print(f"Error: {e}")
    print("Check CLIENT_ID, ACCESS_TOKEN, IP whitelist in config.py")
