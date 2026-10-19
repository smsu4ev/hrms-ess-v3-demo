"""HRMS+ESS v3.3.1 tests: ESS extras.

1. Unified inbox (/inbox) + dashboard My Tasks card (approvals, mandatory
   notices, JD acknowledgements, training, own in-flight requests).
2. Expense claims through the approval matrix: submit, 2-step approval,
   final approval creates an EARNING PayrollInput consumed by the payroll
   calculation; rejection path; receipt validation; matrix UI lists expense.
3. People directory: search, privacy (no phone/IBAN rendered), inactive
   users absent.
4. Org chart nesting from reporting_manager links.
5. Letter requests: lifecycle, salary-permission gating, NOC purpose merge,
   owner-only download, mark received.
6. Team leave calendar: department scoping, approved-only.
7. Team overview (/team-overview): manager scope, admin scope, 403 for
   employees, absent detail scoping, dashboard card.
8. Notification tabs: Important vs Events, per-tab counts, per-tab
   mark-all-read, backfill.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v331.db && ESS_SECRET_KEY=testsecret12345 \
ESS_DATABASE_URL=sqlite:////tmp/test_v331.db python test_v331.py
"""
import io
import re
import sys
import os
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app import approvals  # noqa: E402
from app.models import (User, LeaveBalance, LeaveRequest, AttendanceRecord,
                        ApprovalStep, ApprovalInstance, ExpenseClaim,
                        LetterRequest, LetterTemplate, GeneratedLetter,
                        PayrollInput, Notice, NoticeAck, JobDescription,
                        JDAcknowledgement, TrainingCourse, TrainingAssignment,
                        TrainingAttempt, Notification)  # noqa: E402
import config as app_config  # noqa: E402

app = create_app()
app.config["WTF_CSRF_ENABLED"] = True
from app import limiter  # noqa: E402
limiter.enabled = False
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


def mk(uid, name, role, dept=None, **kw):
    if not User.query.get(uid):
        kw.setdefault("joining_date", date(2020, 1, 15))
        kw.setdefault("employment_status", "active")
        u = User(id=uid, name=name, role=role, department=dept, **kw)
        u.set_password("pw-" + uid)
        u.must_change_password = False
        db.session.add(u)
    return User.query.get(uid)


def post(path, data, files=False, token_path=None):
    # POST-only routes have no GET page for the CSRF token; fall back to
    # token_path (e.g. "/approvals") in that case.
    t = get_token(token_path or path) or get_token("/dashboard")
    data = dict(data)
    data["csrf_token"] = t
    return client.post(path, data=data, follow_redirects=False,
                       content_type="multipart/form-data" if files else None)


def set_chain(process_key, types):
    m = approvals.get_matrix(process_key)
    ApprovalStep.query.filter_by(matrix_id=m.id).delete()
    for i, atype in enumerate(types, start=1):
        db.session.add(ApprovalStep(matrix_id=m.id, step_no=i,
                                    approver_type=atype))
    db.session.commit()


PDF = b"%PDF-1.4\n%EOF"

with app.app_context():
    # ---------- fixtures ----------
    mk("A1", "Admin One", "admin", dept="Human Resources")
    mk("M1", "Mgr One", "manager", dept="Dept One")
    mk("H1", "Hr One", "employee", dept="Human Resources", reporting_manager="A1",
       is_hr_staff=True)
    mk("E1", "Emp One", "employee", dept="Dept One", reporting_manager="M1",
       phone="+971501234567", iban="AE070331234567890123456",
       basic_salary=3000.0)
    mk("E2", "Emp Two", "employee", dept="Dept Two", reporting_manager="M1",
       basic_salary=2800.0)
    mk("C1", "Clean User", "employee", dept="Dept One", reporting_manager="M1")
    mk("X1", "Ex Staff", "employee", dept="Dept One", is_active=False,
       employment_status="separated")
    mk("X2", "Absent Two", "employee", dept="Dept Two", reporting_manager="M1")
    db.session.commit()
    # C1 joined last month (team-overview fixture)
    today = date.today()
    first_this = today.replace(day=1)
    prev_first = (first_this - timedelta(days=1)).replace(day=1)
    c1 = User.query.get("C1")
    c1.joining_date = prev_first
    db.session.add(LeaveBalance(user_id="E1", leave_type="annual",
                                entitled=30, used=0))
    db.session.add(LeaveBalance(user_id="E2", leave_type="annual",
                                entitled=30, used=0))
    # letter templates needed for the letter-request tests (Employment
    # Certificate + NOC are auto-added by startup; add Salary Certificate)
    db.session.add(LetterTemplate(
        name="Salary Certificate",
        body="Cert for {{employee_name}}: AED {{basic_salary}}.",
        updated_by="A1"))
    # mandatory notice for Dept Two only (E2 sees it, C1 does not)
    db.session.add(Notice(title="Dept Two Safety Brief", body="Read this.",
                          audience="Dept Two", requires_ack=True,
                          created_by="A1"))
    # JD assigned to E2, unacknowledged
    jd = JobDescription(title="Test JD", department="Dept Two",
                        body="Do the things.", version=1, is_active=True,
                        created_by="A1")
    db.session.add(jd)
    db.session.flush()
    db.session.add(JDAcknowledgement(jd_id=jd.id, user_id="E2",
                                    status="assigned", jd_version=1,
                                    assigned_by="A1"))
    # training assigned to E2, not passed
    course = TrainingCourse(title="Test Course", category="Basic",
                            created_by="A1")
    db.session.add(course)
    db.session.flush()
    db.session.add(TrainingAssignment(course_id=course.id, user_id="E2",
                                      assigned_by="A1"))
    db.session.commit()
    # attendance today (Asia/Dubai): E1 absent, X2 absent, others present
    dxb = (datetime.utcnow() + timedelta(hours=4)).date()
    for uid, st in [("E1", "absent"), ("X2", "absent"), ("E2", "present"),
                    ("C1", "present"), ("M1", "present")]:
        db.session.add(AttendanceRecord(user_id=uid, date=dxb, status=st))
    # E2 on approved annual leave covering today (Dept Two vacation)
    db.session.add(LeaveRequest(user_id="E2", leave_type="annual",
                                start_date=dxb, end_date=dxb, days=1.0,
                                status="approved"))
    # E1 approved leave today (visible in own-dept calendar) + a pending
    # one (must stay hidden); E2 approved leave today (other dept).
    db.session.add(LeaveRequest(user_id="E1", leave_type="annual",
                                start_date=dxb, end_date=dxb, days=1.0,
                                status="approved"))
    db.session.add(LeaveRequest(user_id="E1", leave_type="annual",
                                start_date=dxb, end_date=dxb, days=1.0,
                                status="pending"))
    db.session.commit()
    set_chain("expense", ["reporting_manager", "hr_staff"])

    # ================= 1) unified inbox =================
    login("E1", "pw-E1")
    r = post("/my-expenses", {"claim_date": today.isoformat(),
                              "category": "Fuel", "amount": "250.75",
                              "description": "test fuel",
                              "receipt": (io.BytesIO(PDF), "receipt.pdf")},
             files=True)
    check("expense submit 302", r.status_code == 302, r.status_code)
    with app.app_context():
        claim1 = ExpenseClaim.query.filter_by(user_id="E1").order_by(
            ExpenseClaim.id.desc()).first()
    check("expense pending", claim1 is not None and claim1.status == "pending")
    check("expense receipt stored", bool(claim1.receipt_path))
    logout()

    login("M1", "pw-M1")
    r = client.get("/inbox")
    check("inbox 200 for manager", r.status_code == 200, r.status_code)
    check("inbox shows 1 approval",
          b"Approvals awaiting my decision (1)" in r.data)
    check("inbox approval links to /approvals", b"/approvals" in r.data)
    r = client.get("/")
    check("dashboard My Tasks card", b"My tasks" in r.data)
    r = client.get("/approvals")
    check("approvals page shows expense item",
          b"Expense Claims Awaiting Your Action" in r.data
          and str(claim1.id).encode() in r.data)
    logout()

    login("E2", "pw-E2")
    r = client.get("/inbox")
    check("inbox shows unack notice",
          b"Mandatory notices (1)" in r.data and b"Dept Two Safety Brief" in r.data)
    check("inbox shows pending JD",
          b"Job descriptions to acknowledge (1)" in r.data)
    check("inbox shows pending training",
          b"Training to complete (1)" in r.data)
    logout()

    login("C1", "pw-C1")
    r = client.get("/inbox")
    check("inbox empty approvals", b"Nothing awaiting your decision." in r.data)
    check("inbox empty notices", b"No mandatory notices pending." in r.data)
    check("inbox empty JDs", b"All your JDs are acknowledged." in r.data)
    check("inbox empty training", b"No training outstanding." in r.data)
    logout()

    # ================= 2) expense approval chain =================
    login("M1", "pw-M1")
    r = post(f"/expenses/{claim1.id}", {"action": "approve",
                                        "comment": "ok step1"},
             token_path="/approvals")
    check("step1 approve redirects", r.status_code == 302, r.status_code)
    logout()
    with app.app_context():
        c = ExpenseClaim.query.get(claim1.id)
        inst = approvals.get_instance("expense", c.id)
    check("still pending after step 1",
          c.status == "pending" and inst.current_step == 2,
          f"{c.status} step {inst.current_step}")
    check("no payroll input yet", c.payroll_input_id is None)

    login("H1", "pw-H1")  # hr_staff = step 2
    r = post(f"/expenses/{claim1.id}", {"action": "approve",
                                        "comment": "final ok"},
             token_path="/approvals")
    check("step2 approve redirects", r.status_code == 302, r.status_code)
    logout()
    with app.app_context():
        c = ExpenseClaim.query.get(claim1.id)
    check("approved after step 2", c.status == "approved", c.status)
    period = today.strftime("%Y-%m")
    with app.app_context():
        inp = PayrollInput.query.get(c.payroll_input_id)
    check("payroll input created",
          inp is not None and inp.amount == 250.75 and inp.period == period
          and inp.kind == "addition",
          f"{inp.amount if inp else None} {inp.period if inp else None}")
    check("payroll input label",
          inp is not None and f"claim #{c.id}" in inp.label, inp.label if inp else "")
    with app.app_context():
        from app.payroll import calculate_payroll_line
        line = calculate_payroll_line(User.query.get("E1"), period)
    earn = [e for e in line["earnings"]
            if abs(e["amount"] - 250.75) < 0.005
            and "Expense reimbursement" in e["name"]]
    check("payroll calc includes expense earning", len(earn) == 1,
          str([e["name"] for e in line["earnings"]]))
    with app.app_context():
        n = Notification.query.filter_by(user_id="E1", kind="expense").first()
    check("claimant notified of approval", n is not None)

    # rejection path
    login("E1", "pw-E1")
    r = post("/my-expenses", {"claim_date": today.isoformat(),
                              "category": "Food", "amount": "100.00",
                              "description": "reject me"}, files=True)
    with app.app_context():
        claim2 = ExpenseClaim.query.filter_by(
            user_id="E1", amount=100.0).order_by(
            ExpenseClaim.id.desc()).first()
    logout()
    login("M1", "pw-M1")
    r = post(f"/expenses/{claim2.id}", {"action": "reject",
                                        "comment": "not eligible"},
             token_path="/approvals")
    logout()
    with app.app_context():
        c2 = ExpenseClaim.query.get(claim2.id)
    check("rejected status+comment",
          c2.status == "rejected" and c2.decision_comment == "not eligible",
          f"{c2.status}/{c2.decision_comment}")
    with app.app_context():
        noinp = PayrollInput.query.filter(
            PayrollInput.label.like(f"%claim #{c2.id}%")).first()
    check("no payroll input on rejection", noinp is None)

    # validation: bad magic bytes
    login("E1", "pw-E1")
    with app.app_context():
        before = ExpenseClaim.query.filter_by(user_id="E1").count()
    r = post("/my-expenses", {"claim_date": today.isoformat(),
                              "category": "Fuel", "amount": "50.00",
                              "description": "bad file",
                              "receipt": (io.BytesIO(b"GARBAGE-DATA"),
                                          "receipt.pdf")}, files=True)
    with app.app_context():
        after = ExpenseClaim.query.filter_by(user_id="E1").count()
    check("bad receipt rejected, no claim created",
          before == after and b"PDF, JPG or PNG" in r.data,
          f"{before}->{after}")
    # validation: amount <= 0
    r = post("/my-expenses", {"claim_date": today.isoformat(),
                              "category": "Fuel", "amount": "0",
                              "description": "zero"}, files=True)
    check("zero amount rejected", b"greater than zero" in r.data)
    logout()

    # matrix UI lists the expense process
    login("A1", "pw-A1")
    r = client.get("/hrms/approval-matrix")
    check("matrix UI lists Expense Claims",
          r.status_code == 200 and b"Expense Claims" in r.data,
          r.status_code)
    logout()

    # ================= 3) directory =================
    login("E1", "pw-E1")
    r = client.get("/directory")
    html = r.data
    check("directory 200", r.status_code == 200, r.status_code)
    check("directory lists active staff", b"Emp One" in html and b"Emp Two" in html)
    check("directory hides phone", b"+971501234567" not in html)
    check("directory hides IBAN", b"AE070331234567890123456" not in html)
    check("directory hides inactive", b"Ex Staff" not in html)
    r = client.get("/directory?q=Emp+Two")
    check("directory search filters",
          b"Emp Two" in r.data
          and b"<div><strong>Emp One</strong></div>" not in r.data)
    logout()

    # ================= 4) org chart =================
    login("E1", "pw-E1")
    r = client.get("/org-chart")
    check("org chart 200", r.status_code == 200, r.status_code)
    m = re.search(rb"Mgr One</strong>.*?<ul>.*?Emp One", r.data, re.S)
    check("org chart nests report under manager", m is not None)
    logout()

    # ================= 5) letter requests =================
    login("E1", "pw-E1")
    r = post("/my-letters", {"letter_type": "employment_certificate",
                             "purpose": "Bank account"})
    with app.app_context():
        lr1 = LetterRequest.query.filter_by(
            user_id="E1", letter_type="employment_certificate").order_by(
            LetterRequest.id.desc()).first()
    check("letter request submitted",
          lr1 is not None and lr1.status == "submitted")
    logout()

    login("H1", "pw-H1")  # HR staff, no salary permission
    r = post("/hrms/letter-requests",
             {"action": "start", "request_id": str(lr1.id),
              "hr_comment": "working on it"})
    with app.app_context():
        lr1 = LetterRequest.query.get(lr1.id)
    check("letter started", lr1.status == "in_progress", lr1.status)
    r = post("/hrms/letter-requests",
             {"action": "generate", "request_id": str(lr1.id)})
    with app.app_context():
        lr1 = LetterRequest.query.get(lr1.id)
        gl = GeneratedLetter.query.get(lr1.generated_letter_id) \
            if lr1.generated_letter_id else None
    check("employment cert generated+linked",
          lr1.status == "ready" and gl is not None, lr1.status)
    check("generated text merged",
          gl is not None and "Emp One" in gl.rendered_text)
    check("non-salary letter flag", gl is not None and not gl.has_salary_data)
    logout()

    login("E1", "pw-E1")
    r = client.get(f"/my-letters/{lr1.id}/download")
    check("owner downloads 200", r.status_code == 200, r.status_code)
    logout()
    login("E2", "pw-E2")
    r = client.get(f"/my-letters/{lr1.id}/download")
    check("other employee 403", r.status_code == 403, r.status_code)
    logout()
    login("E1", "pw-E1")
    r = post(f"/my-letters/{lr1.id}/received", {}, token_path="/my-letters")
    with app.app_context():
        lr1 = LetterRequest.query.get(lr1.id)
    check("marked delivered", lr1.status == "delivered", lr1.status)
    logout()

    # salary-permission gating
    login("E2", "pw-E2")
    r = post("/my-letters", {"letter_type": "salary_certificate",
                             "purpose": "Bank"})
    with app.app_context():
        lr2 = LetterRequest.query.filter_by(
            user_id="E2", letter_type="salary_certificate").order_by(
            LetterRequest.id.desc()).first()
    logout()
    login("H1", "pw-H1")
    r = post("/hrms/letter-requests",
             {"action": "generate", "request_id": str(lr2.id)})
    with app.app_context():
        lr2 = LetterRequest.query.get(lr2.id)
    check("salary letter refused without permission",
          lr2.status != "ready" and lr2.generated_letter_id is None,
          lr2.status)
    logout()
    login("A1", "pw-A1")
    r = post("/hrms/letter-requests",
             {"action": "generate", "request_id": str(lr2.id)})
    with app.app_context():
        lr2 = LetterRequest.query.get(lr2.id)
        gl2 = GeneratedLetter.query.get(lr2.generated_letter_id) \
            if lr2.generated_letter_id else None
    check("admin generates salary letter",
          lr2.status == "ready" and gl2 is not None and gl2.has_salary_data,
          lr2.status)
    logout()

    # NOC uses the purpose text
    login("E1", "pw-E1")
    r = post("/my-letters", {"letter_type": "noc",
                             "purpose": "School admission for child"})
    with app.app_context():
        lr3 = LetterRequest.query.filter_by(
            user_id="E1", letter_type="noc").order_by(
            LetterRequest.id.desc()).first()
    logout()
    login("A1", "pw-A1")
    r = post("/hrms/letter-requests",
             {"action": "generate", "request_id": str(lr3.id)})
    with app.app_context():
        lr3 = LetterRequest.query.get(lr3.id)
        gl3 = GeneratedLetter.query.get(lr3.generated_letter_id) \
            if lr3.generated_letter_id else None
    check("NOC merges purpose text",
          gl3 is not None and "School admission for child" in gl3.rendered_text)
    logout()

    # ================= 6) team calendar =================
    login("E1", "pw-E1")
    r = client.get(f"/team-calendar?month={today.strftime('%Y-%m')}")
    check("calendar 200", r.status_code == 200, r.status_code)
    check("calendar shows own dept approved leave",
          b"<td>Emp One (E1)</td>" in r.data)
    check("calendar hides other dept", b"Emp Two" not in r.data)
    check("calendar hides pending leave",
          r.data.count(b"<td>Emp One (E1)</td>") == 1,
          r.data.count(b"<td>Emp One (E1)</td>"))
    logout()
    login("A1", "pw-A1")
    r = client.get(f"/team-calendar?month={today.strftime('%Y-%m')}")
    check("admin sees all departments",
          b"<td>Emp One (E1)</td>" in r.data and b"Emp Two" in r.data)
    r = client.get(f"/team-calendar?month={today.strftime('%Y-%m')}&dept=Dept+Two")
    check("admin dept filter",
          b"Emp Two" in r.data and b"Emp One (E1)" not in r.data)
    logout()

    # ================= 7) team overview =================
    login("M1", "pw-M1")
    r = client.get("/team-overview")
    check("overview 200 for manager", r.status_code == 200, r.status_code)
    check("manager sees own + reports depts",
          b"Dept One" in r.data and b"Dept Two" in r.data)
    check("manager does not see HR dept",
          b"Human Resources" not in r.data)
    # Dept One row: staff M1,E1,C1 = 3; absent E1 = 1; vacation E1 = 1;
    # joined last month C1 = 1
    m = re.search(rb"Dept One</td>\s*<td>(\d+)</td>\s*<td>.*?(\d+).*?</td>\s*"
                  rb"<td>(\d+)</td>\s*<td>(\d+)</td>", r.data, re.S)
    got = tuple(m.groups()) if m else None
    check("manager dept row numbers", got == (b"3", b"1", b"1", b"1"),
          str(got))
    logout()

    login("A1", "pw-A1")
    r = client.get("/team-overview")
    check("admin sees all departments",
          b"Dept One" in r.data and b"Dept Two" in r.data
          and b"Human Resources" in r.data)
    check("totals row present", b">Total<" in r.data)
    logout()

    login("E1", "pw-E1")
    check("employee 403 on overview",
          client.get("/team-overview").status_code == 403)
    check("employee 403 on absent list",
          client.get("/team-overview/absent").status_code == 403)
    logout()

    login("M1", "pw-M1")
    r = client.get("/team-overview/absent?dept=Dept+One")
    check("absent list scoped to dept",
          r.status_code == 200 and b"Emp One" in r.data
          and b"Absent Two" not in r.data, r.status_code)
    check("absent list 403 outside scope",
          client.get("/team-overview/absent?dept=Human+Resources").status_code
          == 403)
    r = client.get("/")
    # v3.8: the manager "Team present today" KPI number carries the animated
    # count-up (data-count); caption capitalisation changed from v3.7's
    # "Present today".
    check("dashboard dept card",
          b"Team present today" in r.data and b"data-count" in r.data)
    logout()

    # ================= 8) notification tabs =================
    from app.engagement import notify_user, backfill_notification_categories
    with app.app_context():
        notify_user("E1", "birthday", "Birthday alert",
                    link="/celebrations", category="event")
        notify_user("E1", "notice", "Notice alert",
                    link="/notifications")  # default important
        notify_user("E1", "approval", "Approval alert", link="/approvals")
        # simulate pre-feature rows stored as 'important' (created before
        # the celebration classification existed)
        db.session.add(Notification(user_id="E1", kind="wish",
                                    title="Old wish", category="important"))
        db.session.add(Notification(user_id="E1", kind="notice",
                                    title="Old notice", category="important"))
        db.session.commit()
        backfill_notification_categories()
        cats = {n.title: n.category for n in Notification.query.filter(
            Notification.user_id == "E1",
            Notification.title.in_(["Old wish", "Old notice"])).all()}
    check("backfill wish -> event", cats.get("Old wish") == "event", str(cats))
    check("backfill notice -> important",
          cats.get("Old notice") == "important", str(cats))
    login("E1", "pw-E1")
    r = client.get("/notifications")
    # v3.6: the desk shell's slide-over notifications panel also renders all
    # recent notifications, so scope the tab checks to the tab-filtered list
    # (matching-div extraction; a naive split would swallow the panel too).
    def tab_list(resp):
        html = resp.data
        start = html.find(b'<div class="notif-list">')
        if start < 0:
            return b""
        pos = start + len(b'<div class="notif-list">')
        depth = 1
        for m in re.finditer(rb"</?div\b[^>]*>", html[pos:]):
            tag = m.group(0)
            depth += -1 if tag.startswith(b"</") else 1
            if depth == 0:
                return html[pos:pos + m.start()]
        return b""
    check("default tab is Important",
          b"Birthday alert" not in tab_list(r) and b"Notice alert" in tab_list(r))
    check("important unread badge", b"Important Updates" in r.data)
    r = client.get("/notifications?tab=event")
    check("events tab shows celebration",
          b"Birthday alert" in tab_list(r) and b"Notice alert" not in tab_list(r))
    r = post("/notifications/read-all", {"tab": "event"}, token_path="/notifications")
    with app.app_context():
        ev_unread = Notification.query.filter_by(
            user_id="E1", category="event", is_read=False).count()
        imp_unread = Notification.query.filter_by(
            user_id="E1", category="important", is_read=False).count()
    check("mark-all-read on Events only",
          ev_unread == 0 and imp_unread > 0,
          f"events={ev_unread} important={imp_unread}")
    logout()

print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
