"""
tools/test_telegram.py — Verify Telegram bot is configured correctly.

Setup:
  1. Message @BotFather on Telegram -> /newbot -> copy token
  2. Message @userinfobot -> copy your chat_id (the number)
  3. Paste both into config.py:
       TELEGRAM_BOT_TOKEN = "7123456789:AAExxx..."
       TELEGRAM_CHAT_ID   = "123456789"
  4. Run this script:
       python tools/test_telegram.py

You should receive a test message in your Telegram within 5 seconds.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
    print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not set in config.py")
    sys.exit(1)

from core.telegram_alert import test_alert
test_alert()
print("Test message sent. Check your Telegram.")
