"""
tools/clear_session.py — Delete the saved session file to start fresh.

Use this when:
  - You want to cancel a carried-over position on next startup
  - The session file is corrupted
  - You are switching from paper to live mode

Run:  python tools/clear_session.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

files = [config.SESSION_FILE, config.PAPER_TRADE_FILE]
for f in files:
    if os.path.exists(f):
        ans = input(f"Delete {f}? (yes/no): ").strip().lower()
        if ans == "yes":
            os.remove(f)
            print(f"  Deleted: {f}")
        else:
            print(f"  Kept: {f}")
    else:
        print(f"  Not found: {f}")
print("Done.")
