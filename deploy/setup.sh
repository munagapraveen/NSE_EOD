#!/bin/bash
# =============================================================================
# NSE EOD Data Manager — Oracle Cloud VM Setup Script
# Run this ONCE on a fresh Ubuntu 22.04 ARM (A1) instance
# Usage: chmod +x setup.sh && sudo ./setup.sh
# =============================================================================

set -e  # Exit on any error

APP_DIR="/app/nse_eod"
DATA_DIR="/app/data"
VENV_DIR="$APP_DIR/.venv"
REPO_URL="https://github.com/YOUR_USERNAME/nse_eod.git"   # <-- CHANGE THIS
SERVICE_FILE="$APP_DIR/deploy/nse-eod.service"

echo "=============================================="
echo " NSE EOD Data Manager — Cloud Setup"
echo "=============================================="

# --- 1. System update & dependencies ---
echo "[1/8] Updating system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev \
    gcc g++ \
    libcurl4-openssl-dev libssl-dev pkg-config \
    nginx certbot python3-certbot-nginx \
    git curl \
    > /dev/null

# --- 2. Create app directories ---
echo "[2/8] Creating directories..."
mkdir -p "$APP_DIR" "$DATA_DIR/logs"
chown -R ubuntu:ubuntu /app

# --- 3. Clone the repository ---
echo "[3/8] Cloning repository..."
if [ -d "$APP_DIR/.git" ]; then
    echo "  Repo already exists — pulling latest..."
    cd "$APP_DIR" && git pull
else
    git clone "$REPO_URL" "$APP_DIR"
fi

# --- 4. Set up Python virtual environment ---
echo "[4/8] Setting up Python virtual environment..."
python3.11 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -e "$APP_DIR" -q

# --- 5. Configure environment ---
echo "[5/8] Configuring environment..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.cloud" "$APP_DIR/.env"
    echo "  .env created from .env.cloud — edit it to adjust settings."
else
    echo "  .env already exists — skipping."
fi

# --- 6. Set up systemd service ---
echo "[6/8] Installing systemd service..."
cp "$SERVICE_FILE" /etc/systemd/system/nse-eod.service
systemctl daemon-reload
systemctl enable nse-eod
systemctl start nse-eod
echo "  Service started. Check status: sudo systemctl status nse-eod"

# --- 7. Set up Nginx ---
echo "[7/8] Configuring Nginx..."
cp "$APP_DIR/deploy/nginx-nse-eod.conf" /etc/nginx/sites-available/nse-eod
# Remove default nginx site if it exists
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/nse-eod /etc/nginx/sites-enabled/nse-eod
nginx -t && systemctl restart nginx
echo "  Nginx configured. Visit http://YOUR_PUBLIC_IP to test."

# --- 8. Open firewall ports (Oracle Cloud also needs Security List rules) ---
echo "[8/8] Configuring UFW firewall..."
ufw allow 22    # SSH
ufw allow 80    # HTTP
ufw allow 443   # HTTPS
ufw --force enable

echo ""
echo "=============================================="
echo " Setup complete!"
echo "=============================================="
echo ""
echo " Next steps:"
echo "  1. Edit /app/nse_eod/.env if needed"
echo "  2. Check app: sudo systemctl status nse-eod"
echo "  3. View logs: sudo journalctl -u nse-eod -f"
echo "  4. For SSL: sudo certbot --nginx -d your-domain.com"
echo "  5. Open Oracle Security List: ports 80 and 443"
echo ""
