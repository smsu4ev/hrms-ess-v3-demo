"""HRMS+ESS v3.2.1 tests: UAE gratuity law correction + settlement Art. 53
gate + roster warnings.

Federal Decree-Law 33/2021 (in force 2 Feb 2022): the 1980-law resignation
gratuity scale (1/3 for 1-3 yrs, 2/3 for 3-5 yrs) was REPEALED. Art. 51 now
gives FULL gratuity on resignation after >= 1 year of service, like
employer termination; unpaid absence days are excluded from the service
period. Art. 53 requires all entitlements to be paid within 14 days of
contract end.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v321.db && ESS_SECRET_KEY=testsecret12345 \\
     ESS_DATABASE_URL=sqlite:////tmp/test_v321.db python test_v321.py
"""
import io
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import (User, FinalSettlement, SettlementLine, ShiftPattern,
                        RosterAssignment, RosterSettings, LeaveBalance,
                        LeaveRequest, AttendanceRecord, Loan, AuditLog)  # noqa: E402
from app import uae  # noqa: E402
import config as app_config  # noqa: E402

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


gst_today = date.today()
JOIN = date(2020, 1, 15)
LWD = date(2025, 6, 30)

with app.app_context():
    adm = mk("T21-ADM", "T21 Admin", "admin", "Human Resources",
             basic_salary=9000, monthly_salary=14000)
    adm2 = mk("T21-ADM2", "T21 Admin Two", "admin", "Human Resources",
              basic_salary=9000, monthly_salary=14000)
    emp = mk("T21-E1", "T21 Employee One", "employee", "Production",
             designation="Welder", basic_salary=3000, monthly_salary=4500,
             joining_date=JOIN)
    emp2 = mk("T21-E2", "T21 Employee Two", "employee", "Production",
              designation="Welder", basic_salary=3000, monthly_salary=4500,
              joining_date=JOIN)
    db.session.commit()

login("T21-ADM", "pw-T21-ADM")

# ================= 1. gratuity: no resignation reduction (Art. 51) =======
g_res = uae.gratuity(JOIN, LWD, 3000.0, "resignation")
g_emp = uae.gratuity(JOIN, LWD, 3000.0, "employer")
check("resignation == termination (Art. 51: scale repealed)",
      g_res["amount"] == g_emp["amount"] and g_res["multiplier"] == 1.0,
      f"res={g_res['amount']} emp={g_emp['amount']}")

# NOTE: "exactly N years" fixtures use timedelta days (4*365 etc.), not
# calendar dates, because calendar 4-year spans include leap days.
_today = date.today()
g4 = uae.gratuity(_today - timedelta(days=4 * 365), _today, 3000.0, "resignation")
check("resignation exactly 4yrs -> full 8400.0", g4["amount"] == 8400.0,
      f"got {g4['amount']}")

g7 = uae.gratuity(_today - timedelta(days=7 * 365), _today, 3000.0, "resignation",
                 unpaid_days=365)
check("7yrs - 365 unpaid days -> effective 6yrs -> 13500.0",
      g7["amount"] == 13500.0, f"got {g7['amount']}")
check("unpaid_days recorded in breakdown",
      g7["unpaid_days_excluded"] == 365 and g7["effective_service_days"] == 6 * 365)

g0 = uae.gratuity(_today - timedelta(days=2 * 365), _today, 3000.0, "resignation",
                 unpaid_days=500)
check("unpaid days pushing service below 1 year -> 0",
      g0["amount"] == 0.0, f"got {g0['amount']}")

# cap still on basic wage (mainstream practice, unchanged)
gcap = uae.gratuity(date(1990, 1, 1), date(2026, 1, 1), 10000.0, "resignation")
check("2-year basic-wage cap still applies", gcap["amount"] == 240000.0
      and gcap["capped"], f"got {gcap['amount']}")

# ================= 2. settlement: unpaid absence exclusion ==============
with app.app_context():
    # 3 approved unpaid-leave days + 2 unexcused absences within tenure
    db.session.add(LeaveRequest(user_id="T21-E1", leave_type="unpaid",
                               start_date=date(2024, 3, 1),
                               end_date=date(2024, 3, 3), days=3.0,
                               status="approved"))
    for d in (date(2024, 5, 10), date(2024, 5, 11)):
        db.session.add(AttendanceRecord(user_id="T21-E1", date=d,
                                        status="absent"))
    db.session.commit()

t = get_token("/hrms/settlements/new")
r = client.post("/hrms/settlements/new", data={
    "csrf_token": t, "user_id": "T21-E1", "last_working_day": LWD.isoformat(),
    "separation_type": "Resignation", "notice_days": "0",
    "notice_direction": "none", "reason": "unpaid test"},
    follow_redirects=False)
check("draft created for unpaid-absence fixture", r.status_code in (301, 302, 303))
with app.app_context():
    st = (FinalSettlement.query.filter_by(user_id="T21-E1")
          .order_by(FinalSettlement.id.desc()).first())
    sid1 = st.id
r = client.post(f"/hrms/settlements/{sid1}/calculate",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid1}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    labels = [(l["label"], l["kind"]) for l in st.lines]
check("calculate succeeds", st.status == "calculated")
check("unpaid_days_used stored = 5", st.unpaid_days_used == 5,
      f"got {st.unpaid_days_used}")
info_lines = [l for l in st.lines if l["kind"] == "info"
              and "unpaid absence" in l["label"]]
check("unpaid-days info line present",
      len(info_lines) == 1 and "5" in info_lines[0]["label"])
grat_line = [l for l in st.lines
             if l["label"].startswith("End-of-service gratuity")][0]
expected = uae.gratuity(JOIN, LWD, 3000.0, "resignation", unpaid_days=5)["amount"]
check("gratuity line reduced for unpaid days",
      grat_line["amount"] == expected and
      expected < uae.gratuity(JOIN, LWD, 3000.0, "resignation",
                             unpaid_days=0)["amount"],
      f"got {grat_line['amount']} expected {expected}")

# totals exclude info lines (info kind has amount 0 -> neutral by design)
check("totals unaffected by info-line kind",
      st.total_payable == round(sum(l["amount"] for l in st.lines
                                    if l["kind"] == "payable"), 2))

# ================= 3. payment due date (Art. 53) ========================
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    check("payment_due_date = LWD + 14",
          st.payment_due_date == LWD + timedelta(days=14))
r = client.get(f"/hrms/settlements/{sid1}")
check("detail shows Art. 53 due date",
      b"Payable by" in r.data and (LWD + timedelta(days=14)).isoformat().encode() in r.data)
r = client.get("/hrms/settlements")
check("list shows due-date column", b"Payable By" in r.data)

# overdue: settlement with LWD 30 days ago, calculated
LWD_OLD = gst_today - timedelta(days=30)
t = get_token("/hrms/settlements/new")
r = client.post("/hrms/settlements/new", data={
    "csrf_token": t, "user_id": "T21-E2", "last_working_day": LWD_OLD.isoformat(),
    "separation_type": "Resignation", "notice_days": "0",
    "notice_direction": "none"}, follow_redirects=False)
with app.app_context():
    st = (FinalSettlement.query.filter_by(user_id="T21-E2")
          .order_by(FinalSettlement.id.desc()).first())
    sid_old = st.id
client.post(f"/hrms/settlements/{sid_old}/calculate",
            data={"csrf_token": get_token(f"/hrms/settlements/{sid_old}")},
            follow_redirects=False)
r = client.get("/hrms/settlements")
check("OVERDUE badge on list for due-date-passed settlement",
      b"OVERDUE" in r.data)
r = client.get(f"/hrms/settlements/{sid_old}")
check("OVERDUE badge on detail", b"OVERDUE" in r.data)
# future LWD -> due date in the future -> NOT overdue
with app.app_context():
    mk("T21-E4", "T21 Employee Four", "employee", "Production",
       designation="Welder", basic_salary=3000, monthly_salary=4500,
       joining_date=JOIN)
    db.session.commit()
LWD_FUT = gst_today + timedelta(days=10)
t = get_token("/hrms/settlements/new")
r = client.post("/hrms/settlements/new", data={
    "csrf_token": t, "user_id": "T21-E4", "last_working_day": LWD_FUT.isoformat(),
    "separation_type": "Resignation", "notice_days": "0",
    "notice_direction": "none"}, follow_redirects=False)
with app.app_context():
    st = (FinalSettlement.query.filter_by(user_id="T21-E4")
          .order_by(FinalSettlement.id.desc()).first())
    check("future-LWD settlement NOT overdue",
          not st.is_overdue and st.payment_due_date == LWD_FUT + timedelta(days=14))

# ================= 4. two-gate flow: disbursement then Mark Paid =========
# v3.4.1: approved -> disbursed (gate 1: payment proof + ref) ->
# paid (gate 2: signed MOHRE cancellation / EOS receipt).
# approve sid1 as a different admin (SoD)
logout()
login("T21-ADM2", "pw-T21-ADM2")
r = client.post(f"/hrms/settlements/{sid1}/approve",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid1}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    check("second admin approved", st.status == "approved")
# gate 1: mark-paid from approved refused
r = client.post(f"/hrms/settlements/{sid1}/mark-paid",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid1}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    check("mark-paid blocked from approved", st.status == "approved")
# mark-disbursed without proof refused
r = client.post(f"/hrms/settlements/{sid1}/mark-disbursed",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid1}"),
                      "payment_ref": "TT-321"},
                content_type="multipart/form-data", follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    check("disbursement blocked without proof", st.status == "approved")
t = get_token(f"/hrms/settlements/{sid1}")
r = client.post(f"/hrms/settlements/{sid1}/mark-disbursed",
                data={"csrf_token": t, "payment_ref": "TT-321",
                      "payment_proof": (io.BytesIO(b"%PDF-1.4 wps advice"),
                                        "proof.pdf")},
                content_type="multipart/form-data", follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    check("disbursed: proof + ref stored, overdue cleared",
          st.status == "disbursed" and st.payment_ref == "TT-321"
          and bool(st.payment_doc_path) and not st.is_overdue)
r = client.get(f"/hrms/settlements/{sid1}/payment-proof")
check("payment proof viewable by admin (200 pdf)",
      r.status_code == 200 and r.headers.get("Content-Type") == "application/pdf")
# gate 2: mark-paid blocked until the signed receipt is uploaded
r = client.post(f"/hrms/settlements/{sid1}/mark-paid",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid1}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    check("mark-paid blocked without receipt", st.status == "disbursed")
r = client.get(f"/hrms/settlements/{sid1}")
check("block reason flashed", b"signed MOHRE cancellation" in r.data)

t = get_token(f"/hrms/settlements/{sid1}")
r = client.post(f"/hrms/settlements/{sid1}/receipt",
                data={"csrf_token": t,
                      "receipt": (io.BytesIO(b"%PDF-1.4 cancellation receipt"),
                                  "cancel.pdf")},
                content_type="multipart/form-data", follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    check("receipt uploaded + stored", bool(st.cancellation_doc_path))
r = client.get(f"/hrms/settlements/{sid1}/receipt")
check("receipt viewable by admin (200 pdf)",
      r.status_code == 200 and r.headers.get("Content-Type") == "application/pdf")
r = client.post(f"/hrms/settlements/{sid1}/mark-paid",
                data={"csrf_token": get_token(f"/hrms/settlements/{sid1}")},
                follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid1)
    check("mark-paid succeeds after receipt", st.status == "paid"
          and st.paid_at is not None)

# receipt view audited
with app.app_context():
    n = AuditLog.query.filter_by(action="settlement_receipt_viewed").count()
    check("receipt view audited", n >= 1)
logout()
login("T21-E2", "pw-T21-E2")
r = client.get(f"/hrms/settlements/{sid1}/receipt")
check("receipt view 403 for plain employee", r.status_code == 403)
logout()
login("T21-ADM", "pw-T21-ADM")

# invalid upload rejected (token sourced from a page that carries a form;
# the detail page only renders the upload form for approved settlements)
t = get_token("/hrms/settlements/new")
r = client.post(f"/hrms/settlements/{sid_old}/receipt",
                data={"csrf_token": t,
                      "receipt": (io.BytesIO(b"not a document at all"),
                                  "evil.exe")},
                content_type="multipart/form-data", follow_redirects=False)
r = client.get(f"/hrms/settlements/{sid_old}")
check("non-document upload rejected",
      b"must be a valid PDF, JPG or PNG" in r.data)

# ================= 5. negative net -> Recovery required =================
with app.app_context():
    mk("T21-E3", "T21 Employee Three", "employee", "Production",
       designation="Welder", basic_salary=3000, monthly_salary=4500,
       joining_date=JOIN)
    db.session.add(Loan(user_id="T21-E3", loan_type="loan", principal=200000.0,
                        installment=10000.0, start_period="2026-01",
                        balance=150000.0, status="active",
                        created_by="T21-ADM"))
    db.session.commit()
t = get_token("/hrms/settlements/new")
client.post("/hrms/settlements/new", data={
    "csrf_token": t, "user_id": "T21-E3", "last_working_day": LWD.isoformat(),
    "separation_type": "Termination", "notice_days": "0",
    "notice_direction": "none"}, follow_redirects=False)
with app.app_context():
    st = (FinalSettlement.query.filter_by(user_id="T21-E3")
          .order_by(FinalSettlement.id.desc()).first())
    sid3 = st.id
client.post(f"/hrms/settlements/{sid3}/calculate",
            data={"csrf_token": get_token(f"/hrms/settlements/{sid3}")},
            follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid3)
    check("negative net possible", st.net < 0, f"net={st.net}")
r = client.get(f"/hrms/settlements/{sid3}")
check("detail shows Recovery required flag", b"Recovery required" in r.data)
r = client.get("/hrms/settlements")
check("list shows Recovery required flag", b"Recovery required" in r.data)

# ================= 6. repatriation ticket options ========================
for opt, amount, expect_label, expect_kind in [
        ("cash", 750.0, "Repatriation ticket (cash in lieu)", "payable"),
        ("booked", 0.0, "Repatriation ticket: company-booked", "info"),
        ("na", 0.0, "Repatriation ticket: not applicable", "info")]:
    uid = f"T21-T{opt}"
    with app.app_context():
        mk(uid, f"T21 Ticket {opt}", "employee", "Production",
           designation="Welder", basic_salary=3000, monthly_salary=4500,
           joining_date=JOIN)
        db.session.commit()
    t = get_token("/hrms/settlements/new")
    r = client.post("/hrms/settlements/new", data={
        "csrf_token": t, "user_id": uid, "last_working_day": LWD.isoformat(),
        "separation_type": "Termination", "notice_days": "0",
        "notice_direction": "none", "ticket_option": opt,
        "ticket_amount": str(amount)}, follow_redirects=False)
    with app.app_context():
        st = (FinalSettlement.query.filter_by(user_id=uid)
              .order_by(FinalSettlement.id.desc()).first())
        sidt = st.id
    client.post(f"/hrms/settlements/{sidt}/calculate",
                data={"csrf_token": get_token(f"/hrms/settlements/{sidt}")},
                follow_redirects=False)
    with app.app_context():
        st = FinalSettlement.query.get(sidt)
        tick = [l for l in st.lines if l["label"] == expect_label]
    ok = (len(tick) == 1 and tick[0]["kind"] == expect_kind
          and tick[0]["amount"] == (amount if opt == "cash" else 0.0))
    check(f"ticket option '{opt}' -> {expect_kind} line", ok)

r = client.get("/hrms/settlements/new")
check("new settlement form has ticket fields", b"Repatriation Ticket" in r.data)

# ================= 7. roster: no-rest badge + midday warning ============
t = get_token("/hrms/shifts")
r = client.post("/hrms/shifts", data={
    "csrf_token": t, "code": "OUT", "name": "Outdoor Install",
    "start_time": "08:00", "end_time": "17:00", "is_outdoor": "on"},
    follow_redirects=False)
check("outdoor shift pattern created", r.status_code in (301, 302, 303))
r = client.get("/hrms/shifts")
check("shifts list marks outdoor pattern", b"Outdoor" in r.data)

with app.app_context():
    mk("T21-R1", "T21 Rest Seven", "employee", "Production",
       joining_date=date(2024, 1, 1))
    mk("T21-R2", "T21 Rest Six", "employee", "Production",
       joining_date=date(2024, 1, 1))
    gen = ShiftPattern.query.filter_by(code="OUT").first()
    mon = gst_today - timedelta(days=gst_today.weekday())
    for i in range(7):  # 7 consecutive days, no rest
        db.session.add(RosterAssignment(user_id="T21-R1", date=mon + timedelta(days=i),
                                       shift_id=gen.id, created_by="T21-ADM"))
    for i in range(6):  # 6 consecutive days -> no badge
        db.session.add(RosterAssignment(user_id="T21-R2", date=mon + timedelta(days=i),
                                       shift_id=gen.id, created_by="T21-ADM"))
    db.session.commit()
    _mon = mon
r = client.get(f"/hrms/roster?week={_mon.isoformat()}")
check("roster grid 200", r.status_code == 200)
check("No weekly rest badge shown for 7-day run",
      r.data.count(b"No weekly rest") == 1 and b"T21 Rest Seven" in r.data)

# midday warning on cell assign: outdoor pattern + summer date
summer = date(2026, 7, 15)
winter = date(2026, 1, 15)
with app.app_context():
    mk("T21-R3", "T21 Rest Three", "employee", "Production",
       joining_date=date(2024, 1, 1))
    db.session.commit()
# NOTE: the roster page itself shows a "Summer midday break" banner in
# season, so the flash is asserted by its distinctive wording.
t = get_token(f"/hrms/roster/assign?user=T21-R3&date={summer.isoformat()}")
r = client.post("/hrms/roster/assign",
                data={"csrf_token": t, "user": "T21-R3",
                      "date": summer.isoformat(), "shift": "OUT"},
                follow_redirects=True)
check("midday warning flashed for outdoor summer assign",
      b"applies to outdoor work on 2026-07-15" in r.data)
t = get_token(f"/hrms/roster/assign?user=T21-R3&date={winter.isoformat()}")
r = client.post("/hrms/roster/assign",
                data={"csrf_token": t, "user": "T21-R3",
                      "date": winter.isoformat(), "shift": "OUT"},
                follow_redirects=True)
check("no midday warning for winter assign",
      b"applies to outdoor work" not in r.data)
with app.app_context():
    a = RosterAssignment.query.filter_by(user_id="T21-R3", date=summer).first()
    check("outdoor assign still saved (warning does not block)", a is not None)

# non-outdoor pattern on summer date -> no warning
t = get_token("/hrms/shifts")
client.post("/hrms/shifts", data={
    "csrf_token": t, "code": "IN1", "name": "Indoor", "start_time": "08:00",
    "end_time": "17:00"}, follow_redirects=False)
t = get_token(f"/hrms/roster/assign?user=T21-R3&date={summer.isoformat()}")
r = client.post("/hrms/roster/assign",
                data={"csrf_token": t, "user": "T21-R3",
                      "date": summer.isoformat(), "shift": "IN1"},
                follow_redirects=True)
check("no midday warning for indoor pattern",
      b"applies to outdoor work" not in r.data)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
