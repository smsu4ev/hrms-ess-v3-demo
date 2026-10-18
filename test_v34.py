"""HRMS+ESS v3.4 tests: configurable approval matrix.

1. User flags is_hr_staff / is_hr_manager (Admin Users toggles).
2. Matrix admin UI (admin-only, add/delete/reorder, last-step delete refused,
   preview rendering).
3. Employee creation approval flow: hr_staff creator -> pending_approval +
   login blocked; HR Manager review approve/reject; hr_manager creator ->
   active immediately; role/salary forcing on the hr_staff path.
4. Leave multi-step chain: step progression, balance only on final approval,
   non-resolver 403, reject mid-chain, auto-skip, specific_user step.
5. Regularisation goes through the engine.
6. Approvals inbox scoping.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v34.db && ESS_SECRET_KEY=testsecret12345 \
ESS_DATABASE_URL=<redacted>
"""
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app import approvals  # noqa: E402
from app.models import (User, LeaveBalance, LeaveRequest, RegularisationRequest,
                        ApprovalMatrix, ApprovalStep, ApprovalInstance,
                        ApprovalAction, Notification)  # noqa: E402
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


def post(path, data):
    t = get_token(path)
    data = dict(data)
    data["csrf_token"] = t
    return client.post(path, data=data, follow_redirects=False)


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


with app.app_context():
    admin = mk("T34-ADM", "T34 Admin", "admin", "Human Resources")
    mgr1 = mk("T34-MGR1", "T34 Manager One", "manager", "Production")
    mgr2 = mk("T34-MGR2", "T34 Manager Two", "manager", "Sales")
    emp1 = mk("T34-E1", "T34 Employee One", "employee", "Production",
              reporting_manager="T34-MGR1")
    hrs = mk("T34-HRS", "T34 HR Staff", "employee", "Human Resources",
             is_hr_staff=True)
    hrm = mk("T34-HRM", "T34 HR Manager", "employee", "Human Resources",
             is_hr_manager=True)
    db.session.add(LeaveBalance(user_id="T34-E1", leave_type="annual",
                                entitled=30, used=5))
    db.session.commit()

# ================= 1) HR flags via Admin Users =================
login("T34-ADM", "pw-T34-ADM")
r = post("/admin/users/T34-HRS", {"action": "toggle_hr_staff"})
check("admin toggles hr_staff on", r.status_code == 302)
# already staff -> toggle turns it off; restore via DB and test the off->on path
with app.app_context():
    u = User.query.get("T34-HRS")
    u.is_hr_staff = False
    db.session.commit()
r = post("/admin/users/T34-HRS", {"action": "toggle_hr_staff"})
with app.app_context():
    check("toggle_hr_staff POST sets flag",
          User.query.get("T34-HRS").is_hr_staff is True)
r = post("/admin/users/T34-MGR1", {"action": "toggle_hr_manager"})
with app.app_context():
    check("toggle_hr_manager POST sets flag",
          User.query.get("T34-MGR1").is_hr_manager is True)
    User.query.get("T34-MGR1").is_hr_manager = False  # restore
    db.session.commit()
check("admin user detail page shows flags",
      b"HR Staff" in client.get("/admin/users/T34-HRS").data)
logout()

login("T34-E1", "pw-T34-E1")
r = client.get("/admin/users/T34-HRS")
check("non-admin cannot open admin user page (403)", r.status_code == 403)
with app.app_context():
    check("flag unchanged by non-admin",
          User.query.get("T34-HRS").is_hr_staff is True)
logout()

# ================= 2) matrix admin UI =================
login("T34-E1", "pw-T34-E1")
r = client.get("/hrms/approval-matrix")
check("matrix UI admin-only (403 for employee)", r.status_code == 403)
logout()

login("T34-MGR1", "pw-T34-MGR1")
r = client.get("/hrms/approval-matrix")
check("matrix UI admin-only (403 for manager)", r.status_code == 403)
logout()

login("T34-ADM", "pw-T34-ADM")
r = client.get("/hrms/approval-matrix")
check("matrix UI 200 for admin", r.status_code == 200)
check("matrix UI shows processes", b"Employee Creation" in r.data and
      b"Attendance Regularisation" in r.data)
# reset chains to the documented defaults so the UI tests are deterministic
with app.app_context():
    set_chain("leave", ["reporting_manager"])
    set_chain("regularisation", ["reporting_manager"])
    set_chain("employee_creation", ["hr_manager"])
check("matrix UI preview shows hr_manager fallback/users",
      b"T34 HR Manager" in r.data or b"administrators" in r.data)

# add a step to the leave chain
r = post("/hrms/approval-matrix",
         {"action": "add_step", "process_key": "leave",
          "approver_type": "hr_staff"})
with app.app_context():
    steps = approvals.get_matrix("leave").steps.order_by(
        ApprovalStep.step_no).all()
    check("add step persists",
          [s.approver_type for s in steps] == ["reporting_manager", "hr_staff"])
# reorder: move step 1 down
sid = steps[0].id
r = post("/hrms/approval-matrix",
         {"action": "move_down", "process_key": "leave", "step_id": sid})
with app.app_context():
    steps = approvals.get_matrix("leave").steps.order_by(
        ApprovalStep.step_no).all()
    check("move step persists (reorder)",
          [s.approver_type for s in steps] == ["hr_staff", "reporting_manager"])
# delete the second step
sid2 = steps[1].id
r = post("/hrms/approval-matrix",
         {"action": "delete_step", "process_key": "leave", "step_id": sid2})
with app.app_context():
    steps = approvals.get_matrix("leave").steps.all()
    check("delete step persists (one step left)", len(steps) == 1)
# deleting the last step is refused
r = post("/hrms/approval-matrix",
         {"action": "delete_step", "process_key": "leave",
          "step_id": steps[0].id})
with app.app_context():
    check("deleting last step refused",
          approvals.get_matrix("leave").steps.count() == 1)
r = client.get("/hrms/approval-matrix", follow_redirects=True)
check("last-step refusal flashes", b"at least one step" in r.data.lower())
logout()

# ================= 3) employee creation approval flow =================
login("T34-HRS", "pw-T34-HRS")
r = client.get("/hrms/employees/new")
check("hr_staff can open employee form (200)", r.status_code == 200)
new_emp = {"employee_id": "T34-N1", "name": "T34 New Hire", "role": "admin",
           "department": "Production", "designation": "Helper",
           "reporting_manager": "T34-MGR1", "joining_date": "2026-10-19",
           "employment_status": "probation", "basic_salary": "5000",
           "monthly_salary": "8000"}
r = post("/hrms/employees/new", new_emp)
with app.app_context():
    u = User.query.get("T34-N1")
    check("hr_staff creation -> pending_approval",
          u is not None and u.employment_status == "pending_approval")
    check("hr_staff creation -> inactive", u is not None and not u.is_active)
    check("hr_staff cannot force role=admin",
          u is not None and u.role == "employee")
    check("hr_staff cannot set salary (ignored)",
          u is not None and u.basic_salary == 0 and u.monthly_salary == 0)
    check("pending_status stores intended status",
          u is not None and u.pending_status == "probation")
    check("approval instance started",
          ApprovalInstance.query.filter_by(process_key="employee_creation",
                                           ref_id="T34-N1").first() is not None)
logout()
r = login("T34-N1", "pw-x")
check("pending employee login blocked", r.status_code != 302)

# review page: resolver sees action buttons; non-resolver is blocked
login("T34-E1", "pw-T34-E1")
r = client.get("/hrms/employees/T34-N1/review")
check("review page 403 for unrelated employee", r.status_code == 403)
logout()
login("T34-HRS", "pw-T34-HRS")
r = client.get("/hrms/employees/T34-N1/review")
check("creator can view review page", r.status_code == 200)
logout()
login("T34-HRM", "pw-T34-HRM")
r = client.get("/hrms/employees/T34-N1/review")
check("HR Manager can view review page", r.status_code == 200)
check("review page shows action buttons to resolver",
      b"Approve" in r.data and b"Reject" in r.data)
# reject path on another record
logout()
login("T34-HRS", "pw-T34-HRS")
r = post("/hrms/employees/new",
         {"employee_id": "T34-N2", "name": "T34 Rejected Hire",
          "department": "Production", "employment_status": "probation"})
logout()
login("T34-HRM", "pw-T34-HRM")
r = post("/hrms/employees/T34-N2/review",
         {"action": "reject", "comment": "no headcount"})
with app.app_context():
    u = User.query.get("T34-N2")
    check("rejected creation stays inactive",
          u is not None and not u.is_active
          and u.employment_status == "pending_approval")
    inst = approvals.get_instance("employee_creation", "T34-N2")
    check("rejected instance status", inst.status == "rejected")
    note = Notification.query.filter_by(
        user_id="T34-HRS", kind="approval").order_by(
            Notification.created_at.desc()).first()
    check("creator notified on rejection",
          note is not None and "rejected" in note.title.lower())
# approve path
r = post("/hrms/employees/T34-N1/review",
         {"action": "approve", "comment": "verified"})
with app.app_context():
    u = User.query.get("T34-N1")
    check("approved creation activated",
          u is not None and u.is_active)
    check("intended status applied on approval",
          u is not None and u.employment_status == "probation")
    inst = approvals.get_instance("employee_creation", "T34-N1")
    check("approved instance status", inst.status == "approved")
    check("pending_status cleared", u.pending_status is None)
with app.app_context():
    u = User.query.get("T34-N1")
    u.set_password("pw-T34-N1")  # creation assigns a random temp password
    db.session.commit()
logout()
check("approved employee can log in",
      login("T34-N1", "pw-T34-N1").status_code == 302)
logout()

# hr_manager creator -> active immediately, no instance
login("T34-HRM", "pw-T34-HRM")
r = post("/hrms/employees/new",
         {"employee_id": "T34-N3", "name": "T34 Direct Hire",
          "department": "Production", "employment_status": "active",
          "basic_salary": "3000", "monthly_salary": "4500"})
with app.app_context():
    u = User.query.get("T34-N3")
    check("hr_manager creator -> active immediately",
          u is not None and u.is_active and u.employment_status == "active")
    check("no approval instance for hr_manager creation",
          approvals.get_instance("employee_creation", "T34-N3") is None)
logout()

# non-hr_staff, non-manager employee cannot create
login("T34-E1", "pw-T34-E1")
r = client.get("/hrms/employees/new")
check("plain employee blocked from creation form (403)",
      r.status_code == 403)
logout()

# ================= 4) leave multi-step chain =================
with app.app_context():
    set_chain("leave", ["reporting_manager", "hr_staff", "hr_manager"])
login("T34-E1", "pw-T34-E1")
s = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")
e = (date.today() + timedelta(days=4)).strftime("%Y-%m-%d")
t = get_token("/leave")
r = client.post("/leave", data={"leave_type": "annual", "start_date": s,
                                 "end_date": e, "reason": "chain test",
                                 "csrf_token": t}, follow_redirects=False)
with app.app_context():
    req = LeaveRequest.query.filter_by(user_id="T34-E1").order_by(
        LeaveRequest.id.desc()).first()
    inst = approvals.get_instance("leave", req.id)
    check("leave instance started at step 1",
          inst is not None and inst.current_step == 1
          and inst.status == "pending")
    check("step 1 resolves to reporting manager",
          approvals.current_resolvers(inst) == ["T34-MGR1"])
    check("leave page shows step progress",
          b"Step 1 of 3" in client.get("/leave").data)
logout()

# inbox: visible to current resolver, not to a non-resolver manager
login("T34-MGR1", "pw-T34-MGR1")
r = client.get("/approvals")
check("resolver's inbox shows the item",
      f"T34-E1".encode() in r.data)
check("inbox step badge", b"Step 1 of 3" in r.data)
logout()
login("T34-MGR2", "pw-T34-MGR2")
r = client.get("/approvals")
check("inbox hides item from non-resolver manager",
      b"T34-E1" not in r.data and r.status_code == 200)
logout()

# step 1 approve by manager -> still pending, balance untouched
login("T34-MGR1", "pw-T34-MGR1")
t = get_token("/approvals")
r = client.post(f"/approvals/{req.id}",
                data={"action": "approve", "comment": "ok by mgr",
                      "csrf_token": t}, follow_redirects=False)
with app.app_context():
    db.session.expire_all()
    req = LeaveRequest.query.get(req.id)
    inst = approvals.get_instance("leave", req.id)
    check("after step 1: request still pending", req.status == "pending")
    check("after step 1: balance NOT deducted",
          LeaveBalance.query.filter_by(user_id="T34-E1",
                                       leave_type="annual").first().used == 5)
    check("after step 1: instance advanced to step 2",
          inst.current_step == 2 and inst.status == "pending")
logout()

# non-resolver (eligible role, but not a step-2 resolver) cannot act on step 2
login("T34-MGR2", "pw-T34-MGR2")
t = get_token("/approvals")
r = client.post(f"/approvals/{req.id}",
                data={"action": "approve", "csrf_token": t},
                follow_redirects=False)
check("non-resolver acting on step 2 -> 403", r.status_code == 403)
logout()

# step 2 approve by hr_staff -> still pending
login("T34-HRS", "pw-T34-HRS")
t = get_token("/approvals")
r = client.post(f"/approvals/{req.id}",
                data={"action": "approve", "comment": "hr ok",
                      "csrf_token": t}, follow_redirects=False)
with app.app_context():
    db.session.expire_all()
    req = LeaveRequest.query.get(req.id)
    inst = approvals.get_instance("leave", req.id)
    check("after step 2: request still pending", req.status == "pending")
    check("after step 2: balance still untouched",
          LeaveBalance.query.filter_by(user_id="T34-E1",
                                       leave_type="annual").first().used == 5)
    check("after step 2: instance advanced to step 3",
          inst.current_step == 3)
logout()

# step 3 approve by hr_manager -> approved, balance deducted, 3 actions
login("T34-HRM", "pw-T34-HRM")
t = get_token("/approvals")
r = client.post(f"/approvals/{req.id}",
                data={"action": "approve", "comment": "final ok",
                      "csrf_token": t}, follow_redirects=False)
with app.app_context():
    db.session.expire_all()
    req = LeaveRequest.query.get(req.id)
    inst = approvals.get_instance("leave", req.id)
    bal = LeaveBalance.query.filter_by(user_id="T34-E1",
                                       leave_type="annual").first()
    check("after step 3: request approved", req.status == "approved")
    check("after step 3: balance deducted",
          bal.used == 5 + req.days, f"used={bal.used} days={req.days}")
    approves = ApprovalAction.query.filter_by(
        instance_id=inst.id, action="approve").count()
    check("action history has 3 approves", approves == 3, f"count={approves}")
    check("instance approved", inst.status == "approved")
logout()

# reject at step 2 variant
login("T34-E1", "pw-T34-E1")
s = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")
e = (date.today() + timedelta(days=11)).strftime("%Y-%m-%d")
t = get_token("/leave")
client.post("/leave", data={"leave_type": "annual", "start_date": s,
                             "end_date": e, "reason": "reject test",
                             "csrf_token": t}, follow_redirects=False)
logout()
with app.app_context():
    req2 = LeaveRequest.query.filter_by(user_id="T34-E1").order_by(
        LeaveRequest.id.desc()).first()
login("T34-MGR1", "pw-T34-MGR1")
t = get_token("/approvals")
client.post(f"/approvals/{req2.id}",
            data={"action": "approve", "csrf_token": t},
            follow_redirects=False)
logout()
login("T34-HRS", "pw-T34-HRS")
t = get_token("/approvals")
client.post(f"/approvals/{req2.id}",
            data={"action": "reject", "comment": "cover needed",
                  "csrf_token": t}, follow_redirects=False)
with app.app_context():
    db.session.expire_all()
    req2 = LeaveRequest.query.get(req2.id)
    inst2 = approvals.get_instance("leave", req2.id)
    check("reject at step 2: status rejected", req2.status == "rejected")
    check("reject at step 2: balance untouched",
          LeaveBalance.query.filter_by(user_id="T34-E1",
                                       leave_type="annual").first().used ==
          5 + LeaveRequest.query.get(req.id).days)
    check("reject at step 2: instance rejected", inst2.status == "rejected")
    check("reject comment recorded", "cover needed" in
          (inst2.reject_reason or ""))
logout()

# ================= 5) auto-skip =================
with app.app_context():
    set_chain("leave", ["skip_level_manager", "hr_manager"])
    # T34-MGR1 has no manager -> skip_level resolvers empty -> auto-skip
    inst = approvals.start_instance("leave", "T34-AUTO1", "T34-E1", "T34-E1")
    db.session.expire_all()
    inst = approvals.get_instance("leave", "T34-AUTO1")
    check("auto-skip: lands on step 2 (hr_manager)", inst.current_step == 2)
    check("auto-skip action recorded",
          ApprovalAction.query.filter_by(instance_id=inst.id,
                                         action="auto_skip",
                                         step_no=1).count() == 1)
    check("auto-skipped chain resolves to hr_manager",
          "T34-HRM" in approvals.current_resolvers(inst))
    # expired snapshot: later matrix edits don't mutate in-flight instances
    set_chain("leave", ["reporting_manager"])
    db.session.expire_all()
    inst = approvals.get_instance("leave", "T34-AUTO1")
    check("in-flight instance keeps its snapshot",
          len(inst.steps) == 2)

# ================= 6) specific_user step =================
with app.app_context():
    set_chain("leave", [("specific_user", "T34-MGR2")])
    inst = approvals.start_instance("leave", "T34-SPEC1", "T34-E1", "T34-E1")
    check("specific_user step resolves to that user only",
          approvals.current_resolvers(inst) == ["T34-MGR2"])
login("T34-MGR1", "pw-T34-MGR1")
t = get_token("/approvals")
r = client.post("/approvals/999999",
                data={"action": "approve", "csrf_token": t},
                follow_redirects=False)
check("legacy 404 preserved for unknown request", r.status_code == 404)
logout()
# act through the engine directly for the synthetic instance
with app.test_request_context("/"):
    inst = approvals.get_instance("leave", "T34-SPEC1")
    hrs_u = User.query.get("T34-HRS")
    try:
        approvals.act(inst, hrs_u, True)
        check("wrong specific_user cannot act (raises)", False)
    except PermissionError:
        check("wrong specific_user cannot act (raises)", True)
    mgr2_u = User.query.get("T34-MGR2")
    outcome = approvals.act(inst, mgr2_u, True)
    check("designated specific_user can act", outcome == "final_approved")

# ================= 7) regularisation through the engine =================
with app.app_context():
    set_chain("regularisation", ["reporting_manager", "hr_staff"])
login("T34-E1", "pw-T34-E1")
t = get_token("/regularisation")
yday = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
r = client.post("/regularisation",
                data={"date": yday, "corrected_in": "09:05",
                      "corrected_out": "18:05",
                      "reason": "biometric missed",
                      "csrf_token": t}, follow_redirects=False)
with app.app_context():
    rr = RegularisationRequest.query.filter_by(
        user_id="T34-E1").order_by(RegularisationRequest.id.desc()).first()
    inst = approvals.get_instance("regularisation", rr.id)
    check("regularisation instance started",
          inst is not None and inst.current_step == 1)
logout()
login("T34-MGR1", "pw-T34-MGR1")
t = get_token("/regularisation/approve")
r = client.post("/regularisation/approve",
                data={"rid": rr.id, "action": "approve", "comment": "ok",
                      "csrf_token": t}, follow_redirects=False)
with app.app_context():
    db.session.expire_all()
    rr = RegularisationRequest.query.get(rr.id)
    inst = approvals.get_instance("regularisation", rr.id)
    check("regularisation step 1: still pending", rr.status == "pending")
    check("regularisation advanced to step 2", inst.current_step == 2)
logout()
login("T34-HRS", "pw-T34-HRS")
t = get_token("/regularisation/approve")
r = client.post("/regularisation/approve",
                data={"rid": rr.id, "action": "approve", "comment": "final",
                      "csrf_token": t}, follow_redirects=False)
with app.app_context():
    db.session.expire_all()
    rr = RegularisationRequest.query.get(rr.id)
    check("regularisation final: approved", rr.status == "approved")

# ================= 8) defaults seeded on fresh DB =================
with app.app_context():
    defaults = {p: [s["approver_type"] for s in
                    approvals.snapshot_steps(approvals.get_matrix(p))]
                for p in ("employee_creation", "leave", "regularisation")}
    check("default chains exist for all processes",
          set(defaults) == {"employee_creation", "leave", "regularisation"}
          and all(defaults[p] for p in defaults))

print()
print(f"{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
