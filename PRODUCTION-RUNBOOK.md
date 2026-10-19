# HRMS+ESS v3 — PRODUCTION RUNBOOK (private host + real staff data)

Read this end-to-end before starting. Everything here was prepared in advance:
- `deploy/setup-production.sh` — one-shot VM setup (bash -n checked, never run on this box)
- `tools/migrate_real_data.py` — the staff-data loader (fixture-tested, 38/38)

The live demo on Render stays on fake demo data. **Real staff data goes ONLY
on this private host.**

## 0. What you need before step 1
- Oracle Cloud account signed in; an Always Free VM created (Ubuntu 22.04)
- SSH access to it as the `ubuntu` user (the SSH key was given at VM creation)
- The staff master file (`Employees_List__2.xlsx`) ready to copy over
- The app package (`hrms-ess-v3.zip`) ready to copy over

## 1. VM ready + SSH
Log in to the VM and copy the package up:
```bash
scp hrms-ess-v3.zip ubuntu@<VM-IP>:~/
scp Employees_List__2.xlsx ubuntu@<VM-IP>:~/
ssh ubuntu@<VM-IP>
unzip -q hrms-ess-v3.zip -d hrms-ess-v3
```

## 2. Run the setup script
```bash
bash hrms-ess-v3/deploy/setup-production.sh
```
It installs system packages, creates the `hrms` user, puts the app in
`/opt/hrms`, the database in `/var/lib/hrms/ess.db`, writes `/etc/hrms.env`
with an `ESS_SECRET_KEY=GENERATE_ME` placeholder, installs the systemd unit
and nginx site, and installs a daily backup cron (`/etc/cron.daily/hrms-backup`,
14-day retention). It installs the service but does NOT start it yet.

## 3. Set the secret key (required before first start)
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
sudo nano /etc/hrms.env   # paste the key over GENERATE_ME
```
Notes:
- `ESS_COOKIE_SECURE=1` in the template requires HTTPS. Until HTTPS is live
  (step 7), set it to `0` for IP-only HTTP access, then flip to `1` and
  `sudo systemctl restart hrms-ess`.
- **Never add `ESS_SEED_DEMO`** — demo data must never touch production.
- The service refuses to start while the `GENERATE_ME` placeholder is present.

## 4. Load the real staff data (cutover 2026-10-01)
```bash
cd /opt/hrms
sudo -u hrms ./venv/bin/python tools/migrate_real_data.py \
  --xlsx ~/Employees_List__2.xlsx \
  --db /var/lib/hrms/ess.db \
  --cutover 2026-10-01
```
What it does:
- Refuses if the DB already has users (re-run with `--force` only to append;
  `--force` is refused for any path inside the app tree).
- Loads ONLY Status == "Active" rows (resigned/terminated/retired are skipped
  and reported). Employee ID is the login; initial password is the DOB as
  DDMMYYYY, hashed with the app's own hasher, forced change on first login.
- Creates the visa-company master deterministically from the file: code =
  first letters of the first three name words (e.g. "Al-Ameen Industries LLC"
  → `AAI`, numeric suffix on collision) and links each employee.
- Salary: `BASIC` = Basic Salary effective 2026-10-01; if Monthly > Basic, an
  `OTHER_ALLOW` line = Monthly − Basic is added. Rows without basic get no
  salary (their IDs are in the report).
- Bootstraps **AA670** as admin + HR Manager if present (must_change_password
  stays True).
- Writes a JSON report next to the DB with counts and **Employee-ID-only**
  gap lists (missing IBAN, missing basic, missing DOB) — no names or salaries.
- Deliberately does NOT invent leave balances or loan/advance openings.

## 5. Start the app + first admin login
```bash
sudo systemctl start hrms-ess
sudo systemctl status hrms-ess   # should be active (running)
```
Open `http://<VM-IP>/` and log in as **AA670** with the DOB password
(DDMMYYYY). You are forced to set your own password immediately — do it.

## 6. HR verification (do this before inviting anyone)
1. Compare the loader's JSON report against the staff file: actives loaded
   vs expected; visa companies created; salary assignments.
2. HR assigns **reporting managers** (no source column — blank after migration)
   and the `can_view_salary` permission in Admin Users.
3. Check the seeded holiday/leave config matches the company's policy.
4. Only then share login details (Employee ID = username; DOB password +
   forced change).

## 7. HTTPS + domain
- The site is IP-only until a domain is provided. When the domain is ready:
  point it at the VM, then on the VM:
  ```bash
  sudo apt-get install -y certbot python3-certbot-nginx
  sudo certbot --nginx -d <your-domain>
  ```
- After HTTPS works: set `ESS_COOKIE_SECURE=1` in `/etc/hrms.env` and
  `sudo systemctl restart hrms-ess`. Also update nginx `server_name`.

## 8. Backups check
- `/etc/cron.daily/hrms-backup` writes an online SQLite backup to
  `/var/backups/hrms/ess-YYYY-MM-DD.db` daily and deletes files older than
  14 days. Verify after the first night: `ls -la /var/backups/hrms`.
- Restoring: stop the app, copy the backup over `/var/lib/hrms/ess.db`,
  start the app.

## 9. Rules that must never be broken
- **NEVER commit `/var/lib/hrms/ess.db` or the staff xlsx to GitHub** (or any
  public place). The repo `smsu4ev/hrms-ess-v3-demo` stays demo-data-only.
- Demo seed stays OFF in production — `ESS_SEED_DEMO` is never set.
- Employee photos and document uploads live under `/opt/hrms/instance/uploads`
  — include that folder in any manual backup copy.

## Mapping reference (file header → app field)
| File header | App field | Notes |
|---|---|---|
| Employee ID | User.id (login) | stripped |
| Name (En) | User.name | |
| Visa Company | Company master (code auto-derived) | e.g. "Al-Ameen Industries LLC" → `AAI` |
| Department / Designation | User.department / User.designation | as-is |
| DOB | User.date_of_birth + initial password DDMMYYYY | required; rows without DOB are skipped |
| Official Email → Personal Email | User.email | fallback chain |
| Mobile No. | User.phone + User.mobile | |
| Bank Name / IBAN No. | User.bank_name / User.iban | |
| Emirates ID / Passport No. | User.emirates_id_no / User.passport_no | |
| Date of join | User.joining_date | |
| Nationality | User.nationality | |
| Basic Salary / Monthly Salary | User.basic_salary / User.monthly_salary + SalaryAssignment lines | BASIC (+ OTHER_ALLOW split) effective cutover |
| Status | filter | only "Active" loaded |
| AA670 | admin + is_hr_manager | if present among actives |
| *(20 other columns, e.g. Labour Card No., Attendance ID, Marital Status…)* | *(none)* | present in file, no model field — skipped, listed in report |
