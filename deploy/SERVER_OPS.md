# Server Operations — SSH, detach, stop, update code

## 1. Fix applied on your server (deploy these files)

The control-server crash was a typo: `SOL_REUSEADDR` → `SO_REUSEADDR`.

From your **local Windows PC** (project folder):

```powershell
cd "C:\home\claude 7.0\algo_v5_fixed_patched (1)\fixed_algo_v5"

scp -i "C:\home\claude 7.0\ssh-key-2026-06-26.key" `
  core/control_server.py `
  core/paper_api.py `
  core/command_handler.py `
  main.py `
  ubuntu@92.4.86.179:~/algo_v5/
```

Then on server — stop current run (Ctrl+C if attached), restart detached (see below).

---

## 2. Run algo so closing PowerShell does NOT stop it

**Never run `python main.py` directly in SSH** if you plan to close the laptop.
Use one of these:

### Option A — `screen` (simplest)

```bash
ssh -i key.pem ubuntu@92.4.86.179
cd ~/algo_v5 && source venv/bin/activate
screen -S algo
python main.py --mode paper
```

**Detach** (algo keeps running): press `Ctrl+A` then `D`

**Reattach later:**
```bash
ssh -i key.pem ubuntu@92.4.86.179
screen -r algo
```

**Close PowerShell / laptop** — algo continues on server.

### Option B — `nohup` (one-liner)

```bash
cd ~/algo_v5 && source venv/bin/activate
nohup python main.py --mode paper >> logs/service.log 2>&1 &
echo $!   # note PID
```

Exit SSH anytime. Logs: `tail -f ~/algo_v5/logs/service.log`

### Option C — systemd (production)

```bash
sudo systemctl start algo_trader
```

---

## 3. Exit SSH without stopping main.py

| What you did | What happens |
|--------------|--------------|
| Ran `python main.py` in plain SSH, then closed PowerShell | **Process KILLED** — bad |
| Ran inside `screen`, detached with `Ctrl+A D`, then closed SSH | **Keeps running** — good |
| Ran with `nohup ... &` | **Keeps running** — good |
| systemd service | **Keeps running** — good |

**Right now:** If algo is still running in your SSH session, open a **second** SSH window and either:
- Start using `screen` next time, OR
- Press `Ctrl+A` then `D` if already in screen

To move current session to background without screen (emergency):
```bash
# While main.py is running, press Ctrl+Z (suspends)
bg
disown -h %1
```
Not ideal — prefer `screen` for next restart.

---

## 4. Market closed messages (not a bug)

Outside 9:15–15:30 IST:
- Index spot uses **previous close** (24056) — normal
- Option LTP unavailable — paper uses **estimate fill** (0.5% of spot)
- After patch: one INFO every 5 min: *"Market closed — waiting for live data at 9:15 IST"*
- WebSocket connects; live candles start when market opens

Your PUT @ 24056 with fallback Rs120.28 is **expected** off-hours.

---

## 5. Stop trading completely

### Paper mode

| Goal | Command |
|------|---------|
| Close virtual positions + stop paper | Dashboard: **Q-Paper** or CLI: `q-paper` |
| Already flat, just stop process | Dashboard: `stop algo` or CLI: `stop algo` |
| Emergency close all | `sell all` → then `q-paper` or `stop algo` |

### Live mode

1. `sell all` (dashboard) — close real positions  
2. `stop algo` (when flat) — exits main.py  
3. Or: `sudo systemctl stop algo_trader`

### Order of operations

```
sell all  →  confirm yes  →  wait for status (no CALL/PUT)  →  q-paper (paper) or stop algo
```

---

## 6. Local dashboard from Windows

**Why "No connection" when main.py runs on server?**

The dashboard has two modes:

| Mode | When to use | How it connects |
|------|-------------|-----------------|
| **LOCAL** | `main.py` on **same PC** | Reads `data/snapshot.json` locally |
| **REMOTE** | `main.py` on **Oracle server** | TCP to server port **8765** |

If you run `python dashboard_gui.py` on Windows while `main.py` runs on the server,
there is **no local snapshot file** → dashboard shows "No connection".
**This is not a market-hours issue** — remote control works 24/7 for S/R, disable, etc.

### Correct command (direct TCP)

```powershell
cd "C:\home\claude 7.0\algo_v5_fixed_patched (1)\fixed_algo_v5"
python dashboard_gui.py --remote 92.4.86.179
```

Or double-click **`start_dashboard.bat`**.

**Oracle Cloud:** Networking → Virtual Cloud Network → Security List → **Ingress rule:**
- Source: your home IP (or `0.0.0.0/0` for testing)
- IP Protocol: TCP
- Destination port: **8765**

### Alternative: SSH tunnel (no firewall port needed)

**Terminal 1** — keep open while using dashboard:
```powershell
ssh -i "C:\home\claude 7.0\ssh-key-2026-06-26.key" -L 8765:localhost:8765 ubuntu@92.4.86.179
```

**Terminal 2** — dashboard via tunnel:
```powershell
python dashboard_gui.py --remote 127.0.0.1
```

### Optional token

Set in server `config.py`:
```python
CONTROL_TOKEN = "your-secret"
```
```powershell
python dashboard_gui.py --remote 92.4.86.179 --token your-secret
```

---

## 7. Update code on server (routine)

**NEVER `scp config.py` from your PC** — the local copy has placeholder credentials
and will overwrite the server's real `CLIENT_ID` / `ACCESS_TOKEN` (paper mode will
exit with `Credentials not set in config.py`).

Keep secrets in `~/algo_v5/config_local.py` on the server (see `config_local.py.example`).

```powershell
# From local project folder — code only (no config.py)
scp -i "C:\home\claude 7.0\ssh-key-2026-06-26.key" -r `
  core dashboard_gui.py dashboard.py main.py `
  ubuntu@92.4.86.179:~/algo_v5/
```

Wizard / SL / session fixes (example — still no config.py):
```powershell
scp -i "C:\home\claude 7.0\ssh-key-2026-06-26.key" `
  core/sr_engine.py core/algo_engine.py core/setup_wizard.py `
  core/session.py core/dhan_api.py core/paper_api.py `
  main.py `
  ubuntu@92.4.86.179:~/algo_v5/
```

On server:
```bash
# If using screen:
screen -r algo
# Ctrl+C to stop, then:
python main.py --mode paper

# If using systemd:
sudo systemctl restart algo_trader
```

**Do not restart during open live positions** unless intentional.

---

## 8. Check algo is running

```bash
ssh ubuntu@92.4.86.179
pgrep -af "main.py"
tail -f ~/algo_v5/logs/algo.log
cat ~/algo_v5/data/snapshot.json
```

Local dashboard **Status** tab should show fresh snapshot (< 15 s old).
