# Deployed Application Reference — Oracle Cloud Infrastructure

This document serves as a record of the cloud deployment of the **NSE EOD Data Manager** for future developers, maintainers, or AI coding assistants.

---

## 1. Server & Architecture Specifications

* **Hosting Provider:** Oracle Cloud Infrastructure (OCI) — Always Free Tier
* **Region / Availability Domain:** Hyderabad (`ap-hyderabad-1` / `ap-hyderabad-1-AD-1`)
* **VM Instance Name:** `nse-eod-server`
* **Public IP Address:** `68.233.118.69`
* **Custom Domain:** `nse-eod.duckdns.org`
* **Security Protocol:** HTTPS (port 443) with auto-redirect enabled. Let's Encrypt SSL certificate registered to `munagapraveen@gmail.com`.
* **OS / Image:** Canonical Ubuntu 22.04 LTS (Minimal)
* **Compute Shape:** `VM.Standard.E2.1.Micro` (AMD, 1 OCPU, 1 GB RAM)
* **Virtual Memory (Swap):** **4 GB swap file** (`/swapfile`) configured to extend virtual RAM capacity.
* **Persistent Disk Volume:** **50 GB block volume** attached as `sdb` (Paravirtualized) and mounted to `/app/data`.

---

## 2. Server Directory Layout

```text
/
├── app/
│   ├── nse_eod/                  # Application code directory (cloned/copied)
│   │   ├── src/                  # Python source packages
│   │   ├── deploy/               # Configuration templates (Nginx, systemd, setup.sh)
│   │   ├── .venv/                # Python 3.11 virtual environment
│   │   └── .env                  # Active environment settings (copy of .env.cloud)
│   └── data/                     # Mounted block volume mount point (50 GB)
│       ├── market.db             # DuckDB database file (~600 MB)
│       └── logs/
│           └── app.log           # Application logs
└── swapfile                      # 4 GB virtual memory swap file on boot disk
```

---

## 3. Server Setup Commands Performed

The following commands were executed sequentially to set up the VM:

### A. Swap Space (Virtual RAM) Setup
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### B. Block Volume Formatting & Mounting
```bash
sudo mkfs.ext4 -F /dev/sdb
sudo mkdir -p /app/data
sudo mount /dev/sdb /app/data
echo '/dev/sdb /app/data ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
sudo chown -R ubuntu:ubuntu /app
mkdir -p /app/data/logs
```

### C. System Packages & Python 3.11 Installation
```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev \
    gcc g++ libcurl4-openssl-dev libssl-dev pkg-config \
    nginx certbot python3-certbot-nginx git curl net-tools
```

### D. App Dependency Installation
```bash
cd /app/nse_eod
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
cp .env.cloud .env
```

### E. systemd Service Setup
```bash
sudo cp /app/nse_eod/deploy/nse-eod.service /etc/systemd/system/nse-eod.service
sudo systemctl daemon-reload
sudo systemctl enable nse-eod
sudo systemctl start nse-eod
```

### F. Nginx Proxy & Certbot SSL Setup
```bash
# Set server_name in config
sudo sed -i 's/server_name 68.233.118.69;/server_name nse-eod.duckdns.org;/g' /app/nse_eod/deploy/nginx-nse-eod.conf

# Enable site
sudo cp /app/nse_eod/deploy/nginx-nse-eod.conf /etc/nginx/sites-available/nse-eod
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/nse-eod /etc/nginx/sites-enabled/nse-eod
sudo systemctl reload nginx

# Request & Install SSL certificate
sudo certbot --nginx -d nse-eod.duckdns.org --agree-tos --non-interactive -m munagapraveen@gmail.com
```

### G. Linux Firewall rules (iptables)
```bash
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

---

## 4. Maintenance & Operation Cheatsheet

For any future updates or debugging, use these ssh commands from your local machine:
`ssh -i "$env:USERPROFILE\.ssh\oracle_nse_eod" ubuntu@68.233.118.69`

### A. Deploy Code Updates
If you change Python code locally and want to pull it to the VM:
```bash
# On the VM:
cd /app/nse_eod
git pull                 # If you configure git, or run scp from local PC
sudo systemctl restart nse-eod
```

### B. Control the App Service
```bash
# Start service
sudo systemctl start nse-eod

# Stop service
sudo systemctl stop nse-eod

# Restart service
sudo systemctl restart nse-eod

# Check status
sudo systemctl status nse-eod
```

### C. Monitoring Logs
```bash
# Live application startup/process logs
sudo journalctl -u nse-eod -f

# Read NiceGUI application logs
tail -f /app/data/logs/app.log

# Nginx access & error logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### D. Verify Storage & Memory
```bash
# Check disk space on the block volume (should stay under 50 GB)
df -h /app/data

# Check RAM and swap usage
free -h
top
```
