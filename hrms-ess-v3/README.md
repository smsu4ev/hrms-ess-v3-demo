# HRMS + ESS v2.1 — Al-Ameen Industries LLC (800 Retail)

Full HRMS + Employee Self-Service web application. Employees log in with their
**Employee ID**; the initial password is their **date of birth as DDMMYYYY**, and
they must change it on first login. Roles: **employee / manager / admin (HR)**.

The UI is mobile-first (responsive Argon-inspired desktop sidebar; slide-in drawer
+ bottom navigation on phones) and the ESS home screen is a SAP SuccessFactors-style
tile grid. Leave types and the compliance pack follow **UAE Labour Law
(Federal Decree-Law 33/2021)**.

## Modules

**ESS (employee):** Dashboard (tile grid) · Leave (UAE law types, apply/approve/balances) ·
Attendance (monthly view) · Attendance Regularisation (apply) · Payslips (view/print) ·
Profile · My End-of-Service estimate · Grievances (anonymous option) · Performance evaluations.

**MSS (manager):** Leave approvals (department-scoped) · Regularisation approvals ·
Evaluation creation · Grievance inbox.

**HRMS (HR admin):** Employee Central (list/search/filter, 360 view, add/edit incl.
Emirates ID expiry, IBAN, bank name) ·
UAE Compliance Dashboard (60-day document expiries incl. Emirates ID, probation endings,
gratuity liability, UAE leave policy, working-hours notes) ·
End-of-Service Gratuity Calculator · WPS payroll CSV export ·
Recruitment Tracker (Applied → Screening → Interview → Offer → Joined/Rejected) ·
Onboarding Checklist (8 standard tasks, progress %) ·
Advanced Audit Trail (filters + CSV export; all employee-field edits audited) ·
User management (password resets, roles, activate/deactivate) ·
Attendance CSV import · Visa/Passport/Emirates-ID expiry widget (30 days) on dashboard.

## Quick start (test on your computer)

```bash
cd hrms-ess-v2
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 1. Generate a strong secret key and export it
export ESS_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

# 2. Import employees from the staff master sheet
python seed.py /path/to/Employees_List.xlsx

# 3. Run
python run.py
# open http://127.0.0.1:5000
```

Login as admin: username `AA670`, password = your DOB as DDMMYYYY (you will be
forced to change it immediately).

## Testing (mandatory before any release)

```bash
export ESS_SECRET_KEY=testsecret12345
python test_e2e.py   # existing ESS flows (32 checks)
ESS_DATABASE_URL="sqlite:////tmp/hrms_v2_test.db" python test_v2.py   # v2 modules (33 checks)
```

## AWS deployment

See **DEPLOY-AWS.md** and the `deploy/` folder (nginx + systemd templates).

## What's built in (security)

- Passwords hashed with PBKDF2/scrypt (Werkzeug) — never stored in plain text
- Forced password change on first login; password policy (8+ chars, upper/lower/digit)
- Login rate limiting (10/min per IP) + account lockout after 5 failed attempts (15 min)
- CSRF protection on every form; HttpOnly + SameSite session cookies
- Role-based access: employee / manager / admin; admin-only HRMS screens
- Advanced audit trail: actor, action, entity, field-level old/new values, IP, timestamp
- Security headers (CSP, X-Frame-Options, nosniff)
- Generic login errors (never reveals whether an ID exists)

## Honest security notes (please read)

No web application is "fully secured" by code alone. The app follows best
practices above, but IT must also:

- Serve **only over HTTPS** (set `ESS_COOKIE_SECURE=1`)
- Keep the server OS, Python and packages patched
- Restrict admin accounts to HR staff; review the audit log periodically
- DOB-based initial passwords are **weak by design** — that is exactly why
  first-login change is forced; brief employees not to delay it
- For production, point Flask-Limiter at Redis instead of in-memory storage
  (`RATELIMIT_STORAGE_URL`) and use PostgreSQL (see DEPLOY-AWS.md)
- Keep daily encrypted backups of the database; test restores monthly
