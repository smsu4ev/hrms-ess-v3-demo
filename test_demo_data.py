"""Demo-data integrity tests for the ESS_SEED_DEMO sample company.

Asserts the full 20-user synthetic company plus module sample data, created
by app/demo_seed.py via ESS_SEED_DEMO=1. Uses an isolated temp SQLite DB;
no real employee data.

Run: ESS_SECRET_KEY=testsecret12345 ESS_DATABASE_URL=<redacted> \\\n     python test_demo_data.py
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["ESS_SECRET_KEY"] = os.environ.get("ESS_SECRET_KEY", "testsecret12345")
os.environ["ESS_SEED_DEMO"] = "1"

from app import create_app, db  # noqa: E402
from app.models import (User, LeaveBalance, LeaveRequest, AttendanceRecord,
                        RegularisationRequest, Candidate, OnboardingCase,
                        Grievance, Evaluation)  # noqa: E402
import config as app_config  # noqa: E402

app = create_app()
app.config["WTF_CSRF_ENABLED"] = True

passed, failed = [], []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra else ""))


def new_client():
    return app.test_client()


def csrf_token(resp):
    m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    return m.group(1).decode() if m else ""


def login(client, uid, pw):
    r = client.get("/login")
    t = csrf_token(r)
    return client.post("/login", data={"username": uid, "password": pw,
                                       "csrf_token": t}, follow_redirects=False)


# ---------------------------------------------------------------- DB checks
with app.app_context():
    # ---- 1. Staff list: exactly 20 users, expected role split ----
    users = User.query.all()
    check("20 demo users exist", len(users) == 20, f"{len(users)}")
    check("no duplicate ids", len({u.id for u in users}) == 20)
    roles = {u.id: u.role for u in users}
    check("1 admin", sum(1 for r in roles.values() if r == "admin") == 1)
    check("3 managers", sum(1 for r in roles.values() if r == "manager") == 3)
    check("16 employees", sum(1 for r in roles.values() if r == "employee") == 16)
    depts = {u.department for u in users}
    check("7 departments", len(depts) == 7, f"{len(depts)}")
    nationals = {u.nationality for u in users if u.nationality != "Demo"}
    check(">=5 nationalities", len(nationals) >= 5, f"{len(nationals)}")
    ibans = [u.iban for u in users]
    check("fake UAE IBANs on all", all(i and i.startswith("AE") and len(i) == 23
                                      for i in ibans))
    check("salaries in 1400-14000 range",
          all(1400.0 <= (u.basic_salary or 0) <= 14000.0 for u in users))
    # Leave balances cover every UAE leave type for every user.
    ntypes = len(app_config.LEAVE_TYPES)
    bals = LeaveBalance.query.count()
    check("leave balances for all users/types",
          bals == 20 * ntypes, f"{bals}")
    maternity = LeaveBalance.query.filter_by(user_id="EMP010",
                                             leave_type="maternity").first()
    check("maternity balance exists", maternity is not None and maternity.entitled == 60)

    # ---- 3. Attendance seeded for current month ----
    ms = date.today().replace(day=1)
    att = AttendanceRecord.query.filter(AttendanceRecord.date >= ms).count()
    check("attendance records for all staff this month", att >= 15 * 20, f"{att}")
    statuses = {a.status for a in AttendanceRecord.query.all()}
    check("present/late/absent statuses present",
          {"present", "late", "absent"} <= statuses)

    # ---- 4. Leave requests: 3 pending, 2 approved, 1 rejected ----
    pend = LeaveRequest.query.filter_by(status="pending").all()
    check("3 pending leave requests", len(pend) == 3, f"{len(pend)}")
    check("pending includes maternity",
          any(p.leave_type == "maternity" for p in pend))
    check("pending from 3 different employees",
          len({p.user_id for p in pend}) == 3)
    check("2 approved leave requests",
          LeaveRequest.query.filter_by(status="approved").count() == 2)
    check("1 rejected leave request",
          LeaveRequest.query.filter_by(status="rejected").count() == 1)

    # ---- 5. Regularisation: 2 pending, 1 approved ----
    check("2 pending regularisations",
          RegularisationRequest.query.filter_by(status="pending").count() == 2)
    check("1 approved regularisation",
          RegularisationRequest.query.filter_by(status="approved").count() == 1)

    # ---- 7. Recruitment: one candidate in each of the 6 stages ----
    stages = {c.stage for c in Candidate.query.all()}
    check("candidates in all 6 stages", stages == set(Candidate.STAGES),
          f"{sorted(stages)}")

    # ---- 8. Onboarding: case 1 ~half done, case 2 barely started ----
    cases = OnboardingCase.query.order_by(OnboardingCase.id).all()
    check("2 onboarding cases", len(cases) == 2)
    check("case 1 (Sara Thomas) linked to EMP016",
          cases[0].employee_id == "EMP016", cases[0].name)
    check("case 1 progress between 1 and 99",
          1 <= cases[0].progress <= 99, f"{cases[0].progress}%")
    check("case 2 barely started", cases[1].progress < 25,
          f"{cases[1].progress}%")

    # ---- 9. Grievances: 1 under review, 1 resolved ----
    check("1 grievance under review",
          Grievance.query.filter_by(status="under_review").count() == 1)
    check("1 grievance resolved",
          Grievance.query.filter_by(status="resolved").count() == 1)

    # ---- 10. Evaluations: EMP001 acknowledged, EMP004 pending ----
    ev1 = Evaluation.query.filter_by(user_id="EMP001").first()
    check("EMP001 evaluation acknowledged", ev1 is not None and ev1.acknowledged)
    ev4 = Evaluation.query.filter_by(user_id="EMP004").first()
    check("EMP004 evaluation pending acknowledgment",
          ev4 is not None and not ev4.acknowledged)

# ------------------------------------------------- HTTP checks (no app ctx)
c_admin, c_mgr, c_emp, c_bad = (new_client(), new_client(),
                                new_client(), new_client())

# ---- 2. Logins: HRADMIN, MGR001, EMP001 ----
for client, uid, expected in [(c_admin, "HRADMIN", "Welcome, Demo HR Admin"),
                              (c_mgr, "MGR001", "Welcome, Demo Manager"),
                              (c_emp, "EMP001", "Welcome, Demo Employee One")]:
    r = login(client, uid, "Demo@123")
    check(f"login {uid} 302", r.status_code == 302, f"{r.status_code}")
    dash = client.get("/")
    check(f"dashboard {uid} 200 + welcome",
          dash.status_code == 200 and expected.encode() in dash.data,
          f"{dash.status_code}")
check("wrong password rejected",
      login(c_bad, "HRADMIN", "wrong").status_code == 200)

# ---- 4b. Approvals page (admin sees all 3 pending) ----
ap = c_admin.get("/approvals")
check("approvals page 200 for admin", ap.status_code == 200, f"{ap.status_code}")
for nm in [b"Rajesh Nair", b"Sunil Kumar", b"Ayesha Khan"]:
    check(f"approvals shows pending {nm.decode()}", nm in ap.data)

# ---- 5b. Regularisation approval page (manager) ----
rp = c_mgr.get("/regularisation/approve")
check("regularisation approval page 200 for manager",
      rp.status_code == 200, f"{rp.status_code}")

# ---- 6. Payslip pages render for EMP001 ----
ps = c_emp.get(f"/payslip/{date.today().year}/{date.today().month}")
check("payslip page 200 for EMP001", ps.status_code == 200, f"{ps.status_code}")

# ---- 7b. Recruitment page ----
rec = c_admin.get("/hrms/recruitment")
check("recruitment page 200", rec.status_code == 200, f"{rec.status_code}")
check("recruitment shows demo candidates", b"Ahmed Saleem" in rec.data)

# ---- 11. Compliance: probation ending + document expiries ----
cp = c_admin.get("/hrms/compliance")
check("compliance page 200", cp.status_code == 200, f"{cp.status_code}")
check("probation-ending employee shown (Ayesha Khan)",
      b"Ayesha Khan" in cp.data)
check("probation section not empty",
      b"No probation periods ending" not in cp.data)
nbadges = cp.data.count(b" days</span>")
check("document expiries shown (>=2 rows)", nbadges >= 2, f"{nbadges} badges")
check("visa expiring EMP002 shown", b"Demo Employee Two" in cp.data)
check("emirates id expiring EMP005 shown", b"Fatima Noor" in cp.data)

# ---- 12. WPS export: 20 active rows ----
wx = c_admin.get("/hrms/wps/export")
check("WPS export 200", wx.status_code == 200, f"{wx.status_code}")
lines = [ln for ln in wx.data.decode().splitlines() if ln.strip()]
check("WPS has header + 20 data rows", len(lines) == 21, f"{len(lines)}")
check("WPS header has IBAN column", "IBAN" in lines[0])
check("WPS includes HRADMIN row", any("HRADMIN" in ln for ln in lines[1:]))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
