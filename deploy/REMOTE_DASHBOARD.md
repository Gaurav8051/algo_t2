# Remote Dashboard Setup — Oracle Cloud + Local Desktop

## Architecture

```
Oracle Cloud Server (Ubuntu)          Your Local Desktop (Windows)
─────────────────────────────         ──────────────────────────────
main.py  ←─── runs 24x7              dashboard.py  ←── open/close anytime
    │                                      │
    │ writes snapshot.json ────────────────┘  (via SSH tunnel or shared folder)
    │ reads command.json   ────────────────┐
    │                                      │
    └─── algo runs with last config ───────┘
```

The key insight: **main.py and dashboard.py communicate through two JSON files**.
You can close and reopen dashboard.py at any time — the algo never pauses.

---

## OPTION A — SSH Tunnel (Recommended for Oracle Cloud)

### Step 1: Set up Oracle Cloud Free Tier

1. Go to https://cloud.oracle.com → Sign up (free)
2. Create a **VM.Standard.E2.1.Micro** instance (free tier)
   - OS: Ubuntu 22.04
   - Shape: Always Free eligible
3. Download the SSH private key during setup (e.g. `ssh-key.pem`)
4. Note your instance **Public IP** (e.g. `129.154.xxx.xxx`)
5. Open port 22 in Security List (usually default)

### Step 2: Upload the project to server

From your Windows PC (PowerShell):
```powershell
# Replace with your actual IP and key path
scp -i C:\path\to\ssh-key.pem -r C:\home\claude\algo_v5 ubuntu@129.154.xxx.xxx:~/
```

### Step 3: First-time server setup

SSH into server:
```bash
ssh -i C:\path\to\ssh-key.pem ubuntu@129.154.xxx.xxx
cd ~/algo_v5
chmod +x deploy/setup_server.sh
./deploy/setup_server.sh
```

Then edit config.py with your Dhan credentials:
```bash
nano ~/algo_v5/config.py
```

**IMPORTANT: Add your Oracle Cloud server's static IP to Dhan's IP whitelist.**
On the server, run:
```bash
curl ifconfig.me
```
Copy this IP → dhan.co → Profile → DhanHQ Trading APIs → Static IP Setting → Edit

### Step 4: Run algo interactively (first time)

```bash
cd ~/algo_v5
source venv/bin/activate
python main.py --mode paper
```

Answer the startup prompts once. After this, you can switch to service mode.

### Step 5: Run as background service (production)

```bash
# Install service
sudo cp ~/algo_v5/deploy/algo_trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable algo_trader
sudo systemctl start algo_trader

# Check it's running
sudo systemctl status algo_trader

# View live logs
tail -f ~/algo_v5/logs/algo.log
```

### Step 6: Open dashboard from local Windows PC

The simplest method — open a **second SSH terminal** on your Windows PC
and run dashboard.py ON THE SERVER:

**Terminal 1 (already running — algo):**
```
ssh -i ssh-key.pem ubuntu@129.154.xxx.xxx
(algo is running via systemd — nothing to type here)
```

**Terminal 2 (dashboard — open any time):**
```powershell
# Windows PowerShell
ssh -i C:\path\to\ssh-key.pem ubuntu@129.154.xxx.xxx
cd ~/algo_v5
source venv/bin/activate
python dashboard.py
```

You can close Terminal 2 at any time. The algo keeps running.
Reopen Terminal 2 whenever you want to check status or send commands.

---

## OPTION B — Shared folder via rsync (alternative)

If you prefer to run dashboard.py on your LOCAL Windows PC
while main.py runs on the server, sync the data folder:

```powershell
# Install rsync for Windows (via Git Bash or WSL)
# Run this in a loop to keep data/ synced from server to local:
while ($true) {
    rsync -az -e "ssh -i ssh-key.pem" `
        ubuntu@129.154.xxx.xxx:~/algo_v5/data/ `
        C:\home\claude\algo_v5\data\
    rsync -az -e "ssh -i ssh-key.pem" `
        C:\home\claude\algo_v5\data\command.json `
        ubuntu@129.154.xxx.xxx:~/algo_v5/data/ 2>$null
    Start-Sleep 3
}
```

Then run dashboard.py locally:
```powershell
cd C:\home\claude\algo_v5
python dashboard.py
```

---

## OPTION C — Screen session (simplest, no service needed)

```bash
# On server — start algo in a detachable screen session
ssh -i ssh-key.pem ubuntu@129.154.xxx.xxx
cd ~/algo_v5
source venv/bin/activate
screen -S algo
python main.py --mode paper

# Detach from screen (algo keeps running):
# Press: Ctrl+A then D

# Reattach later:
screen -r algo
```

For dashboard, open second SSH terminal and run dashboard.py there.

---

## Daily Routine (Production)

```
Morning (before 9:10 AM IST):
  1. Go to dhan.co -> Profile -> DhanHQ Trading APIs
  2. Click "Generate" next to your app -> copy new Access Token
  3. SSH to server:  ssh -i ssh-key.pem ubuntu@<ip>
  4. Edit token:     nano ~/algo_v5/config.py
  5. Restart service: sudo systemctl restart algo_trader
  6. Open dashboard in second SSH terminal: python dashboard.py
  7. Type 'status' to confirm algo is running

During trading:
  - Dashboard open or closed — algo runs either way
  - Monitor: tail -f ~/algo_v5/logs/algo.log
  - Telegram alerts arrive on your phone for every trade

After market (3:30 PM IST):
  - DELIVERY positions stay open — no action needed
  - INTRA positions are auto-squared by Dhan at 3:20 PM

Next morning:
  - Service auto-restarts with new session
  - If DELIVERY positions exist, algo resumes from saved session
  - No first-trade prompt — algo reads open positions automatically
```

---

## Service Management Commands

```bash
sudo systemctl start algo_trader      # start
sudo systemctl stop algo_trader       # stop gracefully
sudo systemctl restart algo_trader    # restart (use after config changes)
sudo systemctl status algo_trader     # check if running
journalctl -u algo_trader -f          # view systemd logs
tail -f ~/algo_v5/logs/algo.log       # view algo logs
tail -f ~/algo_v5/logs/service.log    # view service stdout
```

---

## File Roles (complete reference)

```
algo_v5/
├── main.py              THE ALGO — run this on server, never close
├── dashboard.py         CONTROL — open/close anytime from any terminal
├── config.py            SETTINGS — edit before each session
├── requirements.txt     pip install -r requirements.txt
│
├── core/
│   ├── states.py        Dataclasses: Position, SRLevel, Candle, AlgoState, FSMState
│   ├── sr_engine.py     All S/R math (pure Python, no API)
│   ├── algo_engine.py   FSM brain: all trading decisions
│   ├── dhan_api.py      All Dhan API calls (live orders + market data)
│   ├── paper_api.py     Paper trading (real data, fake orders)
│   ├── market_feed.py   Dhan WebSocket (live Nifty ticks -> CandleBuilder)
│   ├── candle_builder.py  Ticks -> 1-min OHLC Candle
│   ├── session.py       Persist positions across sessions (delivery mode)
│   └── telegram_alert.py  Trade notifications to Telegram
│
├── tools/
│   ├── find_expiry.py      List available option expiries
│   ├── check_strike.py     Verify security_id for a strike
│   ├── download_history.py Download 1-min OHLC CSV for backtesting
│   ├── test_telegram.py    Send test Telegram message
│   └── clear_session.py    Delete saved session file
│
├── deploy/
│   ├── setup_server.sh     Run once on Oracle Cloud to set up server
│   ├── algo_trader.service systemd service unit (auto-restart)
│   └── REMOTE_DASHBOARD.md This file
│
├── data/
│   ├── snapshot.json       Written by main.py every 10s (dashboard reads this)
│   ├── command.json        Written by dashboard.py (main.py reads and deletes)
│   ├── session.json        Open positions saved here (delivery carry-forward)
│   └── paper_trades.json   Paper trade log
│
└── logs/
    ├── algo.log            Full audit trail of every decision
    └── service.log         systemd service stdout
```

---

## Key Concepts

### Why can dashboard be closed without stopping algo?

main.py and dashboard.py share state through **two files**:
- `data/snapshot.json` — main.py writes this every 10s with all state
- `data/command.json`  — dashboard.py writes commands, main.py reads within 1s

They never share a process or a network connection. Dashboard is just a
file reader/writer. Closing it has zero effect on main.py.

### How does algo handle overnight positions (DELIVERY mode)?

After each candle, session.py writes:
- Open positions (strike, security_id, entry price, S/R levels, SL state)

Next morning when main.py starts:
1. It reads session.json
2. Calls `get_positions()` on Dhan to verify positions still exist on exchange
3. If confirmed → restores AlgoState, skips first-trade prompt
4. Resumes exactly where it left off, including active SLs

### What happens if main.py crashes?

- systemd restarts it within 30 seconds
- It reloads session.json → positions resume
- Telegram alert fires when session starts
- No manual intervention needed

### What if I need to force-sell all and start fresh?

From dashboard: type `sell all` → confirm `yes`
Or from server: `python tools/clear_session.py` then restart service
