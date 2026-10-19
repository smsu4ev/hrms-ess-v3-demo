"""HRMS+ESS v3.8 tests: Nixtio-style soft-blue theme + dashboard rebuild.

Covers V38-THEME-SPEC.md acceptance:
Theme: style.css carries the new tokens (--bg/--panel/--navy/--blue/--blue2/
--chip) plus .panel and pill-nav classes; a normal page renders inside
.panel; the pill nav shows the active state on the dashboard and is
role-filtered (plain employee: no Payroll pill, no Approvals pill, no
settings gear); the <=860px media query flattens the dashboard columns
(display:contents on the .dcol-* columns) and orders the mord-* card
classes tracker -> tasks -> profile -> progress -> calendar.
Dashboard: greeting with the user's first name; KPI numbers match fixture
data; bars render % from fixture data; the profile salary chip renders only
for the fixture user WITH a salary assignment and is absent for the one
without (privacy: no other employee's salary figure appears in an
employee's HTML); the tracker ring's stroke-dashoffset + centre text come
from fixture punches; the tasks card % + rows (open-approval row for the
manager fixture); accordions list fixture leave balances/documents; the
calendar shows a fixture approved-leave chip. Regression guards:
.bottomnav base display:none rule intact before the mobile media query,
prefers-reduced-motion block present.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v38.db; ESS_SECRET_KEY=testsecret12345 \\
       ESS_DATABASE_URL=sqlite:////tmp/test_v38.db \\
       ~/workspace/ess-app/venv/bin/python tests/test_v38.py
"""
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from app import create_app, db  # noqa: E402
from app.models import (  # noqa: E402
    User, AttendanceRecord, PunchRecord, LeaveBalance, LeaveRequest,
    RosterAssignment, ShiftPattern, SalaryAssignment, SalaryComponent,
    EmployeeDocument, ApprovalInstance, Notice,
)

app = create_app()
app.config["WTF_CSRF_ENABLED"] = True
from app import limiter  # noqa: E402
limiter.enabled = False  # many logins in this suite; rate limiting is live-tested separately
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
    return client.post("/login", data={"username": uid, "password": pw,
                                       "csrf_token": t},
                       follow_redirects=False)


def logout():
    t = csrf_token(client.get("/change-password"))
    return client.post("/logout", data={"csrf_token": t},
                       follow_redirects=False)


def mk(uid, name, role, dept, **kw):
    if not User.query.get(uid):
        kw.setdefault("joining_date", date(2020, 1, 15))
        u = User(id=uid, name=name, role=role, department=dept,
                 employment_status="active", is_active=True, **kw)
        u.set_password("pw-" + uid)
        u.must_change_password = False
        db.session.add(u)
    return User.query.get(uid)


def pillnav(html):
    m = re.search(rb'<nav class="pillnav".*?</nav>', html, re.S)
    return m.group(0) if m else b""


TODAY = date.today()
MONTH_START = TODAY.replace(day=1)
MON = TODAY - timedelta(days=TODAY.weekday())  # Monday of this week

with app.app_context():
    mk("ADM38", "Sam Admin", "admin", "Human Resources",
       is_hr_manager=True, can_view_salary=True)
    mk("MGR38", "Mia Manager", "manager", "Production")
    mk("EMP381", "Sal Employee", "employee", "Production",
       reporting_manager="MGR38", monthly_salary=8500.0)
    mk("EMP382", "Nosal Employee", "employee", "Production",
       reporting_manager="MGR38")

    # Salary setup: EMP381 has an assignment (+ monthly_salary), EMP382 none.
    if not SalaryComponent.query.filter_by(code="BASIC38").first():
        db.session.add(SalaryComponent(code="BASIC38", name="Basic 38",
                                       kind="earning"))
        db.session.flush()
    basic = SalaryComponent.query.filter_by(code="BASIC38").first()
    if not SalaryAssignment.query.filter_by(user_id="EMP381").first():
        db.session.add(SalaryAssignment(user_id="EMP381",
                                        component_id=basic.id,
                                        amount=8500.0,
                                        effective_from=date(2020, 1, 15)))

    # Attendance: EMP381 present every day of the month so far; EMP382 today.
    d = MONTH_START
    while d <= TODAY:
        if not AttendanceRecord.query.filter_by(
                user_id="EMP381", date=d).first():
            db.session.add(AttendanceRecord(user_id="EMP381", date=d,
                                            check_in="08:05", check_out="18:00",
                                            status="present"))
        d += timedelta(days=1)
    if not AttendanceRecord.query.filter_by(
            user_id="EMP382", date=TODAY).first():
        db.session.add(AttendanceRecord(user_id="EMP382", date=TODAY,
                                        check_in="08:02", check_out="18:00",
                                        status="present"))

    # Leave balances for EMP381: annual 30/6 -> 20% used, 24 days balance.
    if not LeaveBalance.query.filter_by(user_id="EMP381",
                                        leave_type="annual").first():
        db.session.add(LeaveBalance(user_id="EMP381", leave_type="annual",
                                    entitled=30.0, used=6.0))

    # Roster + fixed-time closed punch pair today for EMP381: exactly 120 min
    # worked against a 600-min shift -> ring at 20%. Fixed UTC punches keep
    # the ring and the on-time rule deterministic at any run hour.
    if not ShiftPattern.query.filter_by(code="GEN38").first():
        db.session.add(ShiftPattern(code="GEN38", name="General 38",
                                     start_time="08:00", end_time="18:00",
                                     grace_minutes=10))
        db.session.flush()
    gen = ShiftPattern.query.filter_by(code="GEN38").first()
    if not RosterAssignment.query.filter_by(user_id="EMP381",
                                            date=TODAY).first():
        db.session.add(RosterAssignment(user_id="EMP381", date=TODAY,
                                        shift_id=gen.id))
    if not PunchRecord.query.filter_by(user_id="EMP381",
                                       local_date=TODAY).first():
        db.session.add(PunchRecord(user_id="EMP381", punch_type="in",
                                   punched_at=datetime.combine(TODAY,
                                                               time(3, 55)),
                                   local_date=TODAY))
        db.session.add(PunchRecord(user_id="EMP381", punch_type="out",
                                   punched_at=datetime.combine(TODAY,
                                                               time(5, 55)),
                                   local_date=TODAY))

    # Documents for EMP381: one expiring in 45d (warn tint), one far out.
    if not EmployeeDocument.query.filter_by(user_id="EMP381").first():
        db.session.add(EmployeeDocument(
            user_id="EMP381", category="identity", title="Emirates ID",
            stored_name="t38-eid.bin", expiry_date=TODAY + timedelta(days=45),
            superseded=False))
        db.session.add(EmployeeDocument(
            user_id="EMP381", category="identity", title="Passport",
            stored_name="t38-pass.bin",
            expiry_date=TODAY + timedelta(days=200), superseded=False))

    # Approved leave for EMP382 (same dept) covering Wed-Fri of this week ->
    # calendar chip; also feeds the manager/admin "leave decided" bar.
    wed = MON + timedelta(days=2)
    if not LeaveRequest.query.filter_by(user_id="EMP382",
                                        status="approved").first():
        db.session.add(LeaveRequest(user_id="EMP382", leave_type="annual",
                                    start_date=wed,
                                    end_date=wed + timedelta(days=2),
                                    days=3.0, reason="v3.8 fixture",
                                    status="approved",
                                    decided_at=datetime.utcnow()))

    # One pending approval awaiting MGR38 (specific-user step) ->
    # manager tasks card open-approval row.
    if not ApprovalInstance.query.filter_by(
            process_key="leave", ref_id="leave-t38-1").first():
        db.session.add(ApprovalInstance(
            process_key="leave", ref_id="leave-t38-1",
            subject_user_id="EMP381", requester_id="EMP381",
            steps_json=json.dumps([{"step_no": 1,
                                    "approver_type": "specific_user",
                                    "approver_user_id": "MGR38"}]),
            current_step=1, status="pending"))
    db.session.commit()

EXPECTED_PRESENT = TODAY.day  # every day of the month so far = present
RING_C = 2 * 3.14159265358979 * 54
EXPECTED_OFFSET = round(RING_C * (1 - 0.2), 2)  # 120/600 min -> 20% ring

_css = (ROOT / "app/static/style.css").read_text()

# ================= 1) theme: tokens + panel + pill nav in CSS =============
check("style.css has --bg token", "--bg:" in _css)
check("style.css has --panel token", "--panel:" in _css)
check("style.css has --navy token", "--navy:" in _css)
check("style.css has --blue token", "--blue:" in _css)
check("style.css has --blue2 token", "--blue2:" in _css)
check("style.css has --chip token", "--chip:" in _css)
check("style.css has .panel rule", re.search(r"\.panel\s*\{", _css)
      is not None)
check("style.css has .pillnav rule", re.search(r"\.pillnav\s*\{", _css)
      is not None)
check("style.css has .pillnav-item.active rule",
      ".pillnav-item.active" in _css)
check("style.css has .bottomnav base display:none before mobile query",
      ".bottomnav { display: none" in
      _css[:_css.find("@media (max-width: 860px)")])
check("prefers-reduced-motion block present",
      "prefers-reduced-motion" in _css)

# ================= 2) mobile dashboard ordering rules ======================
_mobile = _css[_css.find("@media (max-width: 860px)"):]
check("mobile query flattens dash columns",
      ".dcol-l, .dcol-m, .dcol-r { display: contents; }" in _mobile)
check("mobile mord-tracker order 0",
      ".mord-tracker { order: 0; }" in _mobile)
check("mobile mord-tasks order 1",
      ".mord-tasks { order: 1; }" in _mobile)
check("mobile mord-profile order 2",
      ".mord-profile { order: 2; }" in _mobile)
check("mobile mord-progress order 3",
      ".mord-progress { order: 3; }" in _mobile)
check("mobile mord-cal order 4",
      ".mord-cal { order: 4; }" in _mobile)

# ================= 3) shell: panel + pill nav ==============================
login("EMP381", "pw-EMP381")
r = client.get("/")
check("employee dashboard 200", r.status_code == 200)
check("normal page renders inside .panel", b'<div class="panel">' in r.data)
pn = pillnav(r.data)
check("pill nav renders", len(pn) > 0)
check("pill nav active state on dashboard",
      b'class="pillnav-item active"' in pn)
check("employee pill nav has Dashboard", b">Dashboard<" in pn)
check("employee pill nav has no Payroll pill", b">Payroll<" not in pn)
check("employee pill nav has no Approvals pill", b">Approvals<" not in pn)
check("employee has no settings gear", b"iconbtn-settings" not in r.data)
check("employee pill nav has More", b">More<" in pn)
logout()

login("MGR38", "pw-MGR38")
r = client.get("/leave")
check("manager normal page 200", r.status_code == 200)
check("manager page renders inside .panel",
      b'<div class="panel">' in r.data)
pn = pillnav(client.get("/").data)
check("manager pill nav has Approvals", b">Approvals<" in pn)
logout()

login("ADM38", "pw-ADM38")
pn = pillnav(client.get("/").data)
check("admin pill nav has Payroll pill", b">Payroll<" in pn)
check("admin pill nav has Approvals pill", b">Approvals<" in pn)
check("admin has settings gear", b"iconbtn-settings" in
      client.get("/").data)
logout()

# ================= 4) dashboard: greeting + KPI strip =====================
login("EMP381", "pw-EMP381")
r = client.get("/")
check("greeting uses first name", b"Welcome in, Sal" in r.data)
check("KPI strip renders", b'class="kpi-strip"' in r.data)
check("attendance bar shows fixture % (100%)",
      re.search(rb"My attendance[^<]*</div>\s*<div class=\"kbar-track\">"
                rb".*?<span class=\"kbar-pct\">100%</span>", r.data, re.S)
      is not None)
check("annual-leave-used bar shows fixture % (20%)",
      re.search(rb"Annual leave used</div>\s*<div class=\"kbar-track\">"
                rb".*?<span class=\"kbar-pct\">20%</span>", r.data, re.S)
      is not None)
check("training bar shows em-dash when no data",
      re.search(rb"Training completed</div>\s*<div class=\"kbar-track\">"
                rb".*?<span class=\"kbar-pct\">\xe2\x80\x94</span>",
                r.data, re.S) is not None)
check("days-present big number matches fixture",
      f'data-count="{EXPECTED_PRESENT}"'.encode() in r.data
      and f">{EXPECTED_PRESENT}<".encode() in r.data,
      f"expected={EXPECTED_PRESENT}")
check("annual-leave-balance big number matches fixture",
      b"Annual leave balance (days)" in r.data and b">24<" in r.data)
check("open-requests big number present", b"Open requests" in r.data)
logout()

# ================= 5) profile card: salary chip ============================
login("EMP381", "pw-EMP381")
r = client.get("/")
check("profile card renders", b'class="card profile-card mord-profile"'
      in r.data)
check("salary chip shown for user WITH assignment",
      b'class="pay-chip"' in r.data
      and b"AED 8,500.00 / month" in r.data)
logout()

login("EMP382", "pw-EMP382")
r = client.get("/")
check("salary chip absent for user WITHOUT assignment",
      b'class="pay-chip"' not in r.data)
check("status chip shown instead", b'class="status-chip"' in r.data
      and b"Active" in r.data)
check("privacy: no other employee's salary figure in employee HTML",
      b"8,500.00" not in r.data)
logout()

login("MGR38", "pw-MGR38")
r = client.get("/")
check("privacy: no salary figure in manager HTML",
      b"8,500.00" not in r.data)
logout()

# ================= 6) tracker ring from fixture punches ====================
login("EMP381", "pw-EMP381")
r = client.get("/")
check("tracker card renders", b'class="card tracker-card mord-tracker"'
      in r.data)
check("ring stroke-dashoffset from fixture punches",
      f"stroke-dashoffset:{EXPECTED_OFFSET}".encode() in r.data,
      f"expected={EXPECTED_OFFSET}")
check("ring centre text from fixture punches", b">2:00<" in r.data)
check("ring caption", b"worked today" in r.data)
check("punch button says Check In after out-punch",
      b">Check In<" in r.data)
check("punch button pulses when unpunched",
      b'class="punch-btn pulse"' in r.data)
logout()

# ================= 7) tasks card: % + rows ================================
login("MGR38", "pw-MGR38")
r = client.get("/")
check("dark tasks card renders",
      b'class="card tasks-dark mord-tasks"' in r.data)
check("tasks card header kept (My tasks)", b">My tasks<" in r.data)
check("tasks card shows 0% with 1 open item",
      b"tasks-pct\">0%</div>" in r.data
      and b"0 of 1 done" in r.data)
check("open-approval row for manager fixture",
      b"Approvals awaiting me" in r.data
      and b'<span class="task-count">1</span>' in r.data)
logout()

# ================= 8) accordions: leave balances + documents ==============
login("EMP381", "pw-EMP381")
r = client.get("/")
check("leave-balances accordion renders", b"My leave balances" in r.data)
check("accordion lists fixture annual balance",
      b"Annual Leave" in r.data and b"24 days remaining" in r.data)
check("sick balance hidden from employee (v3.3)",
      b"Sick Leave" not in r.data)
check("documents accordion renders", b"My documents" in r.data)
check("accordion lists fixture document", b"Emirates ID" in r.data)
check("near-expiry document gets warn tint",
      b"45d left" in r.data and b"acc-row warn" in r.data)
check("payslips accordion renders", b"My payslips" in r.data
      and b"Salary slip" in r.data)
logout()

login("ADM38", "pw-ADM38")
r = client.get("/")
check("admin accordion variant: expiring visas", b"Expiring visas" in r.data)
check("admin accordion variant: open tickets", b"Open tickets" in r.data)
logout()

# ================= 9) calendar: approved-leave chip ========================
login("EMP381", "pw-EMP381")
r = client.get("/")
check("calendar card renders", b'class="card cal-card mord-cal"' in r.data)
check("calendar shows fixture approved-leave chip",
      b"Nosal Employee" in r.data and b">NE<" in r.data)
check("this-week progress card renders",
      b'class="card week-card mord-progress"' in r.data)
check("accordions carry the profile mobile-order hook",
      b'class="acc mord-profile"' in r.data)
logout()

# ================= 10) admin/manager KPI variants ==========================
login("ADM38", "pw-ADM38")
r = client.get("/")
check("admin big number: active employees",
      b"Active employees" in r.data
      and b'data-count="4"' in r.data)
check("admin big number: present today", b"Present today" in r.data)
check("admin big number: pending approvals", b"Pending approvals" in r.data)
check("admin tickets-closed bar shows em-dash when no data",
      re.search(rb"Tickets closed</div>\s*<div class=\"kbar-track\">"
                rb".*?<span class=\"kbar-pct\">\xe2\x80\x94</span>",
                r.data, re.S) is not None)
logout()

login("MGR38", "pw-MGR38")
r = client.get("/")
check("manager captions", b"Team present today" in r.data
      and b"On leave today" in r.data and b"Approvals waiting" in r.data)
check("manager approvals-waiting count matches fixture",
      b'data-count="1"' in r.data)
check("manager leave-decided bar from fixture",
      re.search(rb"Leave decided[^<]*</div>\s*<div class=\"kbar-track\">"
                rb".*?<span class=\"kbar-pct\">100%</span>", r.data, re.S)
      is not None)
logout()

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
