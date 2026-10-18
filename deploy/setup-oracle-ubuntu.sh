#!/usr/bin/env bash
# HRMS+ESS v2 — one-shot setup on a fresh Oracle Cloud Always Free VM
# (Ubuntu 22.04, ARM Ampere A1 or AMD micro). Run as the default ubuntu user:
#   bash setup-oracle-ubuntu.sh
# Expects this app folder at ~/hrms-ess-v2 (upload the zip and unzip first).
set -euo pipefail

APP_DIR="$HOME/hrms-ess-v2"
cd "$APP_DIR"

echo "==> Installing system packages"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip nginx ufw

echo "==> Python environment"
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Environment (secret key generated once, stored in ~/hrms-ess-v2/.env.production)"
if [ ! -f .env.production ]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > .env.production <<EOF
ESS_SECRET_KEY=$SECRET
ESS_COOKIE_SECURE=0
EOF
  echo "Created .env.production (ESS_COOKIE_SECURE=0 until HTTPS is set up)."
fi
set -a; source .env.production; set +a

echo "==> Seeding DEMO data (fake users only, for sample testing)"
python seed_demo.py

echo "==> systemd service"
sudo cp deploy/hrms-ess.service /etc/systemd/system/hrms-ess.service
# Point the unit at this folder/user
sudo sed -i "s|/opt/hrms-ess|$APP_DIR|g; s|User=hrms|User=$USER|g" /etc/systemd/system/hrms-ess.service
sudo systemctl daemon-reload
sudo systemctl enable --now hrms-ess

echo "==> nginx reverse proxy"
sudo cp deploy/hrms-ess.nginx.conf /etc/nginx/sites-available/hrms-ess
sudo sed -i "s|/opt/hrms-ess|$APP_DIR|g" /etc/nginx/sites-available/hrms-ess
sudo ln -sf /etc/nginx/sites-available/hrms-ess /etc/nginx/sites-enabled/hrms-ess
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo "==> Firewall (also open 80/443 in the OCI console security list!)"
sudo ufw allow OpenSSH || true
sudo ufw allow 'Nginx Full' || true
sudo ufw --force enable || true

echo ""
echo "DONE. Open http://<VM public IP>/ and log in: HRADMIN / Demo@123"
echo "NOTE: Oracle Cloud also blocks ports at the VCN level — in the OCI"
echo "console, add ingress rules for TCP 80 and 443 (0.0.0.0/0) to the"
echo "instance's security list / NSG, or the site will not load."
