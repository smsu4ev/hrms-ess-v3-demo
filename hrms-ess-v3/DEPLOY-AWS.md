# HRMS + ESS v2 — AWS Deployment Guide (EC2 Ubuntu / Lightsail)

Applies to: Al-Ameen Industries LLC (800 Retail) HRMS+ESS v2 (Flask).
Region suggestion: `me-central-1` (UAE) for low latency; instance: `t3.small`
(minimum 2 GB RAM) for ~270 users.

## 1. Prepare AWS resources

1. Launch EC2 (Ubuntu 22.04 LTS) **or** Lightsail Ubuntu instance.
2. Security group: allow inbound **80** and **443** only (plus 22 from your office IP
   for SSH). No other inbound ports.
3. Allocate an Elastic IP, point your DNS A-record at it, e.g. `hrms.800retail.com`.

## 2. Server setup (Ubuntu 22.04)

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip nginx postgresql-client certbot python3-certbot-nginx
sudo useradd -m -s /bin/bash hrms
```

Copy the app to the server:

```bash
sudo mkdir -p /opt/hrms-ess && sudo chown hrms:hrms /opt/hrms-ess
# from your machine:
scp -r hrms-ess-v2 hrms@<server-ip>:/opt/hrms-ess/
```

On the server, as user `hrms`:

```bash
cd /opt/hrms-ess/hrms-ess-v2
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt gunicorn psycopg2-binary
```

## 3. Database — PostgreSQL / RDS (recommended for production)

1. Create RDS PostgreSQL (db.t3.micro is enough) in the same VPC,
   or install PostgreSQL locally on the EC2 instance.
2. Create DB `hrms` and a dedicated user with least privilege.

## 4. Environment variables (never commit secrets)

```bash
export ESS_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export ESS_DATABASE_URL="postgresql://hrms:YOUR_PASSWORD@<db-host>:5432/hrms"
export ESS_COOKIE_SECURE=1   # required behind HTTPS
```

Put these in `/etc/hrms-ess.env` (root-owned, mode 600) for systemd:

```
ESS_SECRET_KEY=<long-random-string>
ESS_DATABASE_URL=postgresql://hrms:PASSWORD@<db-host>:5432/hrms
ESS_COOKIE_SECURE=1
```

## 5. Seed employee data (once, from a bastion/admin machine)

```bash
cd /opt/hrms-ess/hrms-ess-v2
source venv/bin/activate
python seed.py /path/to/Employees_List__2.xlsx
```

## 6. systemd service + gunicorn

Template: `deploy/hrms-ess.service` in this package. Install:

```bash
sudo cp deploy/hrms-ess.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hrms-ess
sudo systemctl status hrms-ess
```

## 7. nginx reverse proxy + HTTPS

Template: `deploy/hrms-ess.nginx.conf`. Install:

```bash
sudo cp deploy/hrms-ess.nginx.conf /etc/nginx/sites-available/hrms-ess
sudo sed -i 's/hrms.example.com/hrms.800retail.com/' /etc/nginx/sites-available/hrms-ess
sudo ln -s /etc/nginx/sites-available/hrms-ess /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d hrms.800retail.com
```

## 8. Go-live checks

- [ ] `https://hrms.800retail.com/login` loads with a valid certificate
- [ ] Login works with an Employee ID; first-login password change enforced
- [ ] Admin panel: Manage Users, Audit Log, Import Attendance
- [ ] Security headers present (`curl -I https://hrms.800retail.com`)
- [ ] Backup tested (see below)

## 9. Backups

Daily automated dump (root cron):

```bash
0 2 * * * pg_dump -h <db-host> -U hrms hrms | gzip > /var/backups/hrms-$(date +\%F).sql.gz
```

Keep 14 days, restore-test monthly. If using RDS, enable automated snapshots
(retention 14 days) instead.

## 10. Operations notes

- Gunicorn workers: 3 for t3.small (adjust `workers = 2 * CPU + 1`).
- Rate limiter: point Flask-Limiter at Redis for multi-worker consistency
  (`RATELIMIT_STORAGE_URL=redis://localhost:6379/0` in systemd env).
- Keep OS, Python and packages patched monthly; review the audit log weekly.
