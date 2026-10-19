"""HRMS+ESS v2 tests: new modules, advanced audit trail, approvals.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: ESS_SECRET_KEY=testsecret12345 ESS_DATABASE_URL=sqlite:////tmp/hrms_v2_test.db \
     python test_v2.py
"""
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import (User, RegularisationRequest, AttendanceRecord, Candidate,  # noqa: E402
                        OnboardingCase, OnboardingTask, AuditLog)

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


# ---- fixtures ----
with app.app_context():
    for uid, name, role, dept in [("TST-ADM", "Test Admin", "admin", "HR"),
                                  ("TST-MGR", "Test Manager", "manager", "TestDept"),
                                  ("TST-E1", "Test Employee One", "employee", "TestDept"),
                                  ("TST-E2", "Test Employee Two", "employee", "TestDept"),
                                  ("TST-EXP", "Test Expiry", "employee", "TestDept")]:
        if not User.query.get(uid):
            u = User(id=uid, name=name, department=dept, designation="Engineer",
                     role=role, must_change_password=False,
                     employment_status="active")
            u.set_password("AdminPass1" if uid == "TST-ADM" else
                           "ManagerPass1" if uid == "TST-MGR" else "EmployeePass1")
            db.session.add(u)
    exp = User.query.get("TST-EXP")
    exp.visa_expiry_date = date.today() + timedelta(days=10)
    db.session.commit()

# 1. Admin login
r = login("TST-ADM", "AdminPass1")
check("admin login", r.status_code == 302 and r.headers["Location"] == "/", r.status_code)

# 2. Employee create via HRMS -> DB + audit entries with values
r = client.get("/hrms/employees/new")
check("employee-new form loads", r.status_code == 200)
t = csrf_token(r)
r = client.post("/hrms/employees/new", data={
    "employee_id": "TST-NEW", "name": "New Joiner Test", "designation": "Engineer",
    "department": "TestDept", "nationality": "Indian",
    "visa_expiry_date": (date.today() + timedelta(days=200)).isoformat(),
    "employment_status": "active", "basic_salary": "1500", "monthly_salary": "3000",
    "csrf_token": t}, follow_redirects=True)
with app.app_context():
    u = User.query.get("TST-NEW")
    created = AuditLog.query.filter_by(entity="employee", entity_id="TST-NEW",
                                       action="employee_created").all()
    desig_rows = [a for a in created if a.field_name == "designation"]
check("employee created in db", u is not None and u.name == "New Joiner Test")
check("creation audit entries exist", len(created) >= 5, len(created))
check("creation audit carries field values",
      desig_rows and desig_rows[0].old_value == "" and desig_rows[0].new_value == "Engineer",
      desig_rows[0].new_value if desig_rows else "none")

# 3. Employee edit -> per-field old/new audit
r = client.get("/hrms/employees/TST-NEW/edit")
check("employee-edit form loads", r.status_code == 200)
t = csrf_token(r)
r = client.post("/hrms/employees/TST-NEW/edit", data={
    "name": "New Joiner Test", "designation": "Senior Engineer",
    "department": "TestDept", "nationality": "Indian",
    "visa_expiry_date": (date.today() + timedelta(days=200)).isoformat(),
    "employment_status": "active", "basic_salary": "1500", "monthly_salary": "3500",
    "csrf_token": t}, follow_redirects=True)
check("edit redirects to detail", b"New Joiner Test" in r.data)
with app.app_context():
    changed = AuditLog.query.filter_by(entity="employee", entity_id="TST-NEW",
                                       action="employee_updated").all()
    desig = [a for a in changed if a.field_name == "designation"]
    salary = [a for a in changed if a.field_name == "monthly_salary"]
check("edit audit rows created", len(changed) >= 2, len(changed))
check("edit audit old->new values",
      desig and desig[0].old_value == "Engineer" and desig[0].new_value == "Senior Engineer"
      and salary and salary[0].old_value == "3000.0" and salary[0].new_value == "3500.0",
      desig[0].new_value if desig else "none")

# 4. Audit CSV export returns 200 and contains the change
r = client.get("/admin/audit/export?entity=employee")
check("audit export 200", r.status_code == 200 and r.mimetype == "text/csv", r.status_code)
check("export contains change", b"Senior Engineer" in r.data and b"field" in r.data)
r = client.get("/admin/audit?entity=employee")
check("audit filter page ok", r.status_code == 200 and b"Senior Engineer" in r.data)

# 5. Expiry widget: visa expiring in 10 days appears on admin dashboard
# v3.8: the expiry widget is now the "Expiring visas" accordion plus the
# "Visa expiries · 20 days" row on the dark tasks card.
r = client.get("/")
check("expiry widget shows TST-EXP",
      b"Test Expiry" in r.data and b"Expiring visas" in r.data)

# 6. Employee 360 detail page
r = client.get("/hrms/employees/TST-NEW")
check("employee 360 page", r.status_code == 200 and b"Employee 360" in r.data)

# 7. Regularisation: employee applies
logout()
login("TST-E1", "EmployeePass1")
yday = (date.today() - timedelta(days=1)).isoformat()
r = client.get("/regularisation")
check("regularisation page ok", r.status_code == 200 and b"New Request" in r.data)
t = csrf_token(r)
r = client.post("/regularisation", data={"date": yday, "corrected_in": "08:05",
                                         "corrected_out": "18:10", "reason": "forgot punch",
                                         "csrf_token": t}, follow_redirects=True)
check("regularisation submitted", b"submitted" in r.data)
with app.app_context():
    rr = RegularisationRequest.query.filter_by(user_id="TST-E1").first()
check("regularisation in db", rr is not None and rr.status == "pending")

# 8. Manager approves regularisation; attendance record gets corrected times
logout()
login("TST-MGR", "ManagerPass1")
r = client.get("/regularisation/approve")
check("regularisation approvals page", r.status_code == 200 and b"TST-E1" in r.data)
t = csrf_token(r)
r = client.post("/regularisation/approve",
                data={"rid": rr.id, "action": "approve", "comment": "verified",
                      "csrf_token": t}, follow_redirects=True)
check("regularisation approved", b"approved and attendance updated" in r.data)
with app.app_context():
    rr2 = RegularisationRequest.query.get(rr.id)
    rec = AttendanceRecord.query.filter_by(user_id="TST-E1",
                                           date=rr2.date).first()
    raudit = AuditLog.query.filter_by(entity="regularisation",
                                      entity_id=str(rr.id)).first()
check("request status approved", rr2.status == "approved")
check("attendance corrected", rec is not None and rec.check_in == "08:05"
      and rec.check_out == "18:10",
      (rec.check_in, rec.check_out) if rec else "none")
check("approval audited", raudit is not None and raudit.new_value == "approved")

# 9. Recruitment: candidate create + stage move (audited)
logout()
login("TST-ADM", "AdminPass1")
r = client.get("/hrms/recruitment")
t = csrf_token(r)
r = client.post("/hrms/recruitment", data={"name": "Candidate Test", "position": "Welder",
                                           "phone": "+971500000000", "source": "Indeed",
                                           "notes": "test", "csrf_token": t},
                follow_redirects=True)
with app.app_context():
    c = Candidate.query.filter_by(name="Candidate Test").first()
check("candidate created", c is not None and c.stage == "Applied", c.id if c else "none")
r = client.get("/hrms/recruitment?stage=Applied")
check("candidate stage filter", b"Candidate Test" in r.data)
r = client.get(f"/hrms/recruitment/{c.id}")
t = csrf_token(r)
r = client.post(f"/hrms/recruitment/{c.id}", data={"action": "stage", "stage": "Interview",
                                                  "csrf_token": t}, follow_redirects=True)
with app.app_context():
    c2 = Candidate.query.get(c.id)
    moved = AuditLog.query.filter_by(entity="candidate", entity_id=str(c.id),
                                     field_name="stage").first()
check("stage moved", c2.stage == "Interview", c2.stage)
check("stage move audited old->new", moved is not None and moved.old_value == "Applied"
      and moved.new_value == "Interview",
      moved.old_value if moved else "none")

# 10. Onboarding: case create + task done
r = client.get("/hrms/onboarding")
t = csrf_token(r)
r = client.post("/hrms/onboarding", data={"name": "Joiner Test",
                                          "joining_date": (date.today() + timedelta(days=5)).isoformat(),
                                          "csrf_token": t}, follow_redirects=True)
with app.app_context():
    case = OnboardingCase.query.filter_by(name="Joiner Test").first()
    n_tasks = OnboardingTask.query.filter_by(case_id=case.id).count() if case else 0
check("onboarding case created", case is not None and n_tasks == 8, n_tasks)
r = client.get(f"/hrms/onboarding/{case.id}")
check("checklist rendered", b"Offer letter issued" in r.data)
t = csrf_token(r)
with app.app_context():
    first_task = OnboardingTask.query.filter_by(case_id=case.id).order_by(OnboardingTask.id).first()
r = client.post(f"/hrms/onboarding/{case.id}", data={"task_id": first_task.id, "csrf_token": t},
                follow_redirects=True)
with app.app_context():
    done = OnboardingTask.query.get(first_task.id).is_done
    cprog = OnboardingCase.query.get(case.id).progress
check("task marked done", done is True)
check("progress > 0", cprog > 0, cprog)

# 11. Non-admin blocked from HRMS
logout()
login("TST-E1", "EmployeePass1")
r = client.get("/hrms/employees")
check("non-admin blocked from employee central", r.status_code == 403, r.status_code)
r = client.get("/admin/audit")
check("non-admin blocked from audit", r.status_code == 403, r.status_code)

# 12. Regularisation visible to employee
r = client.get("/regularisation")
check("employee sees own request", r.status_code == 200 and b"forgot punch" in r.data)

print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
