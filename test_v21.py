"""HRMS+ESS v2.1 tests: mobile-first UI, UAE compliance pack, gratuity, WPS export.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: ESS_SECRET_KEY=testsecret12345 ESS_DATABASE_URL=sqlite:////tmp/hrms_v21_test.db \
     python test_v21.py
"""
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import User, LeaveBalance, AuditLog  # noqa: E402
from app.uae import gratuity  # noqa: E402
import config as app_config  # noqa: E402

app = create_app()
app.config["WTF_CSRF_ENABLED"] = True
client = app.test_client()

passed, failed = [], []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra else ""))


def csrf_token(resp):
    m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    return m.group(1).decode() if m else ""


def login(uid, pw):
    r = client.get("/login")
    t = csrf_token(r)
    return client.post("/login", data={"username": uid, "password": pw, "csrf_token": t},
                       follow_redirects=False)


def logout():
    t = csrf_token(client.get("/change-password"))
    return client.post("/logout", data={"csrf_token": t},
                       follow_redirects=False)


# ---- UAE gratuity reference cases (pure function) ----
today = date.today()
r = gratuity(today - timedelta(days=7 * 365), today, 3000.0, "employer")
check("gratuity reference: 7yr employer = 16500", r["amount"] == 16500.0, r["amount"])
# Federal Decree-Law 33/2021, Art. 51 (in force 2 Feb 2022): the 1980-law
# resignation scale (1/3 for 1-3 yrs, 2/3 for 3-5 yrs) was REPEALED. A
# resigning employee with >= 1 year gets FULL gratuity, like termination.
r = gratuity(today - timedelta(days=4 * 365), today, 3000.0, "resignation")
check("gratuity resignation 4yr = full 84 days * 100 = 8400 (Art. 51)",
      r["amount"] == 8400.0 and r["multiplier"] == 1.0, r["amount"])
r2 = gratuity(today - timedelta(days=4 * 365), today, 3000.0, "employer")
check("gratuity resignation == termination for identical inputs",
      r["amount"] == r2["amount"], r["amount"])
# 4yrs service, 2 unpaid years -> effective 2yrs -> 42 days x 100 = 4200
r3 = gratuity(today - timedelta(days=4 * 365), today, 3000.0, "resignation",
             unpaid_days=2 * 365)
check("gratuity excludes unpaid absence days (Art. 51): 4200",
      r3["amount"] == 4200.0 and r3["unpaid_days_excluded"] == 2 * 365,
      r3["amount"])
r4 = gratuity(today - timedelta(days=4 * 365), today, 3000.0, "resignation",
             unpaid_days=4 * 365)
check("gratuity: unpaid days pushing service below 1 year -> 0",
      r4["amount"] == 0.0, r4["amount"])
r = gratuity(today - timedelta(days=200), today, 3000.0, "employer")
check("gratuity under 1 year = 0", r["amount"] == 0.0, r["amount"])

# ---- UAE leave types ----
lt = app_config.LEAVE_TYPES
for key, entitled in [("annual", 30), ("sick", 90), ("maternity", 60),
                      ("parental", 5), ("bereavement", 5), ("study", 10),
                      ("hajj", 30), ("unpaid", 0)]:
    check(f"leave type {key} = {entitled} days",
          key in lt and lt[key]["entitled"] == entitled,
          lt.get(key, {}).get("entitled"))
check("sick note documents 15/30/45 structure", "15" in lt["sick"]["note"] and "45" in lt["sick"]["note"])
check("maternity note documents 45/15 structure", "45" in lt["maternity"]["note"])

# ---- fixtures ----
with app.app_context():
    def mk(uid, name, role, dept, **kw):
        if not User.query.get(uid):
            u = User(id=uid, name=name, department=dept, designation="Engineer",
                     role=role, must_change_password=False,
                     employment_status="active")
            for k, v in kw.items():
                setattr(u, k, v)
            u.set_password("AdminPass1" if role == "admin" else "EmployeePass1")
            db.session.add(u)
            db.session.flush()
            for ltkey, cfg in app_config.LEAVE_TYPES.items():
                db.session.add(LeaveBalance(user_id=uid, leave_type=ltkey,
                                            entitled=cfg["entitled"], used=0.0))
        return User.query.get(uid)
    adm = mk("V21-ADM", "Test Admin", "admin", "HR",
             basic_salary=6000, monthly_salary=12000,
             joining_date=today - timedelta(days=5 * 365))
    emp = mk("V21-E1", "Test Employee", "employee", "TestDept",
             basic_salary=3000, monthly_salary=6000,
             joining_date=today - timedelta(days=3 * 365),
             iban="AE070331234567890123456", bank_name="Emirates NBD")
    mk("V21-EID", "Test Emirates ID", "employee", "TestDept",
       basic_salary=2000, joining_date=today - timedelta(days=100))
    mk("V21-PROB", "Test Probation", "employee", "TestDept",
       basic_salary=2000, joining_date=today - timedelta(days=160))
    e = User.query.get("V21-EID")
    e.emirates_id_expiry_date = today + timedelta(days=10)
    db.session.commit()

# ---- admin login ----
r = login("V21-ADM", "AdminPass1")
check("admin login", r.status_code == 302, r.status_code)

# ---- compliance page ----
r = client.get("/hrms/compliance")
check("compliance page 200 for admin", r.status_code == 200, r.status_code)
check("compliance shows 60-day expiries card", b"60 days" in r.data)
check("compliance shows Emirates ID", b"Emirates ID" in r.data or b"Emirates Id" in r.data)
check("compliance emirates-id expiry user appears", b"V21-EID" in r.data)
check("compliance probation case appears", b"V21-PROB" in r.data)
check("compliance gratuity liability card", b"Gratuity liability" in r.data)
check("compliance leave policy table", b"Maternity Leave" in r.data and b"Parental Leave" in r.data)
check("compliance working hours notes", b"Ramadan" in r.data and b"Overtime" in r.data)
check("compliance WPS note about .sif", b".sif" in r.data)

logout()
r = login("V21-E1", "EmployeePass1")
check("employee login", r.status_code == 302, r.status_code)
r = client.get("/hrms/compliance")
check("compliance 403 for employee", r.status_code == 403, r.status_code)

# ---- WPS export ----
logout()
login("V21-ADM", "AdminPass1")
r = client.get("/hrms/wps/export")
check("wps export 200 csv", r.status_code == 200 and r.mimetype == "text/csv", r.status_code)
check("wps filename", "wps-payroll-" in r.headers.get("Content-Disposition", ""))
check("wps has IBAN column", b"Bank IBAN" in r.data)
check("wps has active employee row + iban value",
      b"V21-E1" in r.data and b"AE070331234567890123456" in r.data)
check("wps fixed/variable split", b"Fixed (AED)" in r.data and b"Variable (AED)" in r.data)

# ---- employee edit: new fields go through audit ----
r = client.get("/hrms/employees/V21-E1/edit")
check("employee edit form has new fields",
      r.status_code == 200 and b"emirates_id_expiry_date" in r.data
      and b"name=\"iban\"" in r.data and b"name=\"bank_name\"" in r.data, r.status_code)
t = csrf_token(r)
r = client.post("/hrms/employees/V21-E1/edit", data={
    "name": "Test Employee", "department": "TestDept", "designation": "Engineer",
    "employment_status": "active",
    "joining_date": (today - timedelta(days=3 * 365)).isoformat(),
    "emirates_id_expiry_date": (today + timedelta(days=700)).isoformat(),
    "iban": "ae070339999999999999999", "bank_name": "Mashreq",
    "basic_salary": "3000", "monthly_salary": "6000", "csrf_token": t},
    follow_redirects=True)
with app.app_context():
    iban_rows = AuditLog.query.filter_by(entity="employee", entity_id="V21-E1",
                                         field_name="iban", action="employee_updated").all()
    bank_rows = AuditLog.query.filter_by(entity="employee", entity_id="V21-E1",
                                         field_name="bank_name", action="employee_updated").all()
    eid_rows = AuditLog.query.filter_by(entity="employee", entity_id="V21-E1",
                                        field_name="emirates_id_expiry_date",
                                        action="employee_updated").all()
    u = User.query.get("V21-E1")
check("iban edit audited old->new",
      iban_rows and iban_rows[0].old_value == "AE070331234567890123456"
      and iban_rows[0].new_value == "AE070339999999999999999",
      iban_rows[0].new_value if iban_rows else "none")
check("bank_name edit audited", bank_rows and bank_rows[0].new_value == "Mashreq",
      bank_rows[0].new_value if bank_rows else "none")
check("emirates id expiry edit audited",
      eid_rows and u.emirates_id_expiry_date.isoformat() == eid_rows[0].new_value,
      eid_rows[0].new_value if eid_rows else "none")

# ---- gratuity calculator (admin) ----
r = client.get("/hrms/gratuity")
check("gratuity calculator page 200", r.status_code == 200 and b"Gratuity Calculator" in r.data, r.status_code)
t = csrf_token(r)
r = client.post("/hrms/gratuity", data={
    "basic": "3000", "join": (today - timedelta(days=7 * 365)).isoformat(),
    "last": today.isoformat(), "separation": "employer", "csrf_token": t},
    follow_redirects=True)
check("gratuity calculator result 16500",
      b"16500.00" in r.data or b"16500" in r.data, "result shown")

logout()
r = login("V21-E1", "EmployeePass1")
check("employee re-login", r.status_code == 302, r.status_code)
r = client.get("/gratuity")
check("employee own gratuity estimate page 200", r.status_code == 200, r.status_code)
check("employee estimate is labelled estimate", b"estimate" in r.data.lower())
r = client.get("/profile")
check("profile shows EOS estimate", r.status_code == 200 and b"End-of-Service Estimate" in r.data, r.status_code)

# ---- mobile: bottom nav + viewport meta on dashboard ----
r = client.get("/")
check("viewport meta correct",
      b'content="width=device-width, initial-scale=1, viewport-fit=cover"' in r.data)
check("bottom nav markup present",
      b'class="bottomnav"' in r.data and b"Home" in r.data and b"Profile" in r.data)
# v3.7: the home quick-access grid is now the .tile-grid component
# (the old .tiles class survives only as a CSS grid alias, not in markup).
check("dashboard quick-access tiles present", b'class="tile-grid"' in r.data)
check("drawer JS included", b"app.js" in r.data)
check("tables wrapped in scroll containers", b"tbl-wrap" in r.data)

print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
