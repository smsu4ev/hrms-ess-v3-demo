"""HRMS+ESS v3.7 tests: App-UI shell (top bar + drawer), home, animations.

Covers V37-APP-UI-SPEC.md acceptance:
1. Shell markup: no .rail / .ws-sidebar classes; top bar has hamburger,
   search opener, bell and avatar menu; desktop text links (Home, All
   Services; My Team for managers/admins only).
2. Drawer: renders role-filtered groups from the workspaces registry;
   admin sees the HR Settings link, a plain employee does not.
3. Home tiles per role: employee sees Check In/Out + Apply Leave and no
   Payroll; admin sees Payroll; manager sees an Approvals tile carrying
   the pending-approval badge (> 0).
4. count-up [data-count] present on the manager/admin hero; animation CSS
   present (@keyframes tile-rise, .pjax-enter) plus a
   prefers-reduced-motion block; .bottomnav base display:none rule intact
   before the <=860px media query (V3.6.2 regression guard).
5. /apps renders grouped tiles per area.
6. Every /ws/<key> home returns 200 for admin.

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
    """The desktop text-link block of the top bar only."""
    m = re.search(rb'<nav class="topbar-links".*?</nav>', html, re.S)
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
      and b'class="hamburger"' in r.data)
check("top bar has search opener", b'id="awesomeOpen"' in r.data)
check("top bar has notifications bell", b'id="notifOpen"' in r.data)
check("top bar has avatar menu",
      b'id="avatarBtn"' in r.data and b'id="avatarMenu"' in r.data)
check("avatar menu has My Profile", b">My Profile<" in r.data)
check("avatar menu has Theme", b">Theme<" in r.data)
check("avatar menu has HR Settings (admin)", b">HR Settings<" in r.data)
tb = topbar_links(r.data)
check("topbar text link: Home", b">Home<" in tb)
check("topbar text link: All Services", b">All Services<" in tb)
check("topbar text link: My Team (admin)", b">My Team<" in tb)

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
check("employee topbar shows Home + All Services",
      b">Home<" in tb and b">All Services<" in tb)
check("employee topbar hides My Team", b">My Team<" not in tb)
check("employee drawer renders", b'id="drawer"' in r.data)
check("employee drawer hides HR Settings", b"HR Settings" not in r.data)
logout()

login("MGR001", "pw-MGR001")
r = client.get("/")
tb = topbar_links(r.data)
check("manager topbar shows My Team", b">My Team<" in tb)
logout()

# ================= 3) home tiles per role =================
login("EMP001", "pw-EMP001")
r = client.get("/")
check("employee home 200", r.status_code == 200)
check("employee greeting shows first name",
      re.search(rb"Good (morning|afternoon|evening), T37", r.data)
      is not None)
check("employee sees Check In/Out tile", b"Check In/Out" in r.data)
check("employee sees Apply Leave tile", b"Apply Leave" in r.data)
check("tiles use tinted icon chips", b'class="t-chip ' in r.data)
check("employee has no Payroll tile", b">Payroll<" not in r.data)
logout()

login("HRADMIN", "pw-HRADMIN")
r = client.get("/")
check("admin home 200", r.status_code == 200)
check("admin sees Payroll tile", b">Payroll<" in r.data)
check("admin sees HR Settings tile", b">HR Settings<" in r.data)
check("admin hero has count-up stats", b"data-count" in r.data)
check("admin hero shows pending approvals",
      b"Pending approvals" in r.data)
logout()

login("MGR001", "pw-MGR001")
r = client.get("/")
check("manager home 200", r.status_code == 200)
check("manager sees Approvals tile", b">Approvals<" in r.data)
m = re.search(rb'class="t-lbl">Approvals</span>\s*'
              rb'<div class="t-sub">.*?</div>\s*'
              rb'<span class="t-badge">(\d+)</span>', r.data, re.S)
check("manager Approvals tile badge > 0",
      m is not None and int(m.group(1)) > 0,
      f"badge={m.group(1).decode() if m else 'none'}")
check("manager hero has count-up stats", b"data-count" in r.data)
check("manager hero shows team counts",
      b"Present today" in r.data and b"On leave" in r.data)
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
check("pages link the bumped stylesheet v=9", b"style.css?v=9" in r.data)
check("pages link app.js v=4", b"app.js?v=4" in r.data)
check("pages link desk.js v=2", b"desk.js?v=2" in r.data)
logout()

print()
print(f"V3.7: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
