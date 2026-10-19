"""HRMS+ESS v3.4.1 tests: Gemini-review fix round.

1. Approval engine hardening: self-approval invariant (subject/requester can
   never act, even via the admin backstop); consecutive-step rule (same actor
   denied when another resolver exists, allowed with a recorded note as sole
   resolver); final step never auto-skipped -> instance halts, only a
   non-subject admin may decide; approval delegations (window + audit).
2. Leave double-spend guard: pending requests count against the balance at
   submission; balance re-validated at the final approval step.
3. Settlement disbursement split: approved -> disbursed (payment proof +
   reference) -> paid (signed receipt); mark-paid from approved refused;
   disbursement stops the Art. 53 overdue clock.
4. Unpaid-exclusion refinement: absent days inside approved non-unpaid leave
   are not counted; no double counting inside unpaid periods.
5. Punch hardening: low-accuracy flag (still accepted in-fence); generic
   rejection message beyond 2 km; legal hold pauses the 45-day purge.
6. JD tamper evidence: sha256 hash of the acknowledged text + user agent.
7. Celebration opt-out: share_celebrations=False -> no post for that user.
8. Quiz shuffle: option/question order shuffled server-side; grading uses
   original option indices and matches the unshuffled result.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v341.db && ESS_SECRET_KEY=testsecret12345 \
ESS_DATABASE_URL=sqlite:////tmp/test_v341.db <venv>/bin/python test_v341.py
"""
import base64
import hashlib
import io
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app import approvals  # noqa: E402
from app.models import (User, LeaveBalance, LeaveRequest, AttendanceRecord,
                        ApprovalMatrix, ApprovalStep, ApprovalInstance,
                        ApprovalAction, ApprovalDelegation, AuditLog,
                        FinalSettlement, PunchRecord, WorkLocation,
                        JobDescription, JDAcknowledgement,
                        TrainingCourse, QuizQuestion, TrainingAssignment,
                        CelebrationPost, Notification)  # noqa: E402
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


def mk(uid, name, role, dept=None, **kw):
    if not User.query.get(uid):
        kw.setdefault("joining_date", date(2020, 1, 15))
        u = User(id=uid, name=name, role=role, department=dept,
                 employment_status="active", is_active=True, **kw)
        u.set_password("pw-" + uid)
        u.must_change_password = False
        db.session.add(u)
    return User.query.get(uid)


def post(path, data, files=False):
    t = get_token(path)
    data = dict(data)
    data["csrf_token"] = t
    return client.post(path, data=data, follow_redirects=False,
                       content_type="multipart/form-data" if files else None)


def set_chain(process_key, types):
    """Replace a matrix chain directly (test setup helper)."""
    m = approvals.get_matrix(process_key)
    ApprovalStep.query.filter_by(matrix_id=m.id).delete()
    for i, atype in enumerate(types, start=1):
        uid = atype[1] if isinstance(atype, tuple) else None
        a = atype[0] if isinstance(atype, tuple) else atype
        db.session.add(ApprovalStep(matrix_id=m.id, step_no=i,
                                    approver_type=a, approver_user_id=uid))
    db.session.commit()


def png_bytes(w=100, h=100, color="red"):
    from PIL import Image
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def apply_leave(uid, ltype, start, end):
    """Apply for leave as `uid` via the ESS endpoint; returns req id or None."""
    logout()
    login(uid, "pw-" + uid)
    r = post("/leave", {"leave_type": ltype,
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                        "reason": "test"}, files=False)
    with app.app_context():
        req = (LeaveRequest.query.filter_by(
            user_id=uid, leave_type=ltype,
            start_date=start, end_date=end)
            .order_by(LeaveRequest.id.desc()).first())
    return req.id if req else None


def decide(req_id, actor_uid, action, comment=""):
    logout()
    login(actor_uid, "pw-" + actor_uid)
    t = get_token("/approvals")  # token from the approvals page (per-request forms)
    return client.post(f"/approvals/{req_id}",
                       data={"csrf_token": t, "action": action,
                             "comment": comment},
                       follow_redirects=False)


TODAY = date.today()
FUT = TODAY + timedelta(days=30)


def _fixtures():
    adm = mk("T41-ADM", "T41 Admin", "admin", "Human Resources")
    adm2 = mk("T41-ADM2", "T41 Admin Two", "admin", "Human Resources")
    mgr1 = mk("T41-MGR1", "T41 Manager One", "manager", "Production")
    e1 = mk("T41-E1", "T41 Employee One", "employee", "Production",
            reporting_manager="T41-MGR1")
    e2 = mk("T41-E2", "T41 Employee Two", "employee", "Production",
            reporting_manager="T41-MGR1")
    hrs = mk("T41-HRS", "T41 HR Staff", "employee", "Human Resources",
             is_hr_staff=True)
    hrm = mk("T41-HRM", "T41 HR Manager", "employee", "Human Resources",
             is_hr_manager=True)
    delg = mk("T41-DEL", "T41 Delegate", "employee", "Human Resources")
    se = mk("T41-SE", "T41 Settlement Emp", "employee", "Production",
            joining_date=date(2018, 1, 1), basic_salary=4000.0,
            monthly_salary=6000.0)
    db.session.add(LeaveBalance(user_id="T41-E1", leave_type="annual",
                                entitled=30, used=0))
    db.session.add(LeaveBalance(user_id="T41-E2", leave_type="annual",
                                entitled=10, used=0))
    db.session.add(LeaveBalance(user_id="T41-E4", leave_type="annual",
                                entitled=30, used=0))
    db.session.commit()


with app.app_context():
    _fixtures()


def set_chain_ctx(process_key, types):
    with app.app_context():
        set_chain(process_key, types)


# ================= 1a) self-approval: subject cannot act at an hr_staff step =
# T41-E1 is both the leave subject and (flagged) HR staff.
with app.app_context():
    u = User.query.get("T41-E1")
    u.is_hr_staff = True
    db.session.commit()
set_chain_ctx("leave", ["hr_staff"])
rid = apply_leave("T41-E1", "annual", FUT, FUT + timedelta(days=2))
check("leave applied for self-approval test", rid is not None)
r = decide(rid, "T41-E1", "approve")
check("self-approval flash shown",
      b"You cannot approve your own request." in
      client.get("/approvals").data)
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    check("self-approval blocked: instance unchanged",
          inst.status == "pending" and inst.current_step == 1
          and inst.actions.filter_by(action="approve").count() == 0)
    # restore flag
    User.query.get("T41-E1").is_hr_staff = False
    db.session.commit()

# ================= 1a cont) creator cannot approve own employee_creation =====
set_chain_ctx("employee_creation", ["hr_manager"])
logout()
login("T41-HRS", "pw-T41-HRS")
t = get_token("/hrms/employees/new")
r = client.post("/hrms/employees/new",
                data={"csrf_token": t, "employee_id": "T41-NEW",
                      "name": "T41 Newbie",
                      "department": "Production", "designation": "Helper",
                      "employment_status": "probation"},
                follow_redirects=False)
with app.app_context():
    nu = User.query.get("T41-NEW")
    check("hr_staff creation -> pending_approval",
          nu is not None and nu.employment_status == "pending_approval"
          and not nu.is_active)
r = post("/hrms/employees/T41-NEW/review",
         {"action": "approve", "comment": ""})
check("creator self-approval flash",
      b"You cannot approve your own request." in
      client.get("/hrms/employees/T41-NEW/review").data)
with app.app_context():
    check("creator approval blocked: record still pending",
          User.query.get("T41-NEW").employment_status == "pending_approval")
    # HR Manager (non-requester) can still approve it
    inst = approvals.get_instance("employee_creation", "T41-NEW")
    check("hr manager may act on it",
          approvals.can_act(inst, User.query.get("T41-HRM")))
logout()
login("T41-HRM", "pw-T41-HRM")
r = post("/hrms/employees/T41-NEW/review",
         {"action": "approve", "comment": "ok"})
with app.app_context():
    check("hr manager approval activates employee",
          User.query.get("T41-NEW").is_active)

# ================= 1b) consecutive-step rule ===============================
# T41-MGR1 is both the reporting manager and a flagged HR Manager.
with app.app_context():
    User.query.get("T41-MGR1").is_hr_manager = True
    db.session.commit()
# chain: specific_user(T41-MGR1) -> hr_manager (flagged: T41-MGR1 + T41-HRM).
set_chain_ctx("leave", [("specific_user", "T41-MGR1"), "hr_manager"])
rid = apply_leave("T41-E1", "annual", FUT + timedelta(days=10),
                  FUT + timedelta(days=11))
decide(rid, "T41-MGR1", "approve")  # step 1 (specific_user)
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    check("step 1 advanced", inst.current_step == 2)
logout()
login("T41-MGR1", "pw-T41-MGR1")
t = get_token("/approvals")
r = client.post(f"/approvals/{rid}",
                data={"csrf_token": t, "action": "approve", "comment": ""},
                follow_redirects=False)
check("consecutive-step denied (403)", r.status_code == 403)
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    check("denied action not recorded",
          inst.status == "pending" and inst.current_step == 2
          and inst.actions.filter_by(action="approve").count() == 1)
r = decide(rid, "T41-HRM", "approve")  # a different approver
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    check("other approver completes chain", inst.status == "approved")

# sole-resolver consecutive: chain reporting_manager -> specific_user(T41-MGR1).
# T41-MGR1 resolves step 1 (as reporting manager) and is the ONLY resolver
# of step 2 -> allowed, with the recorded note.
set_chain_ctx("leave", ["reporting_manager", ("specific_user", "T41-MGR1")])
rid = apply_leave("T41-E1", "annual", FUT + timedelta(days=20),
                  FUT + timedelta(days=21))
decide(rid, "T41-MGR1", "approve")  # step 1
r = decide(rid, "T41-MGR1", "approve")  # step 2, sole resolver
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    acts = inst.actions.order_by(ApprovalAction.id).all()
    check("sole-resolver consecutive allowed",
          inst.status == "approved"
          and any("[sole resolver" in (a.comment or "") for a in acts))
# restore T41-MGR1 flag
with app.app_context():
    User.query.get("T41-MGR1").is_hr_manager = False
    db.session.commit()

# ================= 1c) final step never skipped -> halt ====================
# T41-E5 has no reporting manager and no department.
with app.app_context():
    mk("T41-E5", "T41 Employee Five", "employee", None)
    db.session.add(LeaveBalance(user_id="T41-E5", leave_type="annual",
                                entitled=30, used=0))
    db.session.commit()
set_chain_ctx("leave", ["reporting_manager", ("specific_user", "NOUSER")])
rid = apply_leave("T41-E5", "annual", FUT + timedelta(days=30),
                  FUT + timedelta(days=31))
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    check("final step halted (not skipped)",
          inst.halted is True and inst.status == "pending"
          and inst.current_step == 2)
    check("halt action recorded",
          inst.actions.filter_by(action="halted").count() == 1)
    check("admin notified of halt",
          Notification.query.filter(
              Notification.user_id == "T41-ADM",
              Notification.title.like("%halted%")).count() >= 1)
    check("admin (non-subject) may decide halted",
          approvals.can_act(inst, User.query.get("T41-ADM")))
    check("non-admin cannot decide halted",
          not approvals.can_act(inst, User.query.get("T41-MGR1")))
    check("halt audited",
          AuditLog.query.filter_by(action="approval_halted").count() >= 1)
r = decide(rid, "T41-ADM", "approve")
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    check("admin decision resolves halted chain", inst.status == "approved")

# ================= 1d) delegation ==========================================
set_chain_ctx("leave", ["hr_manager"])
with app.app_context():
    db.session.add(ApprovalDelegation(
        delegator_id="T41-HRM", delegate_id="T41-DEL",
        process_key="leave",
        starts_on=TODAY - timedelta(days=7),
        ends_on=TODAY + timedelta(days=7),
        is_active=True, created_by="T41-ADM"))
    db.session.commit()
rid = apply_leave("T41-E1", "annual", FUT + timedelta(days=40),
                  FUT + timedelta(days=41))
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    del_user = User.query.get("T41-DEL")
    check("delegate resolves inside window",
          approvals.can_act(inst, del_user))
with app.test_request_context():
    # engine-direct call (log_action needs a request context)
    inst = approvals.get_instance("leave", rid)
    del_user = User.query.get("T41-DEL")
    outcome = approvals.act(inst, del_user, True, "covering for HRM")
    check("delegate approval finalises", outcome == "final_approved")
with app.app_context():
    del_row = ApprovalDelegation.query.filter_by(
        delegator_id="T41-HRM", delegate_id="T41-DEL").first()
    check("delegated action audited",
          AuditLog.query.filter(
              AuditLog.action == "approval_delegated_action",
              AuditLog.detail.like(f"%delegation #{del_row.id}%")).count()
          >= 1)
# outside the window: cannot act
with app.app_context():
    d = ApprovalDelegation(delegator_id="T41-HRM", delegate_id="T41-DEL",
                           process_key="leave",
                           starts_on=TODAY - timedelta(days=14),
                           ends_on=TODAY - timedelta(days=1),
                           is_active=True, created_by="T41-ADM")
    db.session.add(d)
    db.session.commit()
rid = apply_leave("T41-E1", "annual", FUT + timedelta(days=50),
                  FUT + timedelta(days=51))
with app.app_context():
    inst = approvals.get_instance("leave", rid)
    # the in-window delegation was consumed by the finished instance;
    # expire it so only the out-of-window row remains
    for row in ApprovalDelegation.query.filter_by(
            delegator_id="T41-HRM", delegate_id="T41-DEL",
            process_key="leave").all():
        if row.ends_on >= TODAY:
            row.is_active = False
    db.session.commit()
    inst2 = approvals.get_instance("leave", rid)
    check("delegate cannot act outside window",
          not approvals.can_act(inst2, User.query.get("T41-DEL")))
# delegation UI: validation + add + deactivate
logout()
login("T41-ADM", "pw-T41-ADM")
r = post("/hrms/approval-matrix",
         {"action": "add_delegation", "delegator_id": "T41-ADM",
          "delegate_id": "T41-ADM",
          "d_process_key": "", "starts_on": TODAY.isoformat(),
          "ends_on": (TODAY + timedelta(days=3)).isoformat()})
with app.app_context():
    check("delegation rejects delegate == delegator",
          ApprovalDelegation.query.filter_by(
              delegator_id="T41-ADM",
              delegate_id="T41-ADM").count() == 0)
r = post("/hrms/approval-matrix",
         {"action": "add_delegation", "delegator_id": "T41-HRM",
          "delegate_id": "T41-MGR1",
          "d_process_key": "", "starts_on": TODAY.isoformat(),
          "ends_on": (TODAY + timedelta(days=3)).isoformat()})
with app.app_context():
    drow = ApprovalDelegation.query.filter_by(
        delegator_id="T41-HRM", delegate_id="T41-MGR1",
        process_key=None).first()
    check("delegation added via UI (all processes)", drow is not None)
    did = drow.id
r = post("/hrms/approval-matrix",
         {"action": "deactivate_delegation", "delegation_id": did})
with app.app_context():
    check("delegation deactivated via UI",
          ApprovalDelegation.query.get(did).is_active is False)
r = client.get("/hrms/approval-matrix")
check("matrix page shows delegations", b"Approval Delegations" in r.data)
logout()
login("T41-MGR1", "pw-T41-MGR1")
r = client.get("/hrms/approval-matrix")
check("matrix page 403 for non-admin", r.status_code == 403)

# ================= 2) leave double-spend guard =============================
set_chain_ctx("leave", ["reporting_manager"])
# T41-E2 has 10 annual days; first request takes 5 as pending.
rida = apply_leave("T41-E2", "annual", FUT + timedelta(days=60),
                   FUT + timedelta(days=64))
check("first 5-day request accepted", rida is not None)
logout()
login("T41-E2", "pw-T41-E2")
r = post("/leave", {"leave_type": "annual",
                    "start_date": (FUT + timedelta(days=70)).isoformat(),
                    "end_date": (FUT + timedelta(days=75)).isoformat(),
                    "reason": "second"}, files=False)
# the guard flashes on the POST response itself (form re-render, no redirect)
check("second request rejected counting pending",
      rida is not None and b"pending approval" in r.data)
with app.app_context():
    check("rejected request not created",
          LeaveRequest.query.filter_by(
              user_id="T41-E2", reason="second").count() == 0)
# final-approval recheck: T41-E1 (30 days balance), request 5 pending;
# simulate another approval consuming 27 -> final approval refused.
with app.app_context():
    mk("T41-E6", "T41 Employee Six", "employee", "Production",
       reporting_manager="T41-MGR1")
    db.session.add(LeaveBalance(user_id="T41-E6", leave_type="annual",
                                entitled=30, used=0))
    db.session.commit()
ridb = apply_leave("T41-E6", "annual", FUT + timedelta(days=80),
                   FUT + timedelta(days=84))
with app.app_context():
    bal = LeaveBalance.query.filter_by(user_id="T41-E6",
                                       leave_type="annual").first()
    bal.used = 27  # consumed elsewhere while the request was pending
    db.session.commit()
r = decide(ridb, "T41-MGR1", "approve")  # final (single) step
check("final approval recheck flash",
      b"Insufficient remaining balance" in client.get("/approvals").data)
with app.app_context():
    rq = LeaveRequest.query.get(ridb)
    inst = approvals.get_instance("leave", ridb)
    check("overdrawn request stays at final step",
          rq.status == "pending" and inst.status == "pending"
          and inst.current_step == inst.total_steps)

# ================= 3) settlement disbursement split ========================
from app import settlement as stl  # noqa: E402
LWD_OLD = TODAY - timedelta(days=30)
with app.app_context():
    s = FinalSettlement(user_id="T41-SE", last_working_day=LWD_OLD,
                        separation_type="Resignation", status="draft",
                        created_by="T41-ADM")
    db.session.add(s)
    db.session.commit()
    sid = s.id
logout()
login("T41-ADM", "pw-T41-ADM")


def st_post(sid, endpoint, data, files=False):
    t = get_token(f"/hrms/settlements/{sid}")
    d = dict(data)
    d["csrf_token"] = t
    return client.post(f"/hrms/settlements/{sid}/{endpoint}", data=d,
                       follow_redirects=False,
                       content_type="multipart/form-data" if files else None)


r = st_post(sid, "calculate", {})
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("settlement calculated", st.status == "calculated")
logout()
login("T41-ADM2", "pw-T41-ADM2")  # SoD: different admin approves
r = st_post(sid, "approve", {})
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("settlement approved", st.status == "approved")
    check("overdue before disbursement", st.is_overdue is True)
# mark-paid straight from approved is refused
r = st_post(sid, "mark-paid", {})
with app.app_context():
    check("mark-paid from approved refused",
          FinalSettlement.query.get(sid).status == "approved")
# mark-disbursed without proof refused
r = st_post(sid, "mark-disbursed", {"payment_ref": "TT-341"}, files=True)
with app.app_context():
    check("disbursement blocked without proof",
          FinalSettlement.query.get(sid).status == "approved")
# mark-disbursed with proof + ref
t = get_token(f"/hrms/settlements/{sid}")
r = client.post(f"/hrms/settlements/{sid}/mark-disbursed",
                data={"csrf_token": t, "payment_ref": "TT-341",
                      "payment_proof": (io.BytesIO(b"%PDF-1.4 wps advice"),
                                        "proof.pdf")},
                content_type="multipart/form-data", follow_redirects=False)
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("disbursed with proof + ref",
          st.status == "disbursed" and st.payment_ref == "TT-341"
          and bool(st.payment_doc_path) and st.disbursed_at is not None)
    check("overdue clock stops at disbursement", st.is_overdue is False)
r = client.get(f"/hrms/settlements/{sid}/payment-proof")
check("payment proof viewable (200 pdf)",
      r.status_code == 200 and r.headers.get("Content-Type") == "application/pdf")
# mark-paid still blocked until the signed receipt
r = st_post(sid, "mark-paid", {})
with app.app_context():
    check("mark-paid blocked without receipt",
          FinalSettlement.query.get(sid).status == "disbursed")
t = get_token(f"/hrms/settlements/{sid}")
r = client.post(f"/hrms/settlements/{sid}/receipt",
                data={"csrf_token": t,
                      "receipt": (io.BytesIO(b"%PDF-1.4 cancellation"),
                                  "cancel.pdf")},
                content_type="multipart/form-data", follow_redirects=False)
r = st_post(sid, "mark-paid", {})
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("mark-paid succeeds after receipt",
          st.status == "paid" and st.paid_at is not None)
r = client.get(f"/hrms/settlements/{sid}")
check("detail page renders for paid", r.status_code == 200
      and b"TT-341" in r.data)

# ================= 4) unpaid-exclusion refinement ==========================
with app.app_context():
    mk("T41-E7", "T41 Employee Seven", "employee", "Production",
       joining_date=date(2020, 1, 15))
    db.session.commit()
    d1, d2 = date(2026, 1, 1), date(2026, 1, 31)
    db.session.add(LeaveRequest(user_id="T41-E7", leave_type="sick",
                                start_date=date(2026, 1, 5),
                                end_date=date(2026, 1, 7), days=3,
                                status="approved"))
    db.session.add(LeaveRequest(user_id="T41-E7", leave_type="unpaid",
                                start_date=date(2026, 1, 15),
                                end_date=date(2026, 1, 16), days=2,
                                status="approved"))
    # absent inside the approved sick period -> NOT excluded
    db.session.add(AttendanceRecord(user_id="T41-E7",
                                   date=date(2026, 1, 6), status="absent"))
    # plain unexcused absence -> excluded
    db.session.add(AttendanceRecord(user_id="T41-E7",
                                   date=date(2026, 1, 10), status="absent"))
    # absent inside the unpaid period -> counted once (via unpaid leave)
    db.session.add(AttendanceRecord(user_id="T41-E7",
                                   date=date(2026, 1, 16), status="absent"))
    db.session.commit()
    n = stl.unpaid_absence_days("T41-E7", d1, d2)
    check("absent inside sick leave not excluded, unpaid counted once",
          n == 3, f"got {n}")

# ================= 5) punch hardening ======================================
logout()
login("T41-ADM", "pw-T41-ADM")
r = post("/hrms/locations",
         {"action": "save", "name": "T41 Site", "latitude": "25.305400",
          "longitude": "55.424800", "radius_m": "200", "is_active": "on"})
with app.app_context():
    loc = WorkLocation.query.filter_by(name="T41 Site").first()
    check("work location created", loc is not None)
JPG = png_bytes(400, 300)


def punch_as(uid, lat, lng, accuracy):
    logout()
    login(uid, "pw-" + uid)
    t = get_token("/attendance/punch")
    return client.post("/attendance/punch/submit",
                       data={"csrf_token": t, "punch_type": "in",
                             "latitude": str(lat), "longitude": str(lng),
                             "accuracy": str(accuracy),
                             "photo": (io.BytesIO(JPG), "selfie.png")},
                       content_type="multipart/form-data")


# accuracy 500 m inside the fence: accepted, flagged.
r = punch_as("T41-E1", 25.3054, 55.4248, 500)
with app.app_context():
    p = (PunchRecord.query.filter_by(user_id="T41-E1")
         .order_by(PunchRecord.id.desc()).first())
    check("low-accuracy punch accepted inside fence",
          r.get_json().get("ok") is True and p.low_accuracy is True)
    check("low-accuracy audit recorded",
          AuditLog.query.filter_by(action="punch_low_accuracy").count() >= 1)
# rejection beyond 2 km: generic message, no distance/location detail.
r = punch_as("T41-E1", 25.3054 + 0.03, 55.4248, 10)
err = (r.get_json() or {}).get("error", "")
check("generic rejection beyond 2 km",
      r.status_code == 403
      and err == "You are outside every authorised work location."
      and "m away" not in err and "T41 Site" not in err, err[:80])
# rejection ~500 m away: nearest + distance shown.
r = punch_as("T41-E1", 25.3054 + 0.0045, 55.4248, 10)
err = (r.get_json() or {}).get("error", "")
check("near rejection shows nearest + distance",
      r.status_code == 403 and "m away" in err and "T41 Site" in err,
      err[:80])
# legal hold: purge skips held photos, deletes unheld old photos.
with app.app_context():
    from app.files import store_file
    from app import engagement as eng
    held = PunchRecord(user_id="T41-E1", punch_type="in",
                       punched_at=datetime.utcnow(),
                       local_date=TODAY,
                       photo_path=store_file(b"held", "punches", "jpg"),
                       legal_hold=True,
                       created_at=datetime.utcnow() - timedelta(days=46))
    old = PunchRecord(user_id="T41-E1", punch_type="in",
                      punched_at=datetime.utcnow(),
                      local_date=TODAY,
                      photo_path=store_file(b"old", "punches", "jpg"),
                      legal_hold=False,
                      created_at=datetime.utcnow() - timedelta(days=46))
    db.session.add_all([held, old])
    db.session.commit()
    hid, oid = held.id, old.id
    n = eng.purge_punch_photos()
    check("purge skips legal-hold photo",
          PunchRecord.query.get(hid).photo_path is not None)
    check("purge deletes unheld old photo",
          PunchRecord.query.get(oid).photo_path is None)
# admin hold/release toggle (audited); managers forbidden.
logout()
login("T41-ADM", "pw-T41-ADM")
t = get_token("/hrms/punches")
r = client.post(f"/hrms/punches/{hid}/hold",
                data={"csrf_token": t, "action": "release"},
                follow_redirects=False)
with app.app_context():
    check("admin releases legal hold",
          PunchRecord.query.get(hid).legal_hold is False
          and AuditLog.query.filter_by(
              action="punch_legal_hold_released").count() >= 1)
r = client.get("/hrms/punches")
check("punches list flags low accuracy",
      b"Low GPS accuracy" in r.data and b"Legal Hold" in r.data)
logout()
login("T41-MGR1", "pw-T41-MGR1")
t = get_token("/hrms/punches")
r = client.post(f"/hrms/punches/{hid}/hold",
                data={"csrf_token": t, "action": "hold"},
                follow_redirects=False)
check("hold toggle admin-only (manager 403)", r.status_code == 403)

# ================= 6) JD tamper evidence ===================================
logout()
login("T41-ADM", "pw-T41-ADM")
r = post("/hrms/jds",
         {"action": "create", "title": "Grinder", "department": "Production",
          "reports_to": "Production Manager",
          "body": "JOB PURPOSE\nGrind fixtures.\n\nACCOUNTABILITIES\n1. Grind."})
with app.app_context():
    jd = JobDescription.query.filter_by(title="Grinder").first()
    jid = jd.id
r = post(f"/hrms/jds/{jid}",
         {"action": "assign", "scope": "by_designation"})
# assign directly to T41-E2 as well (designation differs)
with app.app_context():
    db.session.add(JDAcknowledgement(jd_id=jid, user_id="T41-E2",
                                     jd_version=1, assigned_by="T41-ADM"))
    db.session.commit()
    aid = JDAcknowledgement.query.filter_by(
        jd_id=jid, user_id="T41-E2").first().id
logout()
login("T41-E2", "pw-T41-E2")
sig_b64 = base64.b64encode(png_bytes(400, 160, "white")).decode()
r = client.post(f"/my-jd/{aid}",
                data={"csrf_token": get_token(f"/my-jd/{aid}"),
                      "typed_name": "T41 Employee Two", "agree": "on",
                      "signature": "data:image/png;base64," + sig_b64},
                follow_redirects=False)
with app.app_context():
    ack = JDAcknowledgement.query.get(aid)
    expect = hashlib.sha256(
        ("Grinder\nJOB PURPOSE\nGrind fixtures.\n\nACCOUNTABILITIES\n1. Grind.\n1"
         ).encode("utf-8")).hexdigest()
    check("jd acknowledgement stores 64-char content hash",
          ack.jd_text_hash is not None and len(ack.jd_text_hash) == 64
          and ack.jd_text_hash == expect,
          (ack.jd_text_hash or "")[:12])
    check("user agent stored", bool(ack.user_agent))
logout()
login("T41-ADM", "pw-T41-ADM")
r = client.get(f"/hrms/jds/{jid}")
check("hr jd detail shows hash prefix",
      ack.jd_text_hash[:12].encode() in r.data)

# ================= 7) celebration opt-out ==================================
with app.app_context():
    from app import engagement as eng2
    mk("T41-E8", "T41 Employee Eight", "employee", "Production",
       date_of_birth=TODAY.replace(year=1990))
    mk("T41-E9", "T41 Employee Nine", "employee", "Production",
       date_of_birth=TODAY.replace(year=1992))
    User.query.get("T41-E8").share_celebrations = False
    # reset the daily guard so the test run creates posts
    from app.models import AppSetting
    row = AppSetting.query.get("celebrations_last_run")
    if row:
        row.value = (TODAY - timedelta(days=1)).isoformat()
    db.session.commit()
    eng2.ensure_todays_celebrations()
    check("opted-out user gets no celebration post",
          CelebrationPost.query.filter_by(
              user_id="T41-E8", post_date=TODAY).count() == 0)
    check("other user still gets a post",
          CelebrationPost.query.filter_by(
              user_id="T41-E9", post_date=TODAY).count() >= 1)
    check("opted-out user still notified about others",
          Notification.query.filter(
              Notification.user_id == "T41-E8",
              Notification.body.like("%wish%")).count() >= 1)
    check("profile toggle persists",
          User.query.get("T41-E8").share_celebrations is False)
logout()
login("T41-E9", "pw-T41-E9")
t = get_token("/profile")
r = client.post("/profile/celebrations",
                data={"csrf_token": t, "share_celebrations": "on"},
                follow_redirects=False)
with app.app_context():
    check("celebration preference toggle",
          User.query.get("T41-E9").share_celebrations is True)
t = get_token("/profile")
r = client.post("/profile/celebrations", data={"csrf_token": t},
                follow_redirects=False)
with app.app_context():
    check("celebration preference untick",
          User.query.get("T41-E9").share_celebrations is False)

# ================= 8) quiz shuffle =========================================
with app.app_context():
    c = TrainingCourse(title="T41 Shuffle Course", category="Basic",
                       pass_percent=70, is_active=True, created_by="T41-ADM")
    db.session.add(c)
    db.session.flush()
    cid = c.id
    for i in range(5):
        opts = [f"opt{i}-{j}" for j in range(4)]
        db.session.add(QuizQuestion(course_id=cid,
                                    question=f"T41 question {i}",
                                    options_json=__import__("json").dumps(opts),
                                    correct_index=i % 4, sort=i))
    db.session.add(TrainingAssignment(course_id=cid, user_id="T41-E1",
                                      assigned_by="T41-ADM"))
    db.session.commit()
    correct = [q.correct_index for q in
               QuizQuestion.query.filter_by(course_id=cid)
               .order_by(QuizQuestion.sort).all()]
    qids = [q.id for q in QuizQuestion.query.filter_by(course_id=cid)
            .order_by(QuizQuestion.sort).all()]
logout()
login("T41-E1", "pw-T41-E1")
orders = []
for _ in range(5):
    r = client.get(f"/my-training/{cid}/quiz")
    ids = re.findall(rb'name="q_(\d+)"', r.data)
    orders.append(ids)
    assert r.status_code == 200
check("quiz question order shuffles across renders",
      len({tuple(o) for o in orders}) > 1,
      f"{len({tuple(o) for o in orders})} distinct orders")
# answer every question with its ORIGINAL correct index -> must pass
# identically to an unshuffled render.
with app.app_context():
    qmap = {q.id: q for q in QuizQuestion.query.filter_by(course_id=cid)}
data = {"csrf_token": get_token(f"/my-training/{cid}/quiz")}
for qid, corr in zip(qids, correct):
    data[f"q_{qid}"] = str(corr)
r = client.post(f"/my-training/{cid}/quiz", data=data,
                follow_redirects=False)
with app.app_context():
    from app.models import TrainingAttempt
    att = (TrainingAttempt.query.filter_by(course_id=cid, user_id="T41-E1")
           .order_by(TrainingAttempt.id.desc()).first())
    check("shuffled quiz grades identically (100% pass)",
          att is not None and att.score_percent == 100.0
          and att.passed is True)

print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
