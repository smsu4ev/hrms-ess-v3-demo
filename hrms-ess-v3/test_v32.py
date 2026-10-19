"""HRMS+ESS v3.2 tests: shift roster module + final settlement module.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v32.db && ESS_SECRET_KEY=testsecret12345 \\
     ESS_DATABASE_URL=sqlite:////tmp/test_v32.db python test_v32.py
"""
import io
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import (User, ShiftPattern, RosterAssignment, RosterSettings,
                        AttendanceRecord, FinalSettlement, SettlementLine,
                        SeparationRecord, LeaveBalance, Loan,
                        AuditLog, AssetCustody)  # noqa: E402
from app import uae  # noqa: E402

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


def get_token(path):
    return csrf_token(client.get(path))


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


today = date.today()
monday = today - timedelta(days=today.weekday())

with app.app_context():
    admin = mk("T32-ADM", "T32 Admin", "admin", "Human Resources")
    admin2 = mk("T32-ADM2", "T32 Admin Two", "admin", "Human Resources")
    mgr = mk("T32-MGR", "T32 Manager", "manager", "Production")
    emp = mk("T32-E1", "T32 Employee One", "employee", "Production",
             designation="Welder", reporting_manager="T32-MGR")
    emp2 = mk("T32-E2", "T32 Employee Two", "employee", "Production")
    emp_sales = mk("T32-S1", "T32 Sales One", "employee", "Sales")
    emp3 = mk("T32-E3", "T32 Employee Three", "employee", "Production")
    db.session.add(ShiftPattern(code="GEN", name="General", start_time="08:00",
                                end_time="18:00", break_minutes=60,
                                grace_minutes=10))
    db.session.add(ShiftPattern(code="NIGHT", name="Night", start_time="22:00",
                                end_time="06:00", break_minutes=60,
                                grace_minutes=10))
    db.session.commit()

with app.app_context():
    check("NIGHT crosses_midnight is True",
          ShiftPattern.query.filter_by(code="NIGHT").first().crosses_midnight)
    check("GEN crosses_midnight is False",
          not ShiftPattern.query.filter_by(code="GEN").first().crosses_midnight)

# ================= shifts CRUD =================
login("T32-ADM", "pw-T32-ADM")
t = get_token("/hrms/shifts")
r = client.post("/hrms/shifts", data={
    "csrf_token": t, "code": "MORN", "name": "Morning", "start_time": "06:00",
    "end_time": "14:00", "break_minutes": "30", "grace_minutes": "10"},
    follow_redirects=False)
check("admin creates shift pattern (redirect)", r.status_code in (301, 302, 303))
r = client.get("/hrms/shifts")
check("shift list shows MORN", r.status_code == 200 and b"MORN" in r.data)
with app.app_context():
    morn_id = ShiftPattern.query.filter_by(code="MORN").first().id
    night_id = ShiftPattern.query.filter_by(code="NIGHT").first().id
    gen_id = ShiftPattern.query.filter_by(code="GEN").first().id
# shift with assignments cannot be deleted
with app.app_context():
    db.session.add(RosterAssignment(user_id="T32-E3", date=monday,
                                   shift_id=night_id, created_by="T32-ADM"))
    db.session.commit()
t = get_token("/hrms/shifts")
r = client.post(f"/hrms/shifts/{night_id}/delete", data={"csrf_token": t},
                follow_redirects=False)
with app.app_context():
    check("shift with assignments is NOT deleted",
          ShiftPattern.query.filter_by(code="NIGHT").first() is not None)
check("delete blocked with error flash", b"cannot be deleted" in
      client.get("/hrms/shifts").data)
r = client.post(f"/hrms/shifts/{morn_id}/delete", data={"csrf_token": t},
                follow_redirects=False)
with app.app_context():
    check("shift without assignments deleted",
          ShiftPattern.query.filter_by(code="MORN").first() is None)

logout()
login("T32-MGR", "pw-T32-MGR")
r = client.get("/hrms/shifts")
check("manager can view shifts", r.status_code == 200)
t = get_token("/hrms/shifts")
r = client.post("/hrms/shifts", data={
    "csrf_token": t, "code": "X1", "name": "X", "start_time": "06:00",
    "end_time": "14:00"}, follow_redirects=False)
check("manager shift create 403", r.status_code == 403)
logout()
login("T32-E1", "pw-T32-E1")
r = client.get("/hrms/shifts")
check("employee shift page 403", r.status_code == 403)
logout()
login("T32-ADM", "pw-T32-ADM")

# ================= bulk assign =================
t = get_token("/hrms/roster")
r = client.post("/hrms/roster/bulk", data={
    "csrf_token": t, "scope": "user:T32-E1", "shift": "GEN",
    "from_date": monday.isoformat(),
    "to_date": (monday + timedelta(days=6)).isoformat(),
    "skip_off": "friday"}, follow_redirects=False)
check("bulk assign redirects", r.status_code in (301, 302, 303))
with app.app_context():
    n = RosterAssignment.query.filter_by(user_id="T32-E1").count()
check("bulk assign created 6 rows (Friday skipped)", n == 6, extra=f"got {n}")
t = get_token("/hrms/roster")
r = client.post("/hrms/roster/bulk", data={
    "csrf_token": t, "scope": "user:T32-E1", "shift": "GEN",
    "from_date": monday.isoformat(),
    "to_date": (monday + timedelta(days=6)).isoformat(),
    "skip_off": "friday"}, follow_redirects=False)
with app.app_context():
    n2 = RosterAssignment.query.filter_by(user_id="T32-E1").count()
check("rerun without overwrite keeps rows", n2 == 6, extra=f"got {n2}")
r = client.post("/hrms/roster/bulk", data={
    "csrf_token": t, "scope": "user:T32-E1", "shift": "GEN",
    "from_date": monday.isoformat(),
    "to_date": (monday + timedelta(days=6)).isoformat(),
    "skip_off": "friday", "overwrite": "on"}, follow_redirects=False)
with app.app_context():
    n3 = RosterAssignment.query.filter_by(user_id="T32-E1").count()
check("rerun with overwrite keeps 6 rows (replaced)", n3 == 6, extra=f"got {n3}")
# fresh NIGHT assignment for grid/variance checks (after the overwrite runs)
# (reuses the delete-block fixture row for T32-E3/Monday if present)
with app.app_context():
    a3 = RosterAssignment.query.filter_by(user_id="T32-E3", date=monday).first()
    if a3 is None:
        a3 = RosterAssignment(user_id="T32-E3", date=monday,
                              shift_id=gen_id, created_by="T32-ADM")
        db.session.add(a3)
    a3.shift_id = night_id
    db.session.commit()

# ================= week grid =================
r = client.get("/hrms/roster")
check("week grid 200", r.status_code == 200)
check("week grid renders GEN code chip", b">GEN<" in r.data)
check("week grid shows night chip style", b"chip-night" in r.data)

# ================= cell assign / clear =================
wed = monday + timedelta(days=2)
r = client.get(f"/hrms/roster/assign?user=T32-E2&date={wed.isoformat()}")
check("cell assign page 200", r.status_code == 200)
t = get_token(f"/hrms/roster/assign?user=T32-E2&date={wed.isoformat()}")
r = client.post("/hrms/roster/assign",
                data={"csrf_token": t, "user": "T32-E2",
                      "date": wed.isoformat(), "shift": "NIGHT"},
                follow_redirects=False)
with app.app_context():
    a = RosterAssignment.query.filter_by(user_id="T32-E2", date=wed).first()
    cell_ok = a is not None and a.shift_id == night_id
check("cell assign sets NIGHT", cell_ok)
r = client.post("/hrms/roster/assign",
                data={"csrf_token": t, "user": "T32-E2",
                      "date": wed.isoformat(), "shift": ""},
                follow_redirects=False)
with app.app_context():
    a = RosterAssignment.query.filter_by(user_id="T32-E2", date=wed).first()
check("cell clear removes assignment", a is None)
# employee write attempts -> 403
logout()
login("T32-E1", "pw-T32-E1")
t_emp = csrf_token(client.get("/change-password"))
r = client.post("/hrms/roster/bulk", data={"csrf_token": t_emp},
                follow_redirects=False)
check("employee bulk assign 403", r.status_code == 403)
r = client.get("/hrms/roster")
check("employee roster grid 403", r.status_code == 403)
logout()
login("T32-ADM", "pw-T32-ADM")

# ================= my-roster =================
logout()
login("T32-E1", "pw-T32-E1")
r = client.get("/my-roster")
check("my-roster 200", r.status_code == 200)
check("my-roster shows own shift", b"GEN" in r.data)
check("my-roster does NOT show another employee's shift code",
      b"NIGHT" not in r.data)
logout()
login("T32-ADM", "pw-T32-ADM")

# ================= variance =================
with app.app_context():
    # T32-E3 rostered this Monday (NIGHT); mark absent -> bucket (a).
    # NOTE: uses T32-E3, not T32-E1, so the settlement fixture for T32-E1
    # below stays free of unexcused absences (Art. 51 unpaid-day exclusion
    # is exercised in test_v321 instead).
    db.session.add(AttendanceRecord(user_id="T32-E3", date=monday, status="absent"))
    # T32-S1 present with no roster assignment -> bucket (b)
    db.session.add(AttendanceRecord(user_id="T32-S1", date=monday, status="present"))
    db.session.commit()
r = client.get(f"/hrms/roster/variance?date={monday.isoformat()}")
check("variance 200", r.status_code == 200)
check("rostered-but-absent employee listed", b"T32-E3" in r.data)
check("present-but-unrostered employee listed", b"T32-S1" in r.data)
check("night-shift rostered employee listed", b"NIGHT" in r.data)

# ================= Ramadan banner =================
with app.app_context():
    s = RosterSettings(id=1)
    s.ramadan_start = monday - timedelta(days=1)
    s.ramadan_end = monday + timedelta(days=8)
    db.session.merge(s)
    db.session.commit()
r = client.get("/hrms/roster")
check("ramadan banner appears when week in Ramadan", b"Ramadan period" in r.data)
with app.app_context():
    s = RosterSettings.query.get(1)
    s.ramadan_start = None
    s.ramadan_end = None
    db.session.commit()
r = client.get("/hrms/roster")
check("ramadan banner absent when dates cleared", b"Ramadan period" not in r.data)

# ================= settings =================
r = client.get("/hrms/roster/settings")
check("settings page 200 for admin", r.status_code == 200)
t = get_token("/hrms/roster/settings")
r = client.post("/hrms/roster/settings", data={
    "csrf_token": t, "ramadan_start": monday.isoformat(),
    "ramadan_end": (monday + timedelta(days=29)).isoformat(),
    "ramadan_daily_hours": "6", "standard_daily_hours": "8",
    "midday_break_start": "12:30", "midday_break_end": "15:00",
    "midday_season_start_md": "06-15", "midday_season_end_md": "09-15"},
    follow_redirects=False)
with app.app_context():
    s = RosterSettings.query.get(1)
    check("settings persist ramadan dates", s.ramadan_start == monday)
    s.ramadan_start = None
    s.ramadan_end = None
    db.session.commit()
logout()
login("T32-E1", "pw-T32-E1")
r = client.get("/hrms/roster/settings")
check("employee settings page 403", r.status_code == 403)
logout()
login("T32-ADM", "pw-T32-ADM")

# ================= manager dept-scoped grid =================
r = client.get("/hrms/roster")
logout()
login("T32-MGR", "pw-T32-MGR")
r = client.get("/hrms/roster")
check("manager roster grid 200", r.status_code == 200)
check("manager sees own-department employee", b"T32 Employee One" in r.data)
check("manager does NOT see other-department employee",
      b"T32 Sales One" not in r.data)
logout()
login("T32-ADM", "pw-T32-ADM")

# ================= settlement fixture =================
LWD = date(2026, 10, 15)
with app.app_context():
    u = User.query.get("T32-E1")
    u.basic_salary = 3000.0
    u.monthly_salary = 4500.0
    u.joining_date = LWD - timedelta(days=2555)  # exactly 7.0 service years
    u.separation_date = date(2026, 9, 30)
    u.separation_type = "Termination"
    db.session.add(LeaveBalance(user_id="T32-E1", leave_type="annual",
                               entitled=30.0, used=10.0))
    db.session.add(Loan(user_id="T32-E1", loan_type="loan", principal=2000.0,
                        installment=800.0, start_period="2026-01",
                        balance=1200.0, status="active", created_by="T32-ADM"))
    db.session.commit()

# create draft settlement A (notice: company_pays)
t = get_token("/hrms/settlements/new")
r = client.post("/hrms/settlements/new", data={
    "csrf_token": t, "user_id": "T32-E1", "last_working_day": LWD.isoformat(),
    "separation_type": "Termination", "notice_days": "30",
    "notice_direction": "company_pays", "reason": "Test"},
    follow_redirects=False)
check("draft settlement created (redirect)", r.status_code in (301, 302, 303))
with app.app_context():
    st = (FinalSettlement.query.filter_by(user_id="T32-E1")
          .order_by(FinalSettlement.id.desc()).first())
    sid = st.id
# manual adjustments
t = get_token(f"/hrms/settlements/{sid}")
r = client.post(f"/hrms/settlements/{sid}/adjustments", data={
    "csrf_token": t, "label": "Transport allowance", "kind": "payable",
    "amount": "500", "note": ""}, follow_redirects=False)
r = client.post(f"/hrms/settlements/{sid}/adjustments", data={
    "csrf_token": t, "label": "Uniform recovery", "kind": "recoverable",
    "amount": "150", "note": ""}, follow_redirects=False)

# calculate -> hand-checked math
r = client.post(f"/hrms/settlements/{sid}/calculate",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid}")},
                follow_redirects=False)
check("calculate redirects", r.status_code in (301, 302, 303))
with app.app_context():
    st = FinalSettlement.query.get(sid)
    exp_grat = uae.gratuity(User.query.get("T32-E1").joining_date,
                            LWD, 3000.0, "employer")["amount"]
check("gratuity matches uae.py reference 16500.00", exp_grat == 16500.0,
      extra=f"got {exp_grat}")
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("settlement status calculated", st.status == "calculated")
    check("7 frozen lines (5 computed + 2 manual)", len(st.lines) == 7,
          extra=f"got {len(st.lines)}")
    kinds = {(l["label"].split("\u2014")[0].strip(), l["kind"], l["amount"])
             for l in st.lines}
    check("payable total 25750.00", st.total_payable == 25750.0,
          extra=f"got {st.total_payable}")
    check("recoverable total 1350.00", st.total_recoverable == 1350.0,
          extra=f"got {st.total_recoverable}")
    check("net 24400.00", st.net == 24400.0, extra=f"got {st.net}")
r = client.get(f"/hrms/settlements/{sid}")
check("statement shows net payable", b"24400.00" in r.data and
      b"Net Payable" in r.data)

# notice direction variant: employee_pays -> recoverable
with app.app_context():
    u2 = User.query.get("T32-E2")
    u2.basic_salary = 3000.0
    u2.monthly_salary = 4500.0
    u2.joining_date = LWD - timedelta(days=2555)
    db.session.commit()
t = get_token("/hrms/settlements/new")
r = client.post("/hrms/settlements/new", data={
    "csrf_token": t, "user_id": "T32-E2", "last_working_day": LWD.isoformat(),
    "separation_type": "Termination", "notice_days": "30",
    "notice_direction": "employee_pays", "reason": ""},
    follow_redirects=False)
with app.app_context():
    st2 = (FinalSettlement.query.filter_by(user_id="T32-E2")
           .order_by(FinalSettlement.id.desc()).first())
    sid2 = st2.id
client.post(f"/hrms/settlements/{sid2}/calculate",
            data={"csrf_token": get_token(f"/hrms/settlements/{sid2}")},
            follow_redirects=False)
with app.app_context():
    st2 = FinalSettlement.query.get(sid2)
    notice_lines = [l for l in st2.lines if "Notice pay" in l["label"]]
    check("employee_pays notice is recoverable",
          len(notice_lines) == 1 and notice_lines[0]["kind"] == "recoverable"
          and notice_lines[0]["amount"] == 4500.0,
          extra=str(notice_lines))

# ================= status flow + SoD =================
# creator (T32-ADM) cannot approve
r = client.post(f"/hrms/settlements/{sid}/approve",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("creator approval blocked (SoD)", st.status == "calculated")
check("SoD message shown", b"Segregation of duties" in
      client.get(f"/hrms/settlements/{sid}").data)
# second admin approves, then mark paid
logout()
login("T32-ADM2", "pw-T32-ADM2")
r = client.post(f"/hrms/settlements/{sid}/approve",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("second admin approves", st.status == "approved"
          and st.approved_by == "T32-ADM2")
# v3.4.1 two-gate design: payment proof at disbursement (gate 1), signed
# MOHRE cancellation / EOS receipt at closure (gate 2). Mark Paid from
# 'approved' is refused — the payment must be disbursed first.
r = client.post(f"/hrms/settlements/{sid}/mark-paid",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("mark paid blocked from approved", st.status == "approved")
# gate 1: mark-disbursed without proof refused
r = client.post(f"/hrms/settlements/{sid}/mark-disbursed",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid}"),
                      "payment_ref": "TT-TEST-1"},
                content_type="multipart/form-data", follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("disbursement blocked without proof", st.status == "approved")
t = get_token(f"/hrms/settlements/{sid}")
r = client.post(f"/hrms/settlements/{sid}/mark-disbursed",
                data={"csrf_token": t, "payment_ref": "TT-TEST-1",
                      "payment_proof": (io.BytesIO(b"%PDF-1.4 payment proof"),
                                        "proof.pdf")},
                content_type="multipart/form-data", follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("disbursed with proof + ref", st.status == "disbursed"
          and st.payment_ref == "TT-TEST-1" and bool(st.payment_doc_path))
# gate 2: mark paid blocked until the signed receipt is uploaded
r = client.post(f"/hrms/settlements/{sid}/mark-paid",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("mark paid blocked without receipt", st.status == "disbursed")
t = get_token(f"/hrms/settlements/{sid}")
r = client.post(f"/hrms/settlements/{sid}/receipt",
                data={"csrf_token": t,
                      "receipt": (io.BytesIO(b"%PDF-1.4 test receipt"),
                                  "receipt.pdf")},
                content_type="multipart/form-data", follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("receipt uploaded", bool(st.cancellation_doc_path))
r = client.post(f"/hrms/settlements/{sid}/mark-paid",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("mark paid sets status + paid_at", st.status == "paid"
          and st.paid_at is not None)
# calculate after approval/paid is blocked
n_lines = len(st.lines)
client.post(f"/hrms/settlements/{sid}/calculate",
            data={"csrf_token": get_token(f"/hrms/settlements/{sid}")},
            follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("recalculate after paid blocked", st.status == "paid"
          and len(st.lines) == n_lines)

# reopen flow: calculated -> draft (admin) -> calculate again
logout()
login("T32-ADM", "pw-T32-ADM")
r = client.post(f"/hrms/settlements/{sid2}/reopen",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid2}")},
                follow_redirects=False)
with app.app_context():
    st2 = FinalSettlement.query.get(sid2)
    check("calculated reopened to draft", st2.status == "draft")
client.post(f"/hrms/settlements/{sid2}/calculate",
            data={"csrf_token": get_token(f"/hrms/settlements/{sid2}")},
            follow_redirects=False)
with app.app_context():
    st2 = FinalSettlement.query.get(sid2)
    check("recalculate from reopened draft works", st2.status == "calculated")

# adjustments only in draft
r = client.post(f"/hrms/settlements/{sid2}/adjustments", data={
    "csrf_token": get_token(f"/hrms/settlements/{sid2}"),
    "label": "Late fee", "kind": "recoverable", "amount": "50"},
    follow_redirects=False)
with app.app_context():
    n_adj = SettlementLine.query.filter_by(settlement_id=sid2,
                                           is_manual=True).count()
check("adjustment blocked after calculate", n_adj == 0, extra=f"got {n_adj}")

# ================= permissions + audit =================
logout()
login("T32-MGR", "pw-T32-MGR")  # manager WITHOUT salary permission
r = client.get("/hrms/settlements")
check("settlements list 403 for manager without salary permission",
      r.status_code == 403)
r = client.get(f"/hrms/settlements/{sid}")
check("settlement detail 403 for manager without salary permission",
      r.status_code == 403)
logout()
login("T32-E1", "pw-T32-E1")
r = client.get("/hrms/settlements")
check("settlements list 403 for employee", r.status_code == 403)
logout()
login("T32-ADM", "pw-T32-ADM")
client.get(f"/hrms/settlements/{sid}")
with app.app_context():
    row = (AuditLog.query.filter_by(action="settlement_viewed",
                                    entity_id=str(sid))
           .order_by(AuditLog.id.desc()).first())
    check("settlement view writes an audit row",
          row is not None and row.actor_id == "T32-ADM")

# ================= separations settlement link =================
with app.app_context():
    db.session.add(SeparationRecord(
        employee_code="T32-E1", employee_name="T32 Employee One",
        department="Production", joining_date=date(2020, 1, 15),
        separation_date=date(2026, 9, 30), separation_type="Termination",
        final_settlement_status="In Process", created_by="T32-ADM"))
    db.session.commit()
r = client.get("/hrms/separations")
check("separations list shows Settlement link for matched user",
      b"Settlement" in r.data and b"/hrms/settlements/new?user_id=T32-E1" in r.data)

# ================= new settlement prefill =================
r = client.get("/hrms/settlements/new?user_id=T32-E1")
check("prefill uses user separation date", b'value="2026-09-30"' in r.data)
check("prefill uses user separation type",
      b'<option value="Termination" selected>' in r.data)

# ================= nav links (v3.6: Frappe workspace model) =================
r = client.get("/")
check("nav has My Roster link", b"My Roster" in r.data)
r = client.get("/ws/shift-attendance")
check("nav has Roster link", b"/hrms/roster" in r.data)
r = client.get("/")
check("nav has Shift Patterns link", b"Shift Patterns" in r.data)
r = client.get("/ws/tenure")
check("nav has Final Settlements link", b"Final Settlements" in r.data)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
