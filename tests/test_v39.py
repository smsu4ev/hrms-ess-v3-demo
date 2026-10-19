"""HRMS+ESS v3.9 tests: theme system (nixtio/glass/indigo/night) + picker + persistence.

Covers V39-THEMES-SPEC.md section 3 acceptance:
1. Server renders data-theme="nixtio|glass|indigo|night" from the
   ess_ui_theme cookie (each of the 4 keys); default nixtio when the cookie
   is unset or holds an invalid key.
2. Picker markup on the dashboard: #themePickerBtn, #themePopover, exactly
   4 .theme-card elements each with data-theme-key, and the active card
   ticked for the cookie's theme.
3. Logged-in POST /api/ui-theme with a valid key -> 200 + {"ok": true} and
   User.ui_theme persisted (verified with a fresh DB read); invalid key ->
   400; unauthenticated POST -> blocked (302 to login).
4. User-saved ui_theme beats the ess_ui_theme cookie on the next login
   (resolution order 1 > 2).
5. Legacy ess_theme=dark cookie (with no ess_ui_theme) renders night.
6. style.css: token blocks for all 4 themes, the @supports not
   (backdrop-filter) glass fallback, the prefers-reduced-transparency media
   query, backdrop-filter declarations scoped to the allowed components
   only (never table rows or inputs), and no leftover html[data-theme="dark"]
   block.
7. Dashboard HTML in glass mode still carries ring/bars/tasks widgets and the
   data is identical to the nixtio-mode render (tasks % and a KPI number).
8. Login page renders the themed <html> attribute from the cookie and
   includes the picker button.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v39.db && ESS_SECRET_KEY=testsecret12345 \\
       ESS_DATABASE_URL=sqlite:////tmp/test_v39.db \\
       ~/workspace/ess-app/venv/bin/python tests/test_v39.py
"""
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
from app import create_app, db  # noqa: E402
from app.models import (  # noqa: E402
    User, LeaveBalance, ApprovalInstance,
)

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


def meta_csrf_token(resp):
    m = re.search(rb'name="csrf-token" content="([^"]+)"', resp.data)
    return m.group(1).decode() if m else ""


def logout():
    t = csrf_token(client.get("/change-password"))
    return client.post("/logout", data={"csrf_token": t},
                       follow_redirects=False)


def login(uid, pw):
    # /login redirects authenticated users, so an already-logged-in session
    # would silently keep the old user; always log out first.
    try:
        logout()
    except Exception:
        pass
    r = client.get("/login")
    t = csrf_token(r)
    return client.post("/login", data={"username": uid, "password": pw,
                                       "csrf_token": t},
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


def set_theme_cookie(key):
    client.set_cookie("ess_ui_theme", key)


def clear_theme_cookies():
    client.delete_cookie("ess_ui_theme")
    client.delete_cookie("ess_theme")


THEME_KEYS = ("nixtio", "glass", "indigo", "night")

with app.app_context():
    mk("ADM39", "Ari Admin", "admin", "Human Resources")
    mk("MGR39", "Mia Manager", "manager", "Production")
    mk("EMP39", "Sal Employee", "employee", "Production",
       reporting_manager="MGR39")
    if not LeaveBalance.query.filter_by(user_id="EMP39",
                                        leave_type="annual").first():
        db.session.add(LeaveBalance(user_id="EMP39", leave_type="annual",
                                    entitled=30.0, used=6.0))
    if not ApprovalInstance.query.filter_by(
            process_key="leave", ref_id="leave-t39-1").first():
        db.session.add(ApprovalInstance(
            process_key="leave", ref_id="leave-t39-1",
            subject_user_id="EMP39", requester_id="EMP39",
            steps_json=json.dumps([{"step_no": 1,
                                    "approver_type": "specific_user",
                                    "approver_user_id": "MGR39"}]),
            current_step=1, status="pending"))
    db.session.commit()

# ============ 1) data-theme rendered from the ess_ui_theme cookie ==========
for key in THEME_KEYS:
    clear_theme_cookies()
    logout()
    set_theme_cookie(key)
    r = client.get("/login")
    check(f"cookie ess_ui_theme={key} renders data-theme=\"{key}\"",
          f'data-theme="{key}"'.encode() in r.data)

# ============ 2) default nixtio when unset / invalid =======================
clear_theme_cookies()
logout()
r = client.get("/login")
check("unset cookie renders data-theme=\"nixtio\"",
      b'data-theme="nixtio"' in r.data)
set_theme_cookie("bogus")
r = client.get("/login")
check("invalid cookie renders data-theme=\"nixtio\"",
      b'data-theme="nixtio"' in r.data)
clear_theme_cookies()

# ============ 3) picker markup on the dashboard ============================
login("MGR39", "pw-MGR39")
set_theme_cookie("glass")
r = client.get("/")
html = r.data
check("dashboard renders data-theme=\"glass\" from cookie",
      b'data-theme="glass"' in html)
check("picker button present (#themePickerBtn)",
      b'id="themePickerBtn"' in html)
check("picker popover present (#themePopover)",
      b'id="themePopover"' in html)
check("picker popover has dialog role", b'role="dialog"' in html)
check("picker has 4 .theme-card options",
      html.count(b'data-theme-key="') == 4,
      f"count={html.count(b'data-theme-key=')}")
for key in THEME_KEYS:
    check(f"picker card for {key} present",
          f'data-theme-key="{key}"'.encode() in html)
check("active theme card ticked for cookie theme",
      b'class="theme-card active" data-theme-key="glass"' in html)
check("body carries data-authed=\"1\" when logged in",
      b'data-authed="1"' in html)
check("meta csrf-token present for the picker POST",
      meta_csrf_token(r) != "")

# ============ 4) POST /api/ui-theme: persist + validation ==================
tok = meta_csrf_token(r)


def post_theme(theme_value, token=tok):
    return client.post(
        "/api/ui-theme",
        data=json.dumps({"theme": theme_value}),
        content_type="application/json",
        headers={"X-CSRFToken": token})


pr = post_theme("indigo")
check("POST /api/ui-theme valid key -> 200", pr.status_code == 200,
      f"status={pr.status_code}")
pj = pr.get_json(silent=True) or {}
check("POST /api/ui-theme returns {ok:true, theme}",
      pj.get("ok") is True and pj.get("theme") == "indigo",
      f"body={pj}")
with app.app_context():
    db.session.expire_all()
    saved = User.query.get("MGR39").ui_theme
check("User.ui_theme persisted after POST", saved == "indigo",
      f"ui_theme={saved!r}")

pr = post_theme("rainbow")
check("POST /api/ui-theme invalid key -> 400", pr.status_code == 400,
      f"status={pr.status_code}")
with app.app_context():
    db.session.expire_all()
    saved2 = User.query.get("MGR39").ui_theme
check("User.ui_theme unchanged after invalid key", saved2 == "indigo",
      f"ui_theme={saved2!r}")

logout()
anon_tok = csrf_token(client.get("/login"))
pr = client.post(
    "/api/ui-theme",
    data=json.dumps({"theme": "glass"}),
    content_type="application/json",
    headers={"X-CSRFToken": anon_tok})
check("unauthenticated POST /api/ui-theme blocked",
      pr.status_code in (401, 302), f"status={pr.status_code}")

# ============ 5) saved ui_theme beats the cookie on next login =============
with app.app_context():
    u = User.query.get("MGR39")
    u.ui_theme = "indigo"
    db.session.commit()
set_theme_cookie("glass")
login("MGR39", "pw-MGR39")
r = client.get("/")
check("saved ui_theme wins over cookie after login",
      b'data-theme="indigo"' in r.data
      and b'data-theme="glass"' not in r.data)
with app.app_context():
    User.query.get("MGR39").ui_theme = None
    db.session.commit()
logout()

# ============ 6) legacy ess_theme=dark -> night =============================
clear_theme_cookies()
client.set_cookie("ess_theme", "dark")
r = client.get("/login")
check("legacy ess_theme=dark renders data-theme=\"night\"",
      b'data-theme="night"' in r.data
      and b'data-theme="dark"' not in r.data)
clear_theme_cookies()

# ============ 7) style.css: tokens + glass fallbacks =======================
_css = (ROOT / "app/static/style.css").read_text()
for key in THEME_KEYS:
    check(f"style.css has :root[data-theme=\"{key}\"] token block",
          f':root[data-theme="{key}"] {{' in _css)
check("default selector covers nixtio", ":root, :root[data-theme=\"nixtio\"]"
      in _css)
check("old html[data-theme=\"dark\"] block removed",
      'html[data-theme="dark"]' not in _css)
check("@supports not (backdrop-filter) glass fallback present",
      "@supports not (backdrop-filter: blur(1px))" in _css)
check("fallback neutralises glass surfaces",
      "rgba(255,255,255,.94)" in _css)
check("prefers-reduced-transparency media query present",
      "@media (prefers-reduced-transparency: reduce)" in _css)
check("backdrop-filter scoped to allowed components (.theme-popover)",
      ':root[data-theme="glass"] .theme-popover' in _css
      and "backdrop-filter: blur(var(--glass-blur))" in _css)
# No backdrop-filter may leak onto table rows or form fields (perf contract).
_leak = False
for m in re.finditer(r"([^{}]+)\{[^{}]*backdrop-filter[^{}]*}", _css):
    sel = m.group(1)
    if re.search(r"(^|[\s,>+~])(tr|td|th|input|select|textarea|option)\b",
                 sel):
        _leak = sel.strip()
        break
check("backdrop-filter never on table rows or inputs", not _leak,
      f"leak={_leak!r}" if _leak else "")
check("picker styles present (.theme-popover / .theme-card)",
      ".theme-popover" in _css and ".theme-card" in _css)
check("theme transitions .35s kept",
      re.search(r"transition:[^;]*\.35s", _css) is not None)
check("prefers-reduced-motion kill-switch kept",
      "prefers-reduced-motion" in _css)

# ============ 8) glass dashboard keeps ring/bars/tasks with same data ======
login("EMP39", "pw-EMP39")


def dash_as(key):
    set_theme_cookie(key)
    h = client.get("/").data
    return h


glass_html = dash_as("glass")
nixtio_html = dash_as("nixtio")
check("glass dashboard renders ring widget (tracker card)",
      b"tracker-card" in glass_html)
check("glass dashboard renders KPI bars", b"kbar-track" in glass_html)
check("glass dashboard renders tasks card", b"tasks-dark" in glass_html)


def _tasks_pct(h):
    m = re.search(rb'tasks-pct">([^<]+)<', h)
    return m.group(1) if m else None


# MGR39 has one open approval -> tasks card shows a real 0% figure;
# compare that data point across themes.
login("MGR39", "pw-MGR39")
mgr_glass = dash_as("glass")
mgr_nixtio = dash_as("nixtio")
check("tasks % identical in glass and nixtio",
      _tasks_pct(mgr_glass) == _tasks_pct(mgr_nixtio)
      and _tasks_pct(mgr_glass) == b"0%",
      f"glass={_tasks_pct(mgr_glass)} nixtio={_tasks_pct(mgr_nixtio)}")
check("manager tasks row present in glass render",
      b"Approvals awaiting me" in mgr_glass)
logout()
login("EMP39", "pw-EMP39")
glass_html = dash_as("glass")
nixtio_html = dash_as("nixtio")
check("annual-leave-used bar (20%) present in glass render",
      re.search(rb"Annual leave used</div>\s*<div class=\"kbar-track\">"
                rb".*?<span class=\"kbar-pct\">20%</span>",
                glass_html, re.S) is not None)
check("KPI annual-leave-balance (24 days) present in glass render",
      b"Annual leave balance (days)" in glass_html
      and b">24<" in glass_html)
check("KPI annual-leave-balance (24 days) present in nixtio render",
      b"Annual leave balance (days)" in nixtio_html
      and b">24<" in nixtio_html)
logout()

# ============ 9) login page: themed html + picker ===========================
set_theme_cookie("night")
r = client.get("/login")
check("login page renders data-theme=\"night\" from cookie",
      b'data-theme="night"' in r.data)
check("login page includes the picker button",
      b'id="themePickerBtn"' in r.data)
clear_theme_cookies()

# ============ 10) theme in My Profile + HR Settings =======================
# (a) employee's My Profile page carries the Theme / Appearance section.
login("EMP39", "pw-EMP39")
r = client.get("/profile")
check("profile page 200", r.status_code == 200)
phtml = r.data
check("profile has Theme / Appearance section",
      b"Theme / Appearance" in phtml and b'id="profile-theme"' in phtml)
check("profile section wired for the picker JS",
      b'data-theme-cards' in phtml)
check("profile renders 8 theme cards (4 popover + 4 section)",
      phtml.count(b'data-theme-key="') == 8,
      f"count={phtml.count(b'data-theme-key=')}")
for label in (b"Nixtio Blue", b"Liquid Glass", b"Slate &amp; Indigo",
              b"Timeless Night"):
    check(f"profile theme card labelled {label.decode()}",
          label in phtml)

# (b) persisting via /api/ui-theme marks that theme active on the profile
# page (both the popover card and the section card).
tok = meta_csrf_token(r)
pr = post_theme("night", token=tok)
check("POST /api/ui-theme night -> 200 (profile flow)",
      pr.status_code == 200)
phtml = client.get("/profile").data
check("profile marks night active in both placements",
      phtml.count(b'class="theme-card active" data-theme-key="night"') == 2)
with app.app_context():
    db.session.expire_all()
    check("User.ui_theme persisted as night",
          User.query.get("EMP39").ui_theme == "night")
logout()

# (c) HR Settings Appearance section: admin sees it, employees get 403.
login("ADM39", "pw-ADM39")
r = client.get("/hrms/settings")
check("admin settings page 200", r.status_code == 200)
shtml = r.data
check("settings has Appearance section",
      b"Appearance" in shtml and b'id="settings-appearance"' in shtml)
check("settings notes the theme is per-user", b"per-user" in shtml)
check("settings renders 8 theme cards (4 popover + 4 section)",
      shtml.count(b'data-theme-key="') == 8,
      f"count={shtml.count(b'data-theme-key=')}")
logout()
login("EMP39", "pw-EMP39")
r = client.get("/hrms/settings")
check("employee blocked from HR Settings (403)", r.status_code == 403)
logout()

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
