"""HRMS+ESS v3.5 tests: helpdesk tickets + shift swaps.

PART A — Helpdesk tickets (distinct from Grievances):
1. Create, assign (requester notified), public vs internal comments
   (internal notes invisible to the requester), resolve/close/reopen.
2. Access control: employee cannot view another employee's ticket.
3. SLA overdue flag on the staff queue; category filter.

PART B — Shift swaps (approval-engine process "shift_swap"):
4. Creation validation: counterpart assignment must be theirs, assignments
   must be future, patterns must differ, no duplicate open swaps.
5. Counterpart consent: accept starts the engine chain; reject ends it and
   notifies the requester.
6. Approval chain: non-resolver 403; final approval exchanges the two
   RosterAssignment pattern ids; staleness (roster changed) aborts the
   swap and keeps engine + swap consistent; cancel closes the instance.
7. Rest-gap advisory flag on the approvals page.
8. Inbox counts include pending swaps for resolvers; /my-swaps shows
   consent requests for the counterpart.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v35.db && ESS_SECRET_KEY=testsecret12345 \\
ESS_DATABASE_URL=<redacted>
"""
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app import approvals  # noqa: E402
from app.models import (User, ShiftPattern, RosterAssignment,  # noqa: E402
                        HelpdeskTicket, TicketComment, ShiftSwapRequest,
                        ApprovalInstance, Notification, ApprovalStep)

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


def form_post(post_path, token_path, data):
    """POST with a CSRF token fetched from a GET-rendering page."""
    t = get_token(token_path)
    data = dict(data)
    data["csrf_token"] = t
    return client.post(post_path, data=data, follow_redirects=False)


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


def set_chain(process_key, types):
    m = approvals.get_matrix(process_key)
    ApprovalStep.query.filter_by(matrix_id=m.id).delete()
    for i, atype in enumerate(types, start=1):
        db.session.add(ApprovalStep(matrix_id=m.id, step_no=i,
                                    approver_type=atype))
    db.session.commit()


def mkpat(code, st, et):
    p = ShiftPattern.query.filter_by(code=code).first()
    if not p:
        p = ShiftPattern(code=code, name=code + " shift", start_time=st,
                         end_time=et)
        db.session.add(p)
        db.session.flush()
    return p


def mkassign(uid, d, pat):
    a = RosterAssignment(user_id=uid, date=d, shift_id=pat.id,
                         created_by="T35-ADM")
    db.session.add(a)
    db.session.flush()
    return a


GST_TODAY = (datetime.utcnow() + timedelta(hours=4)).date()

with app.app_context():
    mk("T35-ADM", "T35 Admin", "admin", "Human Resources")
    mk("T35-MGR", "T35 Manager", "manager", "Production")
    mk("T35-E1", "T35 Employee One", "employee", "Production",
       reporting_manager="T35-MGR")
    mk("T35-E2", "T35 Employee Two", "employee", "Production",
       reporting_manager="T35-MGR")
    mk("T35-E3", "T35 Employee Three", "employee", "Production",
       reporting_manager="T35-MGR")
    mk("T35-HRS", "T35 HR Staff", "employee", "Human Resources",
       is_hr_staff=True)
    morn = mkpat("T35-MORN", "06:00", "14:00")
    night = mkpat("T35-NIGHT", "22:00", "06:00")  # crosses midnight
    eve = mkpat("T35-EVE", "14:00", "22:00")
    D1 = GST_TODAY + timedelta(days=1)
    D2 = GST_TODAY + timedelta(days=2)
    D3 = GST_TODAY + timedelta(days=3)
    D4 = GST_TODAY + timedelta(days=4)
    # Happy-path pair (D4): E1 MORN, E2 EVE
    mkassign("T35-E1", D4, morn)
    mkassign("T35-E2", D4, eve)
    # Rest-gap fixture (D1/D2/D3):
    # E1: NIGHT on D1 and D2; E2: MORN on D1, D2, D3.
    # Swap E1-D2-NIGHT <-> E2-D2-MORN:
    #   E1 gets MORN after a NIGHT day -> flag; E2 gets NIGHT with MORN on
    #   the next day starting 06:00 -> flag.
    mkassign("T35-E1", D1, night)
    mkassign("T35-E1", D2, night)
    mkassign("T35-E2", D1, morn)
    mkassign("T35-E2", D2, morn)
    mkassign("T35-E2", D3, morn)
    db.session.commit()
    set_chain("shift_swap", ["reporting_manager", "hr_staff"])

# ================= PART A: helpdesk tickets =================
login("T35-E1", "pw-T35-E1")
r = form_post("/my-tickets", "/my-tickets",
              {"category": "IT", "subject": "VPN down",
               "description": "VPN keeps dropping.", "priority": "Normal"})
with app.app_context():
    t1 = HelpdeskTicket.query.filter_by(
        requester_id="T35-E1", subject="VPN down").first()
    check("ticket created open", t1 is not None and t1.status == "open",
          f"#{t1.id}" if t1 else "")
    tid = t1.id
r = client.get("/my-tickets")
check("ticket listed for requester", b"VPN down" in r.data)
check("grievance redirect note shown",
      b"Grievances" in r.data and b"grievance" in r.data.lower())
logout()

login("T35-E2", "pw-T35-E2")
r = client.get(f"/my-tickets/{tid}")
check("employee cannot view another's ticket (403)", r.status_code == 403)
logout()

login("T35-ADM", "pw-T35-ADM")
r = form_post(f"/hrms/tickets/{tid}/assign", f"/hrms/tickets/{tid}",
              {"assignee_id": "T35-HRS"})
with app.app_context():
    t1 = HelpdeskTicket.query.get(tid)
    check("ticket assigned", t1.assignee_id == "T35-HRS")
    n = Notification.query.filter_by(
        user_id="T35-E1").order_by(Notification.id.desc()).first()
    check("requester notified on assignment",
          n is not None and f"#{tid}" in (n.title or ""))
r = form_post(f"/hrms/tickets/{tid}/comment", f"/hrms/tickets/{tid}",
              {"body": "PUBLIC-COMMENT-ABC", "is_internal": ""})
r = form_post(f"/hrms/tickets/{tid}/comment", f"/hrms/tickets/{tid}",
              {"body": "SECRET-INTERNAL-XYZ", "is_internal": "1"})
logout()

login("T35-E1", "pw-T35-E1")
r = client.get(f"/my-tickets/{tid}")
check("public comment visible to requester", b"PUBLIC-COMMENT-ABC" in r.data)
check("internal note NOT visible to requester",
      b"SECRET-INTERNAL-XYZ" not in r.data)
logout()

login("T35-ADM", "pw-T35-ADM")
r = client.get(f"/hrms/tickets/{tid}")
check("internal note visible to staff", b"SECRET-INTERNAL-XYZ" in r.data)
r = form_post(f"/hrms/tickets/{tid}/status", f"/hrms/tickets/{tid}",
              {"status": "resolved"})
with app.app_context():
    check("resolved sets resolved_at",
          HelpdeskTicket.query.get(tid).resolved_at is not None)
logout()

login("T35-E1", "pw-T35-E1")
r = form_post(f"/my-tickets/{tid}/close", f"/my-tickets/{tid}", {})
with app.app_context():
    check("requester closes resolved ticket",
          HelpdeskTicket.query.get(tid).status == "closed")
r = form_post(f"/my-tickets/{tid}/reopen", f"/my-tickets/{tid}", {})
with app.app_context():
    check("requester reopens closed ticket",
          HelpdeskTicket.query.get(tid).status == "open")
logout()

# SLA: backdated Urgent ticket shows overdue on staff queue
with app.app_context():
    old = HelpdeskTicket(requester_id="T35-E2", category="IT",
                         subject="SLA-SERVER-DOWN", description="x",
                         priority="Urgent", status="open")
    old.created_at = datetime.utcnow() - timedelta(hours=30)
    db.session.add(old)
    fresh = HelpdeskTicket(requester_id="T35-E2", category="HR",
                           subject="SLA-FRESH-HR", description="y",
                           priority="Normal", status="open")
    db.session.add(fresh)
    db.session.commit()
login("T35-ADM", "pw-T35-ADM")
r = client.get("/hrms/tickets")
check("overdue SLA flagged on queue",
      b"SLA-SERVER-DOWN" in r.data and b"SLA overdue" in r.data)
check("fresh Normal ticket not flagged overdue",
      b"SLA-FRESH-HR" in r.data and r.data.count(b"SLA overdue") == 1)
r = client.get("/hrms/tickets?category=IT")
check("queue category filter",
      b"SLA-SERVER-DOWN" in r.data and b"SLA-FRESH-HR" not in r.data)
logout()

# ================= PART B: shift swaps =================
def get_assign(uid, d):
    with app.app_context():
        return RosterAssignment.query.filter_by(user_id=uid, date=d).first()


A_E1 = get_assign("T35-E1", D4)   # MORN
A_E2 = get_assign("T35-E2", D4)   # EVE

login("T35-E1", "pw-T35-E1")
# validation: counterpart assignment is not theirs
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": A_E1.id,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": A_E1.id, "reason": "x"})
with app.app_context():
    n = ShiftSwapRequest.query.filter_by(
        requester_id="T35-E1", status="pending_counterpart").count()
    check("counterpart assignment must belong to counterpart", n == 0)
# validation: past-date assignment
with app.app_context():
    past = mkassign("T35-E1", GST_TODAY - timedelta(days=1),
                    ShiftPattern.query.filter_by(code="T35-MORN").first())
    pid = past.id
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": pid,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": A_E2.id, "reason": "x"})
with app.app_context():
    n = ShiftSwapRequest.query.filter_by(
        requester_id="T35-E1", status="pending_counterpart").count()
    check("past-date assignment rejected", n == 0)
# validation: same pattern (temp E2 MORN assignment on D5)
with app.app_context():
    other = mkassign("T35-E2", GST_TODAY + timedelta(days=5),
                     ShiftPattern.query.filter_by(code="T35-MORN").first())
    oid = other.id
    db.session.commit()
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": A_E1.id,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": oid, "reason": "x"})
with app.app_context():
    n = ShiftSwapRequest.query.filter_by(
        requester_id="T35-E1", status="pending_counterpart").count()
    check("same pattern rejected", n == 0)
    db.session.delete(db.session.get(RosterAssignment, oid))
    db.session.commit()
logout()

# happy path: create -> consent -> chain -> final approval exchanges patterns
login("T35-E1", "pw-T35-E1")
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": A_E1.id,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": A_E2.id, "reason": "family event"})
with app.app_context():
    s = ShiftSwapRequest.query.filter_by(
        requester_id="T35-E1").order_by(
        ShiftSwapRequest.id.desc()).first()
    check("swap created pending_counterpart",
          s is not None and s.status == "pending_counterpart", f"#{s.id}")
    sid = s.id
    check("pattern snapshots stored",
          s.my_pattern_id == A_E1.shift_id
          and s.their_pattern_id == A_E2.shift_id)
    n = Notification.query.filter_by(user_id="T35-E2").order_by(
        Notification.id.desc()).first()
    check("counterpart notified", n is not None and "swap" in n.title.lower())
# duplicate open swap rejected
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": A_E1.id,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": A_E2.id, "reason": "dup"})
with app.app_context():
    n = ShiftSwapRequest.query.filter(
        ShiftSwapRequest.requester_id == "T35-E1",
        ShiftSwapRequest.status.in_(("pending_counterpart",
                                    "pending_approval"))).count()
    check("duplicate open swap rejected", n == 1)
logout()

# counterpart rejects variant
login("T35-E2", "pw-T35-E2")
r = form_post(f"/my-swaps/{sid}/consent", f"/my-swaps/{sid}",
              {"action": "reject", "reason": "cannot"})
with app.app_context():
    s = ShiftSwapRequest.query.get(sid)
    check("counterpart reject ends swap", s.status == "rejected")
    n = Notification.query.filter_by(user_id="T35-E1").order_by(
        Notification.id.desc()).first()
    check("requester notified of decline",
          n is not None and "declined" in n.title.lower())
logout()

# fresh swap for the accept path
login("T35-E1", "pw-T35-E1")
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": A_E1.id,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": A_E2.id, "reason": "family event 2"})
with app.app_context():
    s = ShiftSwapRequest.query.filter_by(
        requester_id="T35-E1", status="pending_counterpart").order_by(
        ShiftSwapRequest.id.desc()).first()
    sid2 = s.id
logout()
login("T35-E2", "pw-T35-E2")
r = form_post(f"/my-swaps/{sid2}/consent", f"/my-swaps/{sid2}",
              {"action": "accept"})
with app.app_context():
    s = ShiftSwapRequest.query.get(sid2)
    check("counterpart accept -> pending_approval",
          s.status == "pending_approval")
    inst = ApprovalInstance.query.filter_by(
        process_key="shift_swap", ref_id=str(sid2)).first()
    check("engine instance started at step 1",
          inst is not None and inst.status == "pending"
          and inst.current_step == 1)
logout()

# a non-resolver cannot decide
login("T35-E3", "pw-T35-E3")
r = form_post(f"/approvals/swap/{sid2}", "/my-swaps", {"action": "approve"})
check("non-resolver cannot approve swap", r.status_code == 403)
logout()

# manager approves step 1, hr staff final-approves -> patterns exchanged
login("T35-MGR", "pw-T35-MGR")
r = client.get("/inbox")
check("inbox shows pending swap for resolver", b"Shift Swaps" in r.data)
r = form_post(f"/approvals/swap/{sid2}", "/approvals",
              {"action": "approve", "comment": "ok"})
with app.app_context():
    inst = ApprovalInstance.query.filter_by(
        process_key="shift_swap", ref_id=str(sid2)).first()
    check("step 1 approved, chain advanced",
          inst.status == "pending" and inst.current_step == 2)
logout()
login("T35-HRS", "pw-T35-HRS")
r = form_post(f"/approvals/swap/{sid2}", "/approvals", {"action": "approve"})
with app.app_context():
    s = ShiftSwapRequest.query.get(sid2)
    a1 = RosterAssignment.query.get(A_E1.id)
    a2 = RosterAssignment.query.get(A_E2.id)
    check("final approval executes exchange",
          s.status == "approved" and a1.shift_id == A_E2.shift_id
          and a2.shift_id == A_E1.shift_id)
logout()

# rest-gap fixture: swap E1-D2-NIGHT <-> E2-D2-MORN
with app.app_context():
    E1D = RosterAssignment.query.filter_by(
        user_id="T35-E1", date=D2,
        shift_id=ShiftPattern.query.filter_by(
            code="T35-NIGHT").first().id).first()
    E2D = RosterAssignment.query.filter_by(
        user_id="T35-E2", date=D2,
        shift_id=ShiftPattern.query.filter_by(
            code="T35-MORN").first().id).first()
login("T35-E1", "pw-T35-E1")
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": E1D.id,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": E2D.id, "reason": "rg"})
with app.app_context():
    rg = ShiftSwapRequest.query.filter_by(
        reason="rg").order_by(ShiftSwapRequest.id.desc()).first()
    rgid = rg.id
logout()
login("T35-E2", "pw-T35-E2")
r = form_post(f"/my-swaps/{rgid}/consent", f"/my-swaps/{rgid}",
              {"action": "accept"})
logout()
login("T35-MGR", "pw-T35-MGR")
r = client.get("/approvals")
check("rest-gap flag shown on approvals page", b"Rest-gap risk" in r.data)
logout()

# staleness: roster changes after consent -> final approval aborts
with app.app_context():
    E1D3 = mkassign("T35-E1", GST_TODAY + timedelta(days=6),
                    ShiftPattern.query.filter_by(code="T35-EVE").first())
    E2D3 = mkassign("T35-E2", GST_TODAY + timedelta(days=6),
                    ShiftPattern.query.filter_by(code="T35-MORN").first())
    e1id, e2id = E1D3.id, E2D3.id
    db.session.commit()
login("T35-E1", "pw-T35-E1")
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": e1id,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": e2id, "reason": "stale"})
assert r.status_code in (301, 302, 303, 307, 308), r.status_code
with app.app_context():
    st = ShiftSwapRequest.query.filter_by(
        reason="stale").order_by(ShiftSwapRequest.id.desc()).first()
    stid = st.id
logout()
login("T35-E2", "pw-T35-E2")
r = form_post(f"/my-swaps/{stid}/consent", f"/my-swaps/{stid}",
              {"action": "accept"})
logout()
# roster changes before the approvers act
with app.app_context():
    a = RosterAssignment.query.get(e1id)
    a.shift_id = ShiftPattern.query.filter_by(code="T35-NIGHT").first().id
    db.session.commit()
login("T35-MGR", "pw-T35-MGR")
r = form_post(f"/approvals/swap/{stid}", "/approvals",
              {"action": "approve"})
logout()
login("T35-HRS", "pw-T35-HRS")
r = form_post(f"/approvals/swap/{stid}", "/approvals",
              {"action": "approve"})
with app.app_context():
    s = ShiftSwapRequest.query.get(stid)
    inst = ApprovalInstance.query.filter_by(
        process_key="shift_swap", ref_id=str(stid)).first()
    check("stale swap rejected with roster-changed reason",
          s.status == "rejected"
          and s.reason == "Roster changed since request")
    check("engine instance consistent (rejected)",
          inst is not None and inst.status == "rejected")
logout()

# cancel during pending_approval closes the instance
with app.app_context():
    E1D4 = mkassign("T35-E1", GST_TODAY + timedelta(days=7),
                    ShiftPattern.query.filter_by(code="T35-MORN").first())
    E2D4 = mkassign("T35-E2", GST_TODAY + timedelta(days=7),
                    ShiftPattern.query.filter_by(code="T35-EVE").first())
    c1id, c2id = E1D4.id, E2D4.id
    db.session.commit()
login("T35-E1", "pw-T35-E1")
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": c1id,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": c2id, "reason": "cancelme"})
with app.app_context():
    cs = ShiftSwapRequest.query.filter_by(
        reason="cancelme").order_by(ShiftSwapRequest.id.desc()).first()
    csid = cs.id
logout()
login("T35-E2", "pw-T35-E2")
r = form_post(f"/my-swaps/{csid}/consent", f"/my-swaps/{csid}",
              {"action": "accept"})
logout()
login("T35-MGR", "pw-T35-MGR")
with app.app_context():
    mgr = User.query.get("T35-MGR")
    ids = [i.ref_id for i in approvals.inbox_for(mgr)]
    check("pending swap in resolver inbox before cancel",
          str(csid) in ids)
logout()
login("T35-E1", "pw-T35-E1")
r = form_post(f"/my-swaps/{csid}/cancel", f"/my-swaps/{csid}", {})
with app.app_context():
    s = ShiftSwapRequest.query.get(csid)
    inst = ApprovalInstance.query.filter_by(
        process_key="shift_swap", ref_id=str(csid)).first()
    check("cancel marks swap cancelled", s.status == "cancelled")
    check("cancel closes engine instance",
          inst is not None and inst.status == "rejected")
logout()
login("T35-MGR", "pw-T35-MGR")
with app.app_context():
    mgr = User.query.get("T35-MGR")
    ids = [i.ref_id for i in approvals.inbox_for(mgr)]
    check("cancelled swap gone from resolver inbox",
          str(csid) not in ids)
logout()

# consent stage visible to counterpart in /my-swaps and /inbox
with app.app_context():
    E1D5 = mkassign("T35-E1", GST_TODAY + timedelta(days=8),
                    ShiftPattern.query.filter_by(code="T35-MORN").first())
    E2D5 = mkassign("T35-E2", GST_TODAY + timedelta(days=8),
                    ShiftPattern.query.filter_by(code="T35-EVE").first())
    k1, k2 = E1D5.id, E2D5.id
    db.session.commit()
login("T35-E1", "pw-T35-E1")
r = form_post("/my-swaps", "/my-swaps",
              {"my_assignment_id": k1,
               "counterpart_user_id": "T35-E2",
               "their_assignment_id": k2, "reason": "consentview"})
logout()
login("T35-E2", "pw-T35-E2")
r = client.get("/my-swaps")
check("counterpart sees consent request", b"consentview" in r.data)
r = client.get("/inbox")
check("inbox counts consent request", b"Shift Swaps" in r.data)
logout()

# ============ SUMMARY ============
print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
