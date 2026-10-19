"""End-to-end test of the ESS portal using Flask's test client."""
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import User, LeaveRequest, Grievance, Evaluation  # noqa: E402

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


def dob_pw(emp_id):
    import openpyxl
    wb = openpyxl.load_workbook("../user/files/Employees_List__2.xlsx", read_only=True, data_only=True)
    for row in wb.active.iter_rows(values_only=True):
        if str(row[0]).strip() == emp_id:
            from datetime import datetime
            d = datetime.strptime(row[13].strip(), "%d-%b-%Y")
            return f"{d.day:02d}{d.month:02d}{d.year}"
    raise ValueError(emp_id)


def login(emp_id, pw):
    r = client.get("/login")
    t = csrf_token(r)
    return client.post("/login", data={"username": emp_id, "password": pw, "csrf_token": t},
                       follow_redirects=False)


# 1. Wrong password rejected
r = login("AA670", "wrongpass")
check("wrong password rejected", r.status_code == 200 and b"Invalid" in r.data)

# 2. Correct DOB login -> forced to change password
pw_admin = dob_pw("AA670")
r = login("AA670", pw_admin)
check("DOB login redirects to change-password",
      r.status_code == 302 and "/change-password" in r.headers["Location"], r.status_code)

# 3. Dashboard blocked until password changed
r = client.get("/", follow_redirects=False)
check("dashboard blocked pre-change", r.status_code == 302 and "/change-password" in r.headers["Location"])

# 4. Change password (weak rejected, strong accepted)
r = client.get("/change-password")
t = csrf_token(r)
r = client.post("/change-password",
                data={"current_password": pw_admin, "new_password": "weak",
                      "confirm_password": "weak", "csrf_token": t})
check("weak password rejected", b"at least" in r.data)
r = client.post("/change-password",
                data={"current_password": pw_admin, "new_password": "StrongPass1",
                      "confirm_password": "StrongPass1", "csrf_token": t},
                follow_redirects=True)
check("password changed -> dashboard", b"Welcome" in r.data)

# 5. Dashboard renders
check("dashboard ok", r.status_code == 200)

# 6. Leave pages
r = client.get("/leave")
check("leave page ok", r.status_code == 200 and b"Apply for Leave" in r.data)

# 7. Apply leave
t = csrf_token(r)
s = (date.today() + timedelta(days=10)).isoformat()
e = (date.today() + timedelta(days=12)).isoformat()
r = client.post("/leave", data={"leave_type": "annual", "start_date": s, "end_date": e,
                                "reason": "test", "csrf_token": t}, follow_redirects=True)
check("leave applied", b"submitted" in r.data)
with app.app_context():
    lr = LeaveRequest.query.filter_by(user_id="AA670").first()
check("leave in db", lr is not None and lr.days == 3)

# 8. Leave balance over-limit rejected
r = client.get("/leave"); t = csrf_token(r)
s2 = (date.today() + timedelta(days=30)).isoformat()
e2 = (date.today() + timedelta(days=90)).isoformat()
r = client.post("/leave", data={"leave_type": "annual", "start_date": s2, "end_date": e2,
                                "reason": "too long", "csrf_token": t}, follow_redirects=True)
check("over-limit leave rejected", b"Insufficient" in r.data)

# 9. Attendance, payslip, profile pages
for url, marker in [("/attendance", b"Attendance"), ("/payslip", b"Payslips"),
                    (f"/payslip/{date.today().year}/{date.today().month}", b"Net Pay"),
                    ("/profile", b"My Profile"), ("/grievances", b"Raise a Grievance"),
                    ("/evaluations", b"Performance Evaluations")]:
    r = client.get(url)
    check(f"GET {url}", r.status_code == 200 and marker in r.data, r.status_code)

# 10. Grievance submit
r = client.get("/grievances"); t = csrf_token(r)
r = client.post("/grievances", data={"category": "Working Hours & Leave", "subject": "test grievance",
                                     "description": "test desc", "csrf_token": t}, follow_redirects=True)
check("grievance submitted", b"confidentially" in r.data)
with app.app_context():
    check("grievance in db", Grievance.query.filter_by(user_id="AA670").count() == 1)

# 11. Approvals page (admin sees all)
r = client.get("/approvals")
check("approvals page (admin)", r.status_code == 200 and b"Pending Leave Approvals" in r.data)

# 12. Approve the leave as a different approver.
# v3.4.1: self-approval is forbidden — the admin cannot approve their own
# leave request. Assign a reporting manager and approve as them.
with app.app_context():
    mgr = User(id="E2E-MGR", name="E2E Manager", role="manager",
               department="HR", employment_status="active", is_active=True,
               joining_date=date(2020, 1, 1))
    mgr.set_password("pw-E2E-MGR")
    mgr.must_change_password = False
    db.session.add(mgr)
    User.query.get("AA670").reporting_manager = "E2E-MGR"
    db.session.commit()
r = client.post("/logout", data={"csrf_token": csrf_token(client.get("/change-password"))})
r = client.get("/login"); t = csrf_token(r)
client.post("/login", data={"username": "E2E-MGR", "password": "pw-E2E-MGR",
                             "csrf_token": t}, follow_redirects=False)
with app.app_context():
    lr = LeaveRequest.query.filter_by(user_id="AA670", status="pending").first()
r = client.get("/approvals"); t = csrf_token(r)
r = client.post(f"/approvals/{lr.id}", data={"action": "approve", "comment": "ok", "csrf_token": t},
                follow_redirects=True)
check("leave approved", b"approved" in r.data)
with app.app_context():
    from app.models import LeaveBalance
    bal = LeaveBalance.query.filter_by(user_id="AA670", leave_type="annual").first()
check("balance deducted", bal.used == 3, bal.used)
# back to the admin for the remaining admin-only steps
client.post("/logout", data={"csrf_token": csrf_token(client.get("/change-password"))})
r = login("AA670", "StrongPass1")

# 13. Evaluation create (admin)
r = client.get("/evaluations/new/AA670")
check("evaluation form loads", r.status_code == 200 and b"Performance Evaluation" in r.data)
t = csrf_token(r)
data = {"period": "2026 H2", "remarks": "good", "csrf_token": t}
for i in range(8):
    data[f"rating_{i}"] = "4"
    data[f"comment_{i}"] = "Meets Expectations"
    data[f"comment_text_{i}"] = ""
r = client.post("/evaluations/new/AA670", data=data, follow_redirects=True)
check("evaluation saved", b"Evaluation saved" in r.data)
with app.app_context():
    ev = Evaluation.query.filter_by(user_id="AA670").first()
check("evaluation overall", ev is not None and ev.overall == 4.0, getattr(ev, "overall", None))

# 14. Employee acknowledges evaluation
r = client.post(f"/evaluations/{ev.id}/acknowledge", data={"csrf_token": t}, follow_redirects=True)
check("evaluation acknowledged", b"acknowledged" in r.data.lower())

# 15. Grievance inbox (admin)
r = client.get("/grievances/inbox")
check("grievance inbox", r.status_code == 200 and b"Grievance Inbox" in r.data)

# 16. Admin user management
r = client.get("/admin/users")
check("admin users page", r.status_code == 200 and b"User Management" in r.data)
r = client.get("/admin/users/AA670")
check("admin user detail", r.status_code == 200 and b"Temporary Password" in r.data or r.status_code == 200)
r = client.get("/admin/audit")
check("audit log page", r.status_code == 200 and b"Audit Log" in r.data)

# 17. CSRF enforced: POST without token
client.post("/logout", data={"csrf_token": csrf_token(client.get("/change-password"))})
r = client.post("/login", data={"username": "AA670", "password": "x"})
check("CSRF blocks tokenless POST", r.status_code == 400, r.status_code)

# 18. Non-admin cannot access admin
pw2 = dob_pw("AA")
r = login("AA", pw2)  # will redirect to change-password; change it
r = client.get("/change-password"); t = csrf_token(r)
client.post("/change-password", data={"current_password": pw2, "new_password": "StrongPass2",
                                      "confirm_password": "StrongPass2", "csrf_token": t})
r = client.get("/admin/users")
check("non-admin blocked from admin", r.status_code == 403, r.status_code)
r = client.get("/approvals")
check("non-manager blocked from approvals", r.status_code == 403, r.status_code)

print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
