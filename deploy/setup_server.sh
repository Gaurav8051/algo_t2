#!/bin/bash
# deploy/setup_server.sh
# Complete Oracle Cloud Ubuntu server setup for the Nifty Algo Trader.
#
# Run this ONCE after creating your Oracle Cloud (free tier) instance:
#   chmod +x deploy/setup_server.sh
#   ./deploy/setup_server.sh
#
# What it does:
#   1. Updates system packages
#   2. Installs Python 3.11
#   3. Installs dhanhq and dependencies
#   4. Creates project directory structure
#   5. Installs algo as a systemd service (auto-restarts if it crashes)
#   6. Opens firewall port if needed

set -e

echo "=========================================="
echo "  Nifty Algo Trader — Server Setup"
echo "=========================================="

# ── 1. System update ──────────────────────────────────────────────────────────
echo "[1/6] Updating system..."
sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-pip python3.11-venv git screen

# ── 2. Project directory ──────────────────────────────────────────────────────
echo "[2/6] Setting up project directory..."
PROJECT_DIR="$HOME/algo_v5"
mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"

# ── 3. Python venv ────────────────────────────────────────────────────────────
echo "[3/6] Creating Python virtual environment..."
cd "$PROJECT_DIR"
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install dhanhq==2.2.0

echo "[4/6] Python environment ready."
echo "  Activate with:  source ~/algo_v5/venv/bin/activate"

# ── 4. Systemd service ────────────────────────────────────────────────────────
echo "[5/6] Installing systemd service..."
SERVICE_FILE="/etc/systemd/system/algo_trader.service"

sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Nifty Algo Trader
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/main.py --mode paper
Restart=always
RestartSec=30
StandardOutput=append:$PROJECT_DIR/logs/service.log
StandardError=append:$PROJECT_DIR/logs/service.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable algo_trader

echo "[6/6] Setup complete!"
echo ""
echo "=========================================="
echo "  NEXT STEPS:"
echo "=========================================="
echo ""
echo "1. Edit config.py with your credentials:"
echo "   nano ~/algo_v5/config.py"
echo ""
echo "2. To run interactively (recommended first time):"
echo "   cd ~/algo_v5"
echo "   source venv/bin/activate"
echo "   python main.py --mode paper"
echo ""
echo "3. To start as background service:"
echo "   sudo systemctl start algo_trader"
echo "   sudo systemctl status algo_trader"
echo ""
echo "4. To open dashboard from local PC (SSH tunnel):"
echo "   See deploy/REMOTE_DASHBOARD.md"
echo ""
echo "5. To view logs:"
echo "   tail -f ~/algo_v5/logs/algo.log"
echo ""
