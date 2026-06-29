# Complete Setup & Deployment Guide — Nifty Algo Trader v5

## Architecture

```
┌──────────────────────────── ORACLE CLOUD (Ubuntu) ────────────────────────────┐
│  python main.py --mode paper|live                                               │
│    ├── AlgoEngine (strategy, orders)                                            │
│    ├── Dhan WebSocket feed                                                      │
│    ├── TCP Control Server :8765  ←──────────────────┐                           │
│    └── data/snapshot.json (every 10s)              │                           │
└────────────────────────────────────────────────────│───────────────────────────┘
                                                     │
                              SSH tunnel or direct TCP (port 8765 open)
                                                     │
┌──────────────────────────── LOCAL PC (Windows) ────│───────────────────────────┐
│  python dashboard_gui.py --remote <server-ip>  ────┘                           │
│  OR  python dashboard.py --remote <server-ip>   (CLI)                          │
│                                                                                 │
│  Open / close dashboard anytime — algo keeps running on server                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1 — One-time server setup (Oracle Cloud)

### 1.1 Create VM
1. [Oracle Cloud Free Tier](https://cloud.oracle.com) → Ubuntu 22.04 VM
2. Note **public IP** (e.g. `129.154.xxx.xxx`)
3. Security list: allow **TCP 22** (SSH) and **TCP 8765** (dashboard control)

### 1.2 Upload project
```powershell
scp -i C:\path\to\key.pem -r "C:\home\claude 7.0\algo_v5_fixed_patched (1)\fixed_algo_v5" ubuntu@129.154.xxx.xxx:~/algo_v5
```

### 1.3 Install on server
```bash
ssh -i key.pem ubuntu@129.154.xxx.xxx
cd ~/algo_v5
chmod +x deploy/setup_server.sh
./deploy/setup_server.sh
```

### 1.4 Configure Dhan API
```bash
nano ~/algo_v5/config.py
```
Set:
- `CLIENT_ID`, `ACCESS_TOKEN` (daily token from dhan.co)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (optional)
- `CONTROL_TOKEN = "your-secret"` (recommended for remote dashboard)

### 1.5 Whitelist server IP on Dhan
```bash
curl ifconfig.me
```
Add this IP at dhan.co → Profile → DhanHQ APIs → Static IP

---

## Part 2 — First-time trading setup (wizard)

Run interactively **once** on the server:

```bash
cd ~/algo_v5
source venv/bin/activate
python main.py --mode paper    # start with paper (recommended)
```

The wizard asks **step by step**:

| Step | What you enter |
|------|----------------|
| 0 | Mode already chosen via `--mode` |
| 1 | Index: NIFTY50 / BANKNIFTY / SENSEX / CRUDE_MCX |
| 2 | Global tolerance (default 11) |
| 3 | S/R levels (manual line-by-line or config defaults) |
| 4 | Expiry date — **validated against Dhan API** (rejects invalid dates) |
| 5 | Lots per order |
| 6 | First manual trade: CALL or PUT |
| 7 | Summary + **yes/no confirmation** → first order placed, strategy starts |

---

## Part 3 — Paper vs Live rules

| Rule | Behavior |
|------|----------|
| One mode at a time | Only one `main.py` (paper **or** live) via `data/algo.lock` |
| Paper → Live | **Step 1:** Dashboard → `Q-Paper` (or `q-paper` CLI). **Step 2:** `python main.py --mode live` |
| Live → Paper | **BLOCKED** while live positions are open. Close all first (`sell all`). |
| Dashboard close | Safe — algo keeps running |
| PnL limits | Default **disabled** (`PNL_LIMIT_MODE = "none"`) until you set via dashboard |

---

## Part 4 — Local dashboard (Windows)

### Option A — GUI (recommended)
```powershell
cd "C:\home\claude 7.0\algo_v5_fixed_patched (1)\fixed_algo_v5"
python dashboard_gui.py --remote 129.154.xxx.xxx --token your-secret
```

Tabs:
- **Status** — positions, PnL, FSM, S/R, disabled flags
- **Trade Control** — buy/sell, disable sides, Q-paper
- **S/R & Config** — levels, tolerance, expiry, lots, PnL targets
- **Order History** — last 40 trades

### Option B — CLI
```powershell
python dashboard.py --remote 129.154.xxx.xxx
```

### Option C — Same machine (local dev)
```powershell
# Terminal 1
python main.py --mode paper

# Terminal 2 (auto-opens on Windows)
python dashboard_gui.py
```

---

## Part 5 — Production systemd service

After first interactive setup:

```bash
sudo cp ~/algo_v5/deploy/algo_trader.service /etc/systemd/system/
# Edit ExecStart line: --mode paper or --mode live
sudo systemctl daemon-reload
sudo systemctl enable algo_trader
sudo systemctl start algo_trader
sudo systemctl status algo_trader
```

**Note:** systemd runs non-interactively. First run must be interactive to complete the wizard, OR pre-fill `config.py` (S/R, expiry, index) and restore from `session.json`.

---

## Part 6 — Manual controls reference

### Force exit
| Command | Action |
|---------|--------|
| `sell call` | Close CALL only |
| `sell put` | Close PUT only |
| `sell all` | Close everything |

### Force entry
| Command | Action |
|---------|--------|
| `buy call` | Manual CALL (blocked if CALL exists or CALL disabled) |
| `buy put` | Manual PUT (blocked if PUT exists or PUT disabled) |

### Disable strategy side
| Command | Effect |
|---------|--------|
| `disable call` | No CALL auto logic; manual CALL blocked |
| `disable put` | No PUT auto logic; manual PUT blocked |
| `disable both` | Both sides frozen for auto + manual entry |
| `enable call/put/all` | Re-enable |

Open positions on a disabled side: SL management still runs for risk (existing leg).

### S/R & PnL
```
add sr 23978          add sr 23978 8
del sr 23978
set tol 23956 9
set global tol 11
set pnl mode none|profit|loss|both
set profit 8000
set loss -4000
set pnl 5000          override realised PnL
```

---

## Part 7 — Daily routine

```
Before 9:15 AM IST:
  1. Generate new Dhan access token → update config.py on server
  2. sudo systemctl restart algo_trader
  3. Open local dashboard_gui.py --remote <ip>
  4. Type status → confirm FSM and positions

During market:
  - Dashboard open or closed — algo runs either way
  - Telegram alerts on every order

After 3:30 PM:
  - DELIVERY positions carry overnight automatically
  - Next day: session.json restores — no wizard if positions exist
```

---

## Part 8 — Troubleshooting

| Problem | Fix |
|---------|-----|
| Dashboard "No connection" | Check `systemctl status algo_trader`, port 8765 open |
| "LIVE active — cannot start PAPER" | Close live positions, stop live service |
| "PAPER still running" | SSH → Q-paper or kill paper process |
| Invalid expiry | Wizard rejects; run `python tools/find_expiry.py` |
| Remote command fails | Set matching `CONTROL_TOKEN` in config.py |

---

## File reference

| File | Purpose |
|------|---------|
| `main.py` | Algo + control server (run on cloud) |
| `dashboard_gui.py` | GUI control (run locally) |
| `dashboard.py` | CLI control (run locally) |
| `core/setup_wizard.py` | First-time setup prompts |
| `core/command_handler.py` | All dashboard commands |
| `core/control_server.py` | TCP server on port 8765 |
| `data/snapshot.json` | Live state (local mode) |
| `data/algo.lock` | Single-instance mode lock |
| `data/order_log.json` | Order history |
| `data/session.json` | Overnight position persistence |
