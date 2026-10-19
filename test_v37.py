"""HRMS+ESS v3.7 tests: App-UI shell (top bar + drawer), home, animations.

Covers V37-APP-UI-SPEC.md acceptance (v3.8: shell + home markup updated):
1. Shell markup: no .rail / .ws-sidebar classes; top bar has hamburger
   (iconbtn-hamburger), search opener, bell and avatar menu; pill nav
   (Dashboard, People, Attendance, Calendar, More; Payroll for admins,
   Approvals for managers/admins) — the v3.7 desktop text links were
   removed in v3.8.
2. Drawer: renders role-filtered groups from the workspaces registry;
   admin sees the HR Settings link, a plain employee does not.
3. Home per role (v3.8 dashboard rebuild): "Welcome in, <first name>"
   greeting; KPI strip; profile card; punch button; dark tasks card
   ("My tasks"); employee pill nav has no Payroll/Approvals; admin pill
   nav has Payroll + settings gear; manager pill nav has Approvals with
   the pending badge.
4. count-up [data-count] present on the manager/admin KPI numbers;
   animation CSS present (@keyframes tile-rise, .pjax-enter) plus a
   prefers-reduced-motion block; .bottomnav base display:none rule intact
   before the <=860px media query (V3.6.2 regression guard).
5. /apps renders grouped tiles per area.
6. Every /ws/<key> home returns 200 for admin.
7. Asset versions: style.css?v=10, app.js?v=5, desk.js?v=3 (v3.8 bump).

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v37.db && ESS_SECRET_KEY=testsecret12345 \\
       ESS_DATABASE_URL=sqlite:////tmp/test_v37.db \\
       ~/workspace/ess-app/venv/bin/python test_v37.py
"""
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import User, LeaveRequest  # noqa: E402

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


def topbar_links(html):
    """The pill-nav block of the top bar only (v3.8: .topbar-links removed)."""
    m = re.search(rb'<nav class="pillnav".*?</nav>', html, re.S)
    return m.group(0) if m else b""


with app.app_context():
    mk("HRADMIN", "T37 HR Admin", "admin", "Human Resources",
       is_hr_manager=True, can_view_salary=True)
    mk("MGR001", "T37 Manager", "manager", "Production")
    mk("EMP001", "T37 Employee One", "employee", "Production",
       reporting_manager="MGR001")
    # One pending leave -> manager Approvals badge > 0 and the employee's
    # own Apply Leave tile badge.
    if not LeaveRequest.query.filter_by(user_id="EMP001",
                                        status="pending").first():
        db.session.add(LeaveRequest(
            user_id="EMP001", leave_type="annual",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=11),
            days=2.0, reason="v3.7 fixture", status="pending"))
    db.session.commit()

# ================= 1) shell: no rail/sidebar, top bar =================
login("HRADMIN", "pw-HRADMIN")
r = client.get("/")
check("dashboard 200", r.status_code == 200)
check("no .rail class in shell markup", b'class="rail"' not in r.data)
check("no .ws-sidebar class in shell markup",
      b'class="ws-sidebar"' not in r.data)
check("top bar has hamburger", b'id="navToggle"' in r.data
      and b'iconbtn-hamburger' in r.data)
check("top bar has search opener", b'id="awesomeOpen"' in r.data)
check("top bar has notifications bell", b'id="notifOpen"' in r.data)
check("top bar has avatar menu",
      b'id="avatarBtn"' in r.data and b'id="avatarMenu"' in r.data)
check("avatar menu has My Profile", b">My Profile<" in r.data)
check("avatar menu has Theme", b">Theme<" in r.data)
check("avatar menu has HR Settings (admin)", b">HR Settings<" in r.data)
tb = topbar_links(r.data)
check("pill nav: Dashboard", b">Dashboard<" in tb)
check("pill nav: People", b">People<" in tb)
check("pill nav: Attendance", b">Attendance<" in tb)
check("pill nav: Calendar", b">Calendar<" in tb)
check("pill nav: More", b">More<" in tb)
check("pill nav: Payroll (admin)", b">Payroll<" in tb)

# ================= 2) drawer: role-filtered groups =================
check("drawer renders", b'id="drawer"' in r.data
      and b'class="drawer"' in r.data)
check("drawer has scrim", b'id="drawerScrim"' in r.data)
check("drawer renders collapsible groups", b'class="d-group"' in r.data)
r = client.get("/ws/hr-setup")
check("drawer on hr-setup shows Masters group", b">Masters<" in r.data)
check("drawer on hr-setup shows HR Settings link",
      b">HR Settings<" in r.data)
logout()

login("EMP001", "pw-EMP001")
r = client.get("/")
tb = topbar_links(r.data)
check("employee pill nav shows Dashboard",
      b">Dashboard<" in tb)
check("employee pill nav hides Payroll", b">Payroll<" not in tb)
check("employee pill nav hides Approvals", b">Approvals<" not in tb)
check("employee drawer renders", b'id="drawer"' in r.data)
check("employee drawer hides HR Settings", b"HR Settings" not in r.data)
logout()

login("MGR001", "pw-MGR001")
r = client.get("/")
tb = topbar_links(r.data)
check("manager pill nav shows Approvals", b">Approvals<" in tb)
logout()

# ================= 3) home per role (v3.8 dashboard) ====================
login("EMP001", "pw-EMP001")
r = client.get("/")
check("employee home 200", r.status_code == 200)
check("employee greeting shows first name",
      b"Welcome in, T37" in r.data)
check("employee sees punch button", b">Check In<" in r.data)
check("employee sees KPI strip", b'class="kpi-strip"' in r.data)
check("employee sees profile card",
      b'class="card profile-card mord-profile"' in r.data)
check("employee sees dark tasks card",
      b'class="card tasks-dark mord-tasks"' in r.data)
check("employee has no Payroll pill",
      b">Payroll<" not in topbar_links(r.data))
logout()

login("HRADMIN", "pw-HRADMIN")
r = client.get("/")
check("admin home 200", r.status_code == 200)
check("admin sees Payroll pill",
      b">Payroll<" in topbar_links(r.data))
check("admin sees settings gear", b"iconbtn-settings" in r.data)
check("admin KPI numbers have count-up stats", b"data-count" in r.data)
check("admin dashboard shows pending approvals",
      b"Pending approvals" in r.data)
logout()

login("MGR001", "pw-MGR001")
r = client.get("/")
check("manager home 200", r.status_code == 200)
check("manager sees Approvals pill", b">Approvals<" in topbar_links(r.data))
m = re.search(rb">Approvals<span class=\"badge\">(\d+)</span>",
              topbar_links(r.data))
check("manager Approvals pill badge > 0",
      m is not None and int(m.group(1)) > 0,
      f"badge={m.group(1).decode() if m else 'none'}")
check("manager sees My tasks card", b">My tasks<" in r.data)
check("manager KPI numbers have count-up stats", b"data-count" in r.data)
check("manager dashboard shows team counts",
      b"Team present today" in r.data and b"On leave today" in r.data)
logout()

# ================= 4) animation CSS + bottomnav regression guard =====
import pathlib  # noqa: E402
_css = pathlib.Path("app/static/style.css").read_text()
check("tile-rise keyframes present", "@keyframes tile-rise" in _css)
check("pjax page-enter class present", ".pjax-enter" in _css)
check("prefers-reduced-motion block present",
      "prefers-reduced-motion" in _css)
_media_at = _css.find("@media (max-width: 860px)")
_base = _css[:_media_at] if _media_at > 0 else ""
check("bottomnav base rule hides it on desktop",
      ".bottomnav { display: none" in _base)
_mobile = _css[_media_at:] if _media_at > 0 else ""
check("bottomnav enabled inside mobile media query",
      ".bottomnav { display: flex" in _mobile)

# ================= 5) /apps: grouped tiles per area =================
login("EMP001", "pw-EMP001")
r = client.get("/apps")
check("/apps 200 for employee", r.status_code == 200)
check("/apps renders grouped sections", b"ws-section" in r.data)
check("/apps employee sees Me section", b">Me<" in r.data)
check("/apps renders tile grid", b'class="tile-grid"' in r.data)
logout()
login("HRADMIN", "pw-HRADMIN")
r = client.get("/apps")
check("/apps 200 for admin", r.status_code == 200)
check("/apps admin sees HR Setup section", b"HR Setup" in r.data)
check("/apps admin sees Masters group heading", b">Masters<" in r.data)
logout()

# ================= 6) every /ws/<key> home 200 for admin ==============
with app.app_context():
    from app.workspaces import visible_workspaces
    adm = User.query.get("HRADMIN")
    ws_keys = [w["key"] for w in visible_workspaces(adm)]
check("admin sees all 11 workspaces", len(ws_keys) == 11,
      f"{len(ws_keys)}: {','.join(ws_keys)}")
login("HRADMIN", "pw-HRADMIN")
for key in ws_keys:
    r = client.get(f"/ws/{key}")
    check(f"/ws/{key} 200 for admin", r.status_code == 200,
          f"{r.status_code}")
logout()

# ================= 7) asset versions bumped =================
login("HRADMIN", "pw-HRADMIN")
r = client.get("/")
check("pages link the bumped stylesheet v=10", b"style.css?v=10" in r.data)
check("pages link app.js v=5", b"app.js?v=5" in r.data)
check("pages link desk.js v=3", b"desk.js?v=3" in r.data)
logout()

print()
print(f"V3.7: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
