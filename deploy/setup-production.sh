#!/usr/bin/env bash
# HRMS+ESS v3 — one-shot PRODUCTION setup on a fresh Oracle Cloud Always Free VM
# (Ubuntu 22.04, ARM Ampere A1 or AMD micro). Run as the default ubuntu user:
#   bash deploy/setup-production.sh
# Expects the app extracted at ~/hrms-ess-v3 (upload hrms-ess-v3.zip and unzip).
#
# PRODUCTION SAFETY: this script NEVER seeds demo data and NEVER sets
# ESS_SEED_DEMO. Real staff data is loaded afterwards with
# tools/migrate_real_data.py (see PRODUCTION-RUNBOOK.md).
#
# The script stops short of starting the app: you must first set a real
# ESS_SECRET_KEY in /etc/hrms.env (the placeholder GENERATE_ME is refused).
set -euo pipefail

SRC="$HOME/hrms-ess-v3"
APP_DIR="/opt/hrms"
DATA_DIR="/var/lib/hrms"
BACKUP_DIR="/var/backups/hrms"

[ -d "$SRC" ] || { echo "ERROR: $SRC not found — unzip the app package there first"; exit 1; }

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip nginx ufw sqlite3

echo "==> Creating hrms user and directories"
sudo useradd -r -m -s /usr/sbin/nologin hrms 2>/dev/null || true
sudo mkdir -p "$APP_DIR" "$DATA_DIR" "$BACKUP_DIR"

echo "==> Copying app to $APP_DIR"
sudo rsync -a --delete \
    --exclude 'venv' --exclude '__pycache__' --exclude '*.db' \
    --exclude 'instance/' \
    "$SRC/" "$APP_DIR/"
sudo chown -R hrms:hrms "$APP_DIR" "$DATA_DIR" "$BACKUP_DIR"
sudo chmod 750 "$DATA_DIR"

echo "==> Python environment"
sudo -u hrms python3 -m venv "$APP_DIR/venv"
sudo -u hrms "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u hrms "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> Environment template /etc/hrms.env"
if [ ! -f /etc/hrms.env ]; then
sudo tee /etc/hrms.env > /dev/null <<'EOF'
# HRMS+ESS v3 production environment.
# 1. Replace GENERATE_ME with a long random value:
#      python3 -c "import secrets; print(secrets.token_hex(32))"
# 2. ESS_COOKIE_SECURE=1 requires HTTPS. The service will not work over plain
#    HTTP with this set; keep 0 until Let's Encrypt is done (runbook step 7),
#    then flip to 1 and restart the service.
# 3. NEVER add ESS_SEED_DEMO here — demo data must never touch production.
ESS_SECRET_KEY=GENERATE_ME
ESS_COOKIE_SECURE=1
ESS_DATABASE_URL=sqlite:////var/lib/hrms/ess.db
ESS_BEHIND_PROXY=1
EOF
sudo chmod 640 /etc/hrms.env
echo "Created /etc/hrms.env (edit ESS_SECRET_KEY now!)"
fi

echo "==> systemd service"
sudo tee /etc/systemd/system/hrms-ess.service > /dev/null <<'EOF'
[Unit]
Description=Al-Ameen HRMS+ESS v3 (gunicorn)
After=network.target

[Service]
User=hrms
Group=hrms
WorkingDirectory=/opt/hrms
EnvironmentFile=/etc/hrms.env
# Refuse to start with the placeholder secret key still in place.
ExecStartPre=/bin/sh -c '! grep -q "^ESS_SECRET_KEY=GENERATE_ME$" /etc/hrms.env'
ExecStart=/opt/hrms/venv/bin/gunicorn -w 3 -b 127.0.0.1:8000 run:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable hrms-ess

echo "==> nginx reverse proxy"
sudo tee /etc/nginx/sites-available/hrms-ess > /dev/null <<'EOF'
server {
    listen 80;
    # TODO: set your domain when ready, e.g. hrms.800retail.com
    server_name _;
    client_max_body_size 30M;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/hrms-ess /etc/nginx/sites-enabled/hrms-ess
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo "==> Firewall"
sudo ufw allow OpenSSH || true
sudo ufw allow 'Nginx Full' || true
sudo ufw --force enable || true

echo "==> Daily database backup (cron.daily, keeps 14 days)"
sudo tee /etc/cron.daily/hrms-backup > /dev/null <<'EOF'
#!/bin/sh
# HRMS+ESS v3: online SQLite backup, keep 14 days.
set -e
BACKUP_DIR=/var/backups/hrms
DB=/var/lib/hrms/ess.db
[ -f "$DB" ] || exit 0
sqlite3 "$DB" ".backup ${BACKUP_DIR}/ess-$(date +%F).db"
find "$BACKUP_DIR" -name 'ess-*.db' -mtime +13 -delete
EOF
sudo chmod +x /etc/cron.daily/hrms-backup
sudo /etc/cron.daily/hrms-backup || echo "(no DB yet — backup will pick it up after migration)"

echo ""
echo "=============================================================="
echo "NEXT STEPS (see PRODUCTION-RUNBOOK.md):"
echo "1. sudo nano /etc/hrms.env  -> set a real ESS_SECRET_KEY"
echo "   (if you will test over HTTP first, set ESS_COOKIE_SECURE=0,"
echo "   flip to 1 after HTTPS is live)"
echo "2. Load real staff data:"
echo "   cd /opt/hrms && sudo -u hrms ./venv/bin/python \\"
echo "     tools/migrate_real_data.py --xlsx /path/to/staff.xlsx \\"
echo "     --db /var/lib/hrms/ess.db --cutover 2026-10-01"
echo "3. sudo systemctl start hrms-ess   (refused while GENERATE_ME is set)"
echo "4. In the OCI console, add ingress rules for TCP 80 and 443"
echo "   (0.0.0.0/0) to the instance security list / NSG."
echo "=============================================================="
