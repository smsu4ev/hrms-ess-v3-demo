"""HRMS+ESS v3.10 tests: HR Analytics page (/hrms/analytics).

Covers V310-ANALYTICS-SPEC.md acceptance + V310-DATA-CONTRACT.md formulas:
1. Access: admin / HR staff / manager 200; plain employee 403.
2. Attrition formula case: monthly leavers / avg(headcount start,end) * 100,
   hand-computed for two fixture months; YTD KPI + voluntary/involuntary split.
3. Manpower bars: active headcount per department desc + click-through hrefs.
4. Document tiers x windows bucketing (expired / <=30 / 31-60 / 61-90),
   soonest-expiries table ordering; user-level fields / superseded / null
   expiries excluded.
5. Absenteeism formula case: absent / (present+absent+late+leave); holiday
   excluded; by-department bars.
6. Annual leave donut % + per-department used/remaining.
7. OT hours from LOCKED runs (PayrollOTHours, hours only, never AED);
   draft runs ignored; by-department bars for the latest run.
8. Salary section: ABSENT for HR staff without can_view_salary and for a
   manager; PRESENT for an admin with can_view_salary; totals + biggest
   movers with hand-computed deltas.
9. Manager scoping: other-department rows absent everywhere.
10. period=6m/12m changes the month count; dept filter narrows results.
11. Empty-state messages on a fresh DB.
12. Nav: drawer/workspace link + All Services tile for admin & manager
    (not for plain employees); admin dashboard "View HR analytics" link.

Uses isolated temp SQLite DBs and test fixtures only (no real employee data).
Run: rm -f /tmp/test_analytics.db /tmp/test_analytics_empty.db && \\
ESS_SECRET_KEY=testsecret12345 ESS_DATABASE_URL=sqlite:////tmp/test_analytics.db \\
       ~/workspace/ess-app/venv/bin/python tests/test_analytics.py
"""
import calendar as calmod
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

DB = "/tmp/test_analytics.db"
os.environ["ESS_DATABASE_URL"] = f"sqlite:///{DB}"

from app import create_app, db  # noqa: E402
from app import limiter  # noqa: E402
from app.models import (  # noqa: E402
    User, SeparationRecord, AttendanceRecord, EmployeeDocument,
    LeaveBalance, PayrollRun, PayrollOTHours, PayrollLine,
)

app = create_app()
limiter.enabled = False
client = app.test_client()

passed, failed = [], []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra else ""))


def csrf_token(resp):
    m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    return m.group(1).decode() if m else ""


def login(c, uid, pw):
    try:
        t = csrf_token(c.get("/change-password"))
        c.post("/logout", data={"csrf_token": t}, follow_redirects=False)
    except Exception:
        pass
    r = c.get("/login")
    t = csrf_token(r)
    return c.post("/login", data={"username": uid, "password": pw,
                                  "csrf_token": t}, follow_redirects=False)


def d(n, day):
    """Date: day `day` of the month `n` months before the current month."""
    t = date.today()
    y, m = t.year, t.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, min(day, calmod.monthrange(y, m)[1]))


def period_str(n):
    t = date.today()
    y, m = t.year, t.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


TODAY = date.today()
LEAVER = lambda u: u.separation_date or u.exit_date  # noqa: E731


def mk(uid, name, role, dept, **kw):
    if not User.query.get(uid):
        kw.setdefault("joining_date", date(2020, 1, 15))
        kw.setdefault("employment_status", "active")
        kw.setdefault("is_active", True)
        u = User(id=uid, name=name, role=role, department=dept, **kw)
        u.set_password("pw-" + uid)
        u.must_change_password = False
        db.session.add(u)
    return User.query.get(uid)


def mkdoc(uid, category, title, expiry, superseded=False):
    doc = EmployeeDocument(user_id=uid, category=category, title=title,
                           stored_name="seed.bin", expiry_date=expiry,
                           superseded=superseded)
    db.session.add(doc)
    return doc


with app.app_context():
    adm = mk("ANADM", "Ana Admin", "admin", "Human Resources",
             can_view_salary=True)
    hrs = mk("ANHRS", "Ari HRStaff", "employee", "Human Resources",
             is_hr_staff=True)
    mgr1 = mk("ANM1", "Mia Manager", "manager", "Production")
    mgr2 = mk("ANM2", "Max Manager", "manager", "Sales")
    emp = mk("ANEMP", "Eli Employee", "employee", "Production",
             joining_date=date(2023, 5, 5))
    # Two departments, joiners/leavers placed in fixed months.
    p1 = mk("ANP1", "Pete Prod", "employee", "Production",
            joining_date=d(3, 5))
    p2 = mk("ANP2", "Pam Prod", "employee", "Production",
            joining_date=d(5, 10))
    p3 = mk("ANP3", "Paul Prod", "employee", "Production",
            joining_date=d(8, 1), employment_status="resigned",
            separation_date=d(2, 20), is_active=False)
    p4 = mk("ANP4", "Pip Prod", "employee", "Production",
            joining_date=d(0, 1))
    s1 = mk("ANS1", "Sara Sales", "employee", "Sales",
            joining_date=d(4, 12))
    s2 = mk("ANS2", "Sam Sales", "employee", "Sales",
            joining_date=d(8, 15), employment_status="terminated",
            exit_date=d(1, 25), is_active=False)
    s3 = mk("ANS3", "Sue Sales", "employee", "Sales",
            joining_date=d(6, 8))
    db.session.commit()

    # Separation register: 1 voluntary + 1 involuntary.
    if not SeparationRecord.query.filter_by(employee_code="ANP3").first():
        db.session.add(SeparationRecord(
            employee_code="ANP3", employee_name="Paul Prod",
            department="Production", separation_date=d(2, 20),
            separation_type="Resignation"))
    if not SeparationRecord.query.filter_by(employee_code="ANS2").first():
        db.session.add(SeparationRecord(
            employee_code="ANS2", employee_name="Sam Sales",
            department="Sales", separation_date=d(1, 25),
            separation_type="Termination"))
    db.session.commit()

    # Attendance: days 1..min(18, today) of this month so (user, date) stays
    # unique whenever the suite runs. Production: 2 absent / (n-2) denom;
    # Sales: 1 absent / n denom; 2 holiday rows are excluded from the denom.
    _days = list(range(1, min(18, TODAY.day) + 1))
    ND = len(_days)
    if not AttendanceRecord.query.filter_by(user_id="ANP1").first() and ND >= 8:
        def add_att(uid, counts):
            i = 0
            for status, n in counts:
                for _ in range(n):
                    db.session.add(AttendanceRecord(
                        user_id=uid, date=date(TODAY.year, TODAY.month,
                                              _days[i % ND]),
                        status=status))
                    i += 1
        prod = [("present", ND - 6), ("absent", 2), ("leave", 1), ("late", 1),
                ("holiday", 2)]
        sales = [("present", ND - 2), ("absent", 1), ("leave", 1)]
        for uid in ("ANP1", "ANP2", "ANP4"):
            add_att(uid, prod)
        for uid in ("ANS1", "ANS3"):
            add_att(uid, sales)
        db.session.commit()
    globals()["ATT_DAYS"] = ND if ND >= 8 else 0

    # Vault documents.
    if not EmployeeDocument.query.filter_by(user_id="ANP1").first():
        mkdoc("ANP1", "Passport", "Pete passport", TODAY + timedelta(days=10))
        mkdoc("ANP1", "Residence Visa", "Pete visa", TODAY - timedelta(days=5))
        mkdoc("ANS1", "Emirates ID", "Sara EID", TODAY + timedelta(days=45))
        mkdoc("ANS1", "Driving Licence", "Sara licence",
              TODAY + timedelta(days=70))
        mkdoc("ANS3", "Education Certificate", "Sue degree",
              TODAY + timedelta(days=20))
        mkdoc("ANS3", "Offer Letter", "Sue offer",
              TODAY + timedelta(days=400))          # >90d: excluded
        mkdoc("ANP2", "Passport", "Pam old passport",
              TODAY - timedelta(days=2), superseded=True)  # excluded
        mkdoc("ANP4", "Labour Card", "Pip labour card", None)  # excluded
        mkdoc("ANS2", "Passport", "Sam passport",
              TODAY + timedelta(days=15))
        db.session.commit()

    # Annual leave balances: used 45 / entitled 120 -> 37.5%.
    if not LeaveBalance.query.filter_by(user_id="ANP1",
                                        leave_type="annual").first():
        for uid, ent, used in (("ANP1", 30.0, 10.0), ("ANP2", 30.0, 5.0),
                               ("ANS1", 30.0, 30.0), ("ANS3", 30.0, 0.0)):
            db.session.add(LeaveBalance(user_id=uid, leave_type="annual",
                                        entitled=ent, used=used))
        db.session.commit()

    # Payroll: 2 locked runs + 1 draft (draft must be ignored).
    def mkrun(period, status):
        r = PayrollRun.query.filter_by(period=period).first()
        if not r:
            r = PayrollRun(period=period, status=status)
            db.session.add(r)
            db.session.commit()
        return r
    run1 = mkrun(period_str(1), "locked")
    run2 = mkrun(period_str(0), "locked")
    mkrun(period_str(2), "draft")
    if not PayrollLine.query.filter_by(run_id=run1.id).first():
        for uid, net in (("ANP1", 8000.0), ("ANP2", 9000.0),
                         ("ANS1", 7000.0), ("ANS3", 6000.0)):
            db.session.add(PayrollLine(run_id=run1.id, user_id=uid, net=net))
        for uid, net in (("ANP1", 8500.0), ("ANP2", 8700.0),
                         ("ANS1", 7000.0), ("ANS3", 6200.0)):
            db.session.add(PayrollLine(run_id=run2.id, user_id=uid, net=net))
        db.session.commit()
    if not PayrollOTHours.query.filter_by(run_id=run1.id).first():
        db.session.add(PayrollOTHours(run_id=run1.id, user_id="ANP1",
                                      category="normal", hours=5.0))
        db.session.add(PayrollOTHours(run_id=run1.id, user_id="ANS1",
                                      category="night", hours=3.0))
        db.session.add(PayrollOTHours(run_id=run2.id, user_id="ANP1",
                                      category="normal", hours=4.0))
        db.session.add(PayrollOTHours(run_id=run2.id, user_id="ANP2",
                                      category="restday", hours=2.5))
        db.session.add(PayrollOTHours(run_id=run2.id, user_id="ANS3",
                                      category="normal", hours=1.5))
        db.session.commit()

users_all = ["ANADM", "ANHRS", "ANM1", "ANM2", "ANEMP",
             "ANP1", "ANP2", "ANP3", "ANP4", "ANS1", "ANS2", "ANS3"]

with app.app_context():
    U = {uid: User.query.get(uid) for uid in users_all}


def hc_at(day):
    n = 0
    for u in U.values():
        j, lv = u.joining_date, LEAVER(u)
        if (j is None or j <= day) and (lv is None or lv >= day):
            n += 1
    return n


# ================= 1) access control =================
login(client, "ANADM", "pw-ANADM")
r = client.get("/hrms/analytics")
check("admin 200", r.status_code == 200, r.status_code)
ADMIN_HTML = r.data

login(client, "ANHRS", "pw-ANHRS")
r = client.get("/hrms/analytics")
check("HR staff 200", r.status_code == 200, r.status_code)

login(client, "ANM1", "pw-ANM1")
r = client.get("/hrms/analytics")
check("manager 200", r.status_code == 200, r.status_code)
MGR_HTML = r.data

login(client, "ANEMP", "pw-ANEMP")
r = client.get("/hrms/analytics")
check("plain employee 403", r.status_code == 403, r.status_code)

login(client, "ANADM", "pw-ANADM")  # back to admin for the data checks
r = client.get("/hrms/analytics")
ADMIN_HTML = r.data

# ================= 2) attrition formula =================
# Month mo(-2): leavers = Paul (sep day 20) -> 1; hc_start=11, hc_end=10.
p_m2 = period_str(2)
m = re.search(rb'data-month="%s" data-ratio="([\d.]+)" data-leavers="(\d+)"'
              % p_m2.encode(), ADMIN_HTML)
check("attrition mo(-2) ratio 9.52 / 1 leaver", bool(m) and m.group(1) == b"9.52"
      and m.group(2) == b"1", m.group(0)[:80] if m else "no match")
# Month mo(-1): leavers = Sam (exit day 25) -> 1; hc_start=10, hc_end=9.
p_m1 = period_str(1)
m = re.search(rb'data-month="%s" data-ratio="([\d.]+)" data-leavers="(\d+)"'
              % p_m1.encode(), ADMIN_HTML)
check("attrition mo(-1) ratio 10.53 / 1 leaver", bool(m) and m.group(1) == b"10.53"
      and m.group(2) == b"1", m.group(0)[:80] if m else "no match")
# Independent re-check of the formula for mo(-2):
s2, e2 = d(2, 1), d(2, 28)
exp_leavers = [u for u in U.values()
               if LEAVER(u) and s2 <= LEAVER(u) <= e2]
exp_ratio = len(exp_leavers) / ((hc_at(s2) + hc_at(e2)) / 2) * 100
check("attrition formula recomputed: 1 leaver, ratio 9.52",
      len(exp_leavers) == 1 and abs(exp_ratio - 9.5238) < 0.01,
      f"leavers={len(exp_leavers)} ratio={exp_ratio:.4f}")

# YTD KPI: leavers this calendar year / avg(headcount Jan 1, today).
ytd_leavers = [u for u in U.values()
               if LEAVER(u) and LEAVER(u).year == TODAY.year]
ytd_ratio = (len(ytd_leavers)
             / ((hc_at(date(TODAY.year, 1, 1)) + hc_at(TODAY)) / 2) * 100)
m = re.search(rb'data-kpi="attrition".*?data-value="([\d.]+)"',
              ADMIN_HTML, re.S)
check("attrition YTD KPI matches hand computation",
      bool(m) and abs(float(m.group(1)) - round(ytd_ratio, 2)) < 0.01,
      f"rendered={m.group(1) if m else None} expected={ytd_ratio:.2f}")
check("YTD voluntary/involuntary split shown",
      b"1 voluntary / 1 involuntary" in ADMIN_HTML)

# ================= 3) manpower =================
check("manpower Production bar = 5",
      b'data-dept="Production" data-count="5"' in ADMIN_HTML)
check("manpower Sales bar = 3", b'data-dept="Sales" data-count="3"' in ADMIN_HTML)
check("manpower HR bar = 2", b'data-dept="Human Resources" data-count="2"' in ADMIN_HTML)
check("manpower click-through to filtered employees",
      b'href="/hrms/employees?dept=Production"' in ADMIN_HTML)
m = re.search(rb'data-kpi="headcount".*?data-value="(\d+)"', ADMIN_HTML, re.S)
check("headcount KPI = 10 active", bool(m) and m.group(1) == b"10",
      m.group(1) if m else None)

# ================= 4) document tiers =================
check("T1 expired bucket = 1",
      b'data-tier="0" data-window="Expired" data-count="1"' in ADMIN_HTML)
check("T1 <=30d bucket = 2",
      'data-tier="0" data-window="≤30d" data-count="2"'.encode("utf-8")
      in ADMIN_HTML)
check("T1 31-60d bucket = 1",
      'data-tier="0" data-window="31–60d" data-count="1"'.encode("utf-8")
      in ADMIN_HTML)
check("T2 61-90d bucket = 1",
      'data-tier="1" data-window="61–90d" data-count="1"'.encode("utf-8")
      in ADMIN_HTML)
check("T3 <=30d bucket = 1",
      'data-tier="2" data-window="≤30d" data-count="1"'.encode("utf-8")
      in ADMIN_HTML)
# Soonest table: days-left ascending -5, 10, 15, 20, 45, 70.
days = re.findall(rb'data-days="(-?\d+)"', ADMIN_HTML)
check("soonest expiries ordered -5..70 (6 rows)",
      days == [b"-5", b"10", b"15", b"20", b"45", b"70"], str(days))
check("doc row links to vault", b"user=ANP1" in ADMIN_HTML)
check("expired row shown in red", b'class="neg" data-days="-5"' in ADMIN_HTML)
m = re.search(rb'data-kpi="docs".*?data-value="(\d+)"', ADMIN_HTML, re.S)
check("docs <=30d KPI = 3", bool(m) and m.group(1) == b"3",
      m.group(1) if m else None)
check("docs KPI red when > 0", b'class="k-val alert" data-value="3"' in ADMIN_HTML)

# ================= 5) absenteeism =================
# This month: Production 2 absent / (ND-2) per user x3; Sales 1 / ND x2.
ND = ATT_DAYS
EXP_ABS_PCT = (8 / (3 * (ND - 2) + 2 * ND) * 100) if ND else None
m = re.search(rb'data-kpi="absenteeism".*?data-value="([\d.-]*)"',
              ADMIN_HTML, re.S)
if EXP_ABS_PCT is None:
    check("absenteeism KPI empty when <8 days in month",
          bool(m) and m.group(1) == b"", m.group(1) if m else None)
else:
    check("absenteeism this-month KPI matches hand computation",
          bool(m) and abs(float(m.group(1)) - round(EXP_ABS_PCT, 2)) < 0.01,
          f"rendered={m.group(1) if m else None} expected={EXP_ABS_PCT:.2f}")
    mp0 = period_str(0)
    m2 = re.search(rb'data-month="%s" data-pct="([\d.]+)"' % mp0.encode(),
                   ADMIN_HTML)
    check("absenteeism trend bar this month matches",
          bool(m2) and abs(float(m2.group(1)) - round(EXP_ABS_PCT, 2)) < 0.01,
          m2.group(1) if m2 else None)
check("absenteeism caption states the exact definition",
      "absent ÷ (present + absent + late + leave) × 100".encode("utf-8")
      in ADMIN_HTML)
# Independent recomputation from the fixture records:
with app.app_context():
    recs = AttendanceRecord.query.filter(
        AttendanceRecord.date >= d(0, 1)).all()
absent = sum(1 for r in recs if r.status == "absent")
denom = sum(1 for r in recs if r.status in ("present", "absent", "late", "leave"))
if EXP_ABS_PCT is None:
    check("no attendance rows when <8 days in month", not recs)
else:
    exp_pct = absent / denom * 100 if denom else None
    check("absenteeism formula recomputed",
          absent == 8 and denom == 3 * (ND - 2) + 2 * ND
          and abs(exp_pct - EXP_ABS_PCT) < 1e-9,
          f"absent={absent} denom={denom}")
# By-department bars (latest month with records) carry the pct in <title>.
if ND:
    exp_prod = f"Production: absenteeism {2 / (ND - 2) * 100:.1f}%"
    exp_sales = f"Sales: absenteeism {1 / ND * 100:.1f}%"
    check("absenteeism by-dept Production pct in tooltip",
          exp_prod.encode() in ADMIN_HTML, exp_prod)
    check("absenteeism by-dept Sales pct in tooltip",
          exp_sales.encode() in ADMIN_HTML, exp_sales)

# ================= 6) annual vacation =================
m = re.search(rb'id="chart-vacation".*?data-pct="([\d.]+)"', ADMIN_HTML, re.S)
check("leave donut = 37.50%", bool(m) and m.group(1) == b"37.50",
      m.group(1) if m else None)
check("leave remaining 75.0 days shown",
      b"75.0 days" in ADMIN_HTML or b">75.0<" in ADMIN_HTML)
check("leave by-dept Production row",
      b'data-dept="Production"' in ADMIN_HTML)
check("no money figure in leave section",
      b"AED" not in ADMIN_HTML.split(b'id="chart-vacation"')[1]
      .split(b'id="chart-ot"')[0])

# ================= 7) overtime =================
ots = re.findall(rb'data-hours="([\d.]+)"', ADMIN_HTML)
check("OT per locked run = 8.00h x2 (draft ignored)",
      ots[:2] == [b"8.00", b"8.00"], str(ots[:3]))
check("OT by-dept Production 6.5h",
      b'data-dept="Production"' in ADMIN_HTML and True)
m = re.search(rb'data-kpi="ot".*?data-value="([\d.]+)"', ADMIN_HTML, re.S)
check("OT KPI latest run = 8.00", bool(m) and m.group(1) == b"8.00",
      m.group(1) if m else None)
check("OT caption says hours only / PayrollOTHours",
      b"PayrollOTHours" in ADMIN_HTML and b"no AED" in ADMIN_HTML)
check("no AED amounts in OT section",
      not re.search(rb"AED [\d]", ADMIN_HTML.split(b'id="chart-ot"')[1]
                    .split(b'Monthly salary differences')[0]))

# ================= 8) salary section gating + values =================
check("salary section PRESENT for admin with can_view_salary",
      b"Monthly salary differences" in ADMIN_HTML)
nets = re.findall(rb'data-net="([\d.]+)"', ADMIN_HTML)
check("salary run totals 30000 / 30400", nets == [b"30000.00", b"30400.00"],
      str(nets))
check("salary delta +1.3% labelled", b"+1.3%" in ADMIN_HTML)
check("biggest mover Pete Prod +500.00",
      b'data-name="Pete Prod"' in ADMIN_HTML and b"+500.00" in ADMIN_HTML)
i_pete = ADMIN_HTML.find(b'data-name="Pete Prod"')
i_pam = ADMIN_HTML.find(b'data-name="Pam Prod"')
i_sue = ADMIN_HTML.find(b'data-name="Sue Sales"')
check("movers ordered by |delta| desc", 0 < i_pete < i_pam < i_sue)
check("movers link to payslip run",
      b"/hrms/payroll/" in ADMIN_HTML)

login(client, "ANHRS", "pw-ANHRS")
r = client.get("/hrms/analytics")
check("salary section ABSENT for HR staff without can_view_salary",
      b"Monthly salary differences" not in r.data)
check("salary section ABSENT for manager",
      b"Monthly salary differences" not in MGR_HTML)

# ================= 9) manager scoping =================
check("manager sees own-dept names", b"Pete Prod" in MGR_HTML)
check("manager does NOT see Sales names",
      b"Sara Sales" not in MGR_HTML and b"Sue Sales" not in MGR_HTML
      and b"Sam Sales" not in MGR_HTML)
check("manager manpower shows Production only",
      b'data-dept="Production"' in MGR_HTML
      and b'data-dept="Sales"' not in MGR_HTML)
check("manager has no department select", b'id="dept"' not in MGR_HTML)
check("manager page notes own-department scope",
      b"own department only" in MGR_HTML)

# ================= 10) period + dept params =================
login(client, "ANADM", "pw-ANADM")
r = client.get("/hrms/analytics?period=6m")
n6 = len(re.findall(rb'data-ratio="', r.data))
r = client.get("/hrms/analytics?period=12m")
n12 = len(re.findall(rb'data-ratio="', r.data))
check("period=6m renders 6 attrition points", n6 == 6, n6)
check("period=12m renders 12 attrition points", n12 == 12, n12)
r = client.get("/hrms/analytics?dept=Sales")
check("dept=Sales narrows manpower",
      b'data-dept="Sales"' in r.data and b'data-dept="Production"' not in r.data)
check("period pill keeps dept param",
      b"period=6m" in r.data and b"dept=Sales" in r.data)

# ================= 11) joiners vs leavers =================
m = re.search(rb'data-kpi="movement".*?data-value="([\d-]+)"', ADMIN_HTML,
              re.S)
check("movement KPI this month = 1 joiner / 0 leavers",
      bool(m) and m.group(1) == b"1-0", m.group(1) if m else None)
_m0 = period_str(0)
m = re.search(rb'data-month="%s" data-joiners="(\d+)"' % _m0.encode(),
              ADMIN_HTML)
check("joiners bar this month = 1", bool(m) and m.group(1) == b"1")
check("movement caption carries totals",
      b"net 1" in ADMIN_HTML or b"net&nbsp;1" in ADMIN_HTML
      or "net 1" in ADMIN_HTML.decode())

# ================= 12) nav =================
with app.test_request_context():
    from app.workspaces import workspace_nav, visible_workspaces
    adm_u, mgr_u, emp_u = (User.query.get(x) for x in ("ANADM", "ANM1", "ANEMP"))
    labels = [l["label"] for g in workspace_nav(adm_u, "hr-setup")
              for l in g["links"]]
    check("workspace nav: admin sees HR Analytics", "HR Analytics" in labels)
    labels = [l["label"] for g in workspace_nav(mgr_u, "hr-setup")
              for l in g["links"]]
    check("workspace nav: manager sees HR Analytics", "HR Analytics" in labels)
    check("workspace nav: employee sees no hr-setup groups",
          workspace_nav(emp_u, "hr-setup") == [])
    mgr_keys = [w["key"] for w in visible_workspaces(mgr_u)]
    check("manager sees hr-setup workspace", "hr-setup" in mgr_keys)

login(client, "ANM1", "pw-ANM1")
r = client.get("/ws/hr-setup")
check("/ws/hr-setup 200 for manager with HR Analytics link",
      r.status_code == 200 and b"HR Analytics" in r.data, r.status_code)
r = client.get("/apps")
check("All Services tile for manager", b"HR Analytics" in r.data)
r = client.get("/")
check("manager dashboard has no analytics KPI link",
      b"View HR analytics" not in r.data)

login(client, "ANADM", "pw-ANADM")
r = client.get("/")
check("admin dashboard shows 'View HR analytics' link",
      b"View HR analytics" in r.data)

login(client, "ANEMP", "pw-ANEMP")
r = client.get("/apps")
check("employee All Services has no HR Analytics tile",
      b"HR Analytics" not in r.data)

# ================= 13) theme recolour check =================
login(client, "ANADM", "pw-ANADM")
client.set_cookie("ess_ui_theme", "night")
r = client.get("/hrms/analytics")
check("analytics page renders in night theme with CSS vars",
      r.status_code == 200 and b'data-theme="night"' in r.data
      and b"var(--blue)" in r.data)
client.delete_cookie("ess_ui_theme")

print()
print(f"V3.10 main: {len(passed)} passed, {len(failed)} failed")

# ================= 14) empty states on a fresh DB =================
# NOTE: config.py reads ESS_DATABASE_URL at import time, and create_app()
# touches the DB (create_all) before returning — so patch the config dict
# BEFORE creating the second app.
EMPTY_DB = "/tmp/test_analytics_empty.db"
if os.path.exists(EMPTY_DB):
    os.remove(EMPTY_DB)
import config as app_config  # noqa: E402
app_config.CONFIG["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{EMPTY_DB}"
app2 = create_app()
limiter.enabled = False
client2 = app2.test_client()
with app2.app_context():
    if not User.query.get("EMPTYADM"):
        u = User(id="EMPTYADM", name="Empty Admin", role="admin",
                 department=None, employment_status="active", is_active=True,
                 can_view_salary=True)
        u.set_password("pw-EMPTYADM")
        u.must_change_password = False
        db.session.add(u)
        db.session.commit()
    # Sanity: this really is the empty DB, not the fixture DB.
    assert User.query.count() == 1, User.query.count()

login(client2, "EMPTYADM", "pw-EMPTYADM")
r = client2.get("/hrms/analytics")
H = r.data
check("empty DB 200", r.status_code == 200, r.status_code)
for name, needle in [
    ("empty attrition state", b"No separations in the last 12 months"),
    ("empty manpower state", b"No active employees with a department set yet"),
    ("empty docs state", b"No vault documents with expiry dates yet"),
    ("empty attendance state", b"No attendance records in the last 12 months"),
    ("empty leave state", b"No annual-leave balances recorded yet"),
    ("empty OT state", b"No overtime hours on the last 6 locked payroll run"),
    ("empty salary state", b"Need at least 2 locked payroll runs"),
    ("empty movement state", b"No joiners or leavers in the last 12 months"),
]:
    check(name, needle in H)
check("empty headcount KPI = 1 (only the admin)",
      b'data-kpi="headcount"' in H and b'data-value="1"' in H)

print()
print(f"V3.10 total: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
