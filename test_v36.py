"""HRMS+ESS v3.6 tests: Frappe-model desk shell, workspaces, HR Settings wiring.

Covers FRAPPE-CLONE-SPEC.md acceptance:
1. Desk shell per role: rail workspace icons filtered (admin sees all 11,
   employee sees only their workspaces, manager sees My Team).
2. /apps grid renders for all roles.
3. /api/awesome: auth required; role-filtered pages + employee matches;
   employees see only pages + their own record; never salary data.
4. Workspace homes render with live counts; role gating (403).
5. HR Settings page: admin-only, 6 tabs, save -> SettingKV + audit; the four
   wired toggles actually change behaviour (celebrations, leave
   notifications, mobile check-in, geolocation).
6. Employee tabbed form: 7 tabs render, create round-trip, audit timeline on
   the detail page; employees list shows "List View" + status pills + filters.
7. Dark theme toggle persists via the ess_theme cookie (server-rendered).
8. Doodle retired from desk pages; login page keeps the cream class.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v36.db && ESS_SECRET_KEY=testsecret12345 \\
       ESS_DATABASE_URL=sqlite:////tmp/test_v36.db \\
       ~/workspace/ess-app/venv/bin/python test_v36.py
"""
import io
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app import engagement  # noqa: E402
from app.models import (User, Notification, CelebrationPost, PunchRecord,  # noqa: E402
                        AuditLog, AttendanceRecord)
from app.settingsutil import get_setting, set_setting  # noqa: E402

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


def png_bytes(w=100, h=100, color="red"):
    from PIL import Image
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


with app.app_context():
    mk("T36-ADM", "T36 Admin", "admin", "Human Resources",
       is_hr_manager=True, can_view_salary=True)
    mk("T36-MGR", "T36 Manager", "manager", "Production")
    mk("T36-E1", "T36 Employee One", "employee", "Production",
       reporting_manager="T36-MGR")
    mk("T36-HRS", "T36 HR Staff", "employee", "Human Resources",
       is_hr_staff=True)
    db.session.commit()

# ================= 1) desk shell per role =================
login("T36-ADM", "pw-T36-ADM")
r = client.get("/")
check("admin dashboard 200", r.status_code == 200)
check("admin rail has HR Setup workspace", b"/ws/hr-setup" in r.data)
check("admin rail has Payroll workspace", b"/ws/payroll" in r.data)
check("admin rail has Me workspace", b"/ws/me" in r.data)
check("admin rail has My Team workspace", b"/ws/my-team" in r.data)
check("admin has Awesome Bar trigger (Ctrl+K)", b"awesome" in r.data.lower())
check("no doodle wallpaper on desk pages", b"doodle-bg.svg" not in r.data)
logout()

login("T36-E1", "pw-T36-E1")
r = client.get("/")
check("employee dashboard 200", r.status_code == 200)
check("employee rail has Me workspace", b"/ws/me" in r.data)
check("employee rail hides Payroll", b"/ws/payroll" not in r.data)
check("employee rail hides HR Setup", b"/ws/hr-setup" not in r.data)
check("employee sees notifications bell", b"notifications" in r.data.lower())
logout()

login("T36-MGR", "pw-T36-MGR")
r = client.get("/")
check("manager rail has My Team", b"/ws/my-team" in r.data)
check("manager rail hides Payroll", b"/ws/payroll" not in r.data)
logout()

# ================= 2) /apps grid =================
login("T36-E1", "pw-T36-E1")
r = client.get("/apps")
check("/apps 200 for employee", r.status_code == 200)
check("/apps shows Me card", b">Me<" in r.data or b"Me<" in r.data)
logout()
login("T36-ADM", "pw-T36-ADM")
r = client.get("/apps")
check("/apps 200 for admin", r.status_code == 200)
check("/apps shows HR Setup card", b"HR Setup" in r.data)

# ================= 3) /api/awesome =================
logout()
r = client.get("/api/awesome", query_string={"q": "leave"})
check("awesome requires auth", r.status_code == 302)
login("T36-ADM", "pw-T36-ADM")
r = client.get("/api/awesome", query_string={"q": "leave"})
j = r.get_json()
check("awesome 200 for admin", r.status_code == 200 and j is not None)
check("awesome returns pages group", isinstance(j.get("pages"), list) and len(j["pages"]) > 0)
check("awesome never exposes salary data", b"salary" not in r.data.lower()
      or all("salary" not in p["label"].lower() or "slip" in p["label"].lower()
             or "component" in p["label"].lower() for p in j["pages"]))
r = client.get("/api/awesome", query_string={"q": "T36-E1"})
j = r.get_json()
check("admin employee search finds the employee",
      any(e.get("id") == "T36-E1" for e in j.get("employees", [])))
check("employee search rows carry no pay fields",
      all("basic" not in str(e).lower() and "gross" not in str(e).lower()
          for e in j.get("employees", [])))
logout()
login("T36-E1", "pw-T36-E1")
r = client.get("/api/awesome", query_string={"q": "T36"})
j = r.get_json()
emp_ids = [e.get("id") for e in j.get("employees", [])]
check("employee sees only their own record", emp_ids == ["T36-E1"], str(emp_ids))
r = client.get("/api/awesome", query_string={"q": "payroll"})
j = r.get_json()
check("employee gets no admin pages in results",
      all("/hrms/payroll" not in p.get("href", "") for p in j.get("pages", [])))
logout()

# ================= 4) workspace homes =================
login("T36-ADM", "pw-T36-ADM")
r = client.get("/ws/hr-setup")
check("HR Setup home 200", r.status_code == 200)
check("HR Setup home shows live employee count", b"T36-E1" in r.data or b"Employees" in r.data)
r = client.get("/ws/me")
check("Me home 200 for admin", r.status_code == 200)
r = client.get("/ws/payroll")
check("Payroll home 200 for admin", r.status_code == 200)
logout()
login("T36-E1", "pw-T36-E1")
r = client.get("/ws/me")
check("Me home 200 for employee", r.status_code == 200)
r = client.get("/ws/payroll")
check("Payroll home 403 for employee", r.status_code == 403)
r = client.get("/ws/hr-setup")
check("HR Setup home 403 for employee", r.status_code == 403)
r = client.get("/ws/nope")
check("unknown workspace 404", r.status_code == 404)
logout()

# ================= 5) HR Settings =================
login("T36-E1", "pw-T36-E1")
r = client.get("/hrms/settings")
check("settings 403 for employee", r.status_code == 403)
logout()
login("T36-ADM", "pw-T36-ADM")
r = client.get("/hrms/settings")
check("settings 200 for admin", r.status_code == 200)
for tab in [b"Employee", b"Leaves", b"Expenses", b"Shift", b"Tenure", b"Recruitment"]:
    check(f"settings has {tab.decode()} tab", tab in r.data)
check("settings has celebrations toggle", b"celebrations_enabled" in r.data)
check("settings has dirty-watch form", b"data-dirty-watch" in r.data)
check("settings shows locked self-approval", b"Prevent self approval" in r.data)

# 5a) save toggles -> SettingKV + audit
# (v3.6.1: turning geolocation OFF now requires the step-up fields)
r = form_post("/hrms/settings", "/hrms/settings",
              {"celebrations_enabled": "0", "leave_notifications": "1",
               "mobile_checkin": "0", "geolocation": "0",
               "geo_stepup_password": "pw-T36-ADM",
               "geo_stepup_reason": "test v3.6 5a fixture"})
check("settings save redirects", r.status_code in (301, 302, 303))
with app.app_context():
    check("celebrations toggle persisted off", get_setting("celebrations_enabled") == "0")
    check("geolocation toggle persisted off", get_setting("geolocation") == "0")
    aud = AuditLog.query.filter_by(entity="setting", action="update").all()
    check("settings saves are audited", len(aud) >= 2, f"{len(aud)} entries")

# 5b) celebrations off -> no new posts
with app.app_context():
    before = CelebrationPost.query.count()
    new_posts = engagement.ensure_todays_celebrations()
    after = CelebrationPost.query.count()
    check("celebrations off creates no posts", new_posts == [] and after == before)
    # restore for later suites' fixtures (this DB is thrown away anyway)
    set_setting("celebrations_enabled", "1", "T36-ADM")
    db.session.commit()

# 5c) mobile check-in off -> punch page disabled message
r = client.get("/attendance/punch")
check("punch page shows disabled message when off",
      b"Mobile check-in is disabled" in r.data)
with app.app_context():
    set_setting("mobile_checkin", "1", "T36-ADM")
    db.session.commit()

# 5d) geolocation off -> punch accepted without coordinates (selfie still required)
photo = png_bytes(1600, 1200)
r = client.post("/attendance/punch/submit", data={
    "csrf_token": get_token("/attendance/punch"),
    "punch_type": "in", "photo": (io.BytesIO(photo), "selfie.png")},
    content_type="multipart/form-data")
j = r.get_json()
check("geo off: punch accepted with no coordinates",
      r.status_code == 200 and j and j.get("ok"))
with app.app_context():
    pr = PunchRecord.query.filter_by(user_id="T36-ADM", punch_type="in").first()
    check("geo off: punch record stored", pr is not None)
    set_setting("geolocation", "1", "T36-ADM")
    db.session.commit()
# selfie still mandatory
r = client.post("/attendance/punch/submit", data={
    "csrf_token": get_token("/attendance/punch"), "punch_type": "out"},
    content_type="multipart/form-data")
check("punch without selfie still rejected", r.status_code in (400, 403))

# 5e) leave notifications off -> engine leave notifications suppressed
with app.app_context():
    set_setting("leave_notifications", "0", "T36-ADM")
    db.session.commit()
    n_before = Notification.query.filter_by(user_id="T36-ADM").count()
    engagement.notify_user("T36-ADM", "approval", "Leave approved",
                           "Leave #1 approved.")
    n_after = Notification.query.filter_by(user_id="T36-ADM").count()
    check("leave notifications suppressed when off", n_after == n_before)
    engagement.notify_user("T36-ADM", "approval", "Expense approved",
                           "Expense claim approved.")
    n_final = Notification.query.filter_by(user_id="T36-ADM").count()
    check("non-leave notifications unaffected", n_final == n_after + 1)
    set_setting("leave_notifications", "1", "T36-ADM")
    db.session.commit()

# ================= 6) employee tabbed form + list =================
r = client.get("/hrms/employees/new")
check("employee form 200", r.status_code == 200)
for tab in [b"Overview", b"Address", b"Attendance", b"Salary", b"Personal",
            b"Documents", b"Joining"]:
    check(f"employee form has {tab.decode()} tab", tab in r.data)
check("employee form has dirty-watch", b"data-dirty-watch" in r.data)
r = form_post("/hrms/employees/new", "/hrms/employees/new",
              {"employee_id": "T36-NEW", "name": "T36 New Hire",
               "department": "Production", "designation": "Helper",
               "employment_status": "active"})
check("employee create redirects", r.status_code in (301, 302, 303))
with app.app_context():
    check("new employee exists", User.query.get("T36-NEW") is not None)
r = client.get("/hrms/employees/T36-NEW")
check("employee detail 200", r.status_code == 200)
check("employee detail has activity timeline",
      b"timeline" in r.data.lower() or b"Activity" in r.data)
r = client.get("/hrms/employees", query_string={"status": "active"})
check("employees list 200", r.status_code == 200)
check("employees list shows List View label", b"List View" in r.data)
check("employees list shows status pills", b"pill" in r.data)
check("employees list has filter row", b"filter-row" in r.data or b"name=\"status\"" in r.data)
logout()

# ================= 7) dark theme cookie =================
client.set_cookie("ess_theme", "dark")
r = client.get("/login")
check("dark theme persisted server-side", b'data-theme="dark"' in r.data)
check("login keeps cream class", b"authpage" in r.data)
client.delete_cookie("ess_theme")

# ================= 8) doodle retired =================
login("T36-ADM", "pw-T36-ADM")
r = client.get("/ws/me")
check("workspace pages have no doodle reference", b"doodle-bg.svg" not in r.data)

# ================= 9) bottom nav hidden on desktop (V3.6.2 regression) =================
# The mobile bottom bar must have a base display:none rule BEFORE the
# <=860px media query that enables it; without it the bar leaks onto
# desktop pages as an unstyled vertical icon column (user report
# 2026-10-19). Check the raw stylesheet structure.
import pathlib
_css = pathlib.Path("app/static/style.css").read_text()
_media_at = _css.find("@media (max-width: 860px)")
_base = _css[:_media_at] if _media_at > 0 else ""
check("bottomnav base rule hides it on desktop",
      ".bottomnav { display: none" in _base)
_mobile = _css[_media_at:] if _media_at > 0 else ""
check("bottomnav enabled inside mobile media query",
      ".bottomnav { display: flex" in _mobile)
r = client.get("/ws/me")
check("pages link the bumped stylesheet v=7", b"style.css?v=7" in r.data)

print()
print(f"V3.6: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
