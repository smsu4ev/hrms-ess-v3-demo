"""HRMS+ESS v3.6.1 tests: step-up gate for the Geolocation tracking toggle.

Spec: turning 'geolocation' OFF (from on) in /hrms/settings requires
(a) the admin's own password (verified against the password hash) and
(b) a mandatory free-text reason. Wrong password or empty reason -> the
WHOLE save is refused atomically: the toggle stays ON and no other changes
on the same submit are persisted, with a clear error flash. Turning it back
ON needs no step-up; other toggles are unaffected. The audit entry for the
change carries the reason text. A crafted POST without the step-up fields
must also fail (server is the real gate).

Uses an isolated temp SQLite DB and test fixtures only (no real data).
Run: rm -f /tmp/test_v361.db && ESS_SECRET_KEY=testsecret12345 \\
ESS_DATABASE_URL=<redacted> \\
       ~/workspace/ess-app/venv/bin/python test_v361.py
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import User, AuditLog  # noqa: E402
from app.settingsutil import get_setting  # noqa: E402

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


def form_post(post_path, token_path, data):
    t = get_token(token_path)
    data = dict(data)
    data["csrf_token"] = t
    return client.post(post_path, data=data, follow_redirects=True)


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


def geo_audits():
    with app.app_context():
        return AuditLog.query.filter_by(entity="setting",
                                        entity_id="geolocation",
                                        action="update").all()


with app.app_context():
    mk("V361-ADM", "V361 Admin", "admin", "Human Resources",
       is_hr_manager=True)
    db.session.commit()
    check("geo starts ON (default)", get_setting("geolocation") == "1")

login("V361-ADM", "pw-V361-ADM")

# ---- 1) geo OFF without any step-up fields -> refused, stays ON ----
n_aud_before = len(geo_audits())
r = form_post("/hrms/settings", "/hrms/settings",
              {"celebrations_enabled": "0", "leave_notifications": "1",
               "mobile_checkin": "1"})
check("geo off w/o stepup: save refused (error flash)",
      b"was NOT turned off" in r.data)
with app.app_context():
    check("geo off w/o stepup: toggle stays ON", get_setting("geolocation") == "1")
    check("geo off w/o stepup: atomic — other changes also NOT saved",
          get_setting("celebrations_enabled") == "1")
    check("geo off w/o stepup: no audit entry written",
          len(geo_audits()) == n_aud_before)

# ---- 2) geo OFF with wrong password -> refused ----
r = form_post("/hrms/settings", "/hrms/settings",
              {"celebrations_enabled": "1", "leave_notifications": "1",
               "mobile_checkin": "1", "geo_stepup_password": "wrong-pw",
               "geo_stepup_reason": "attacker reason"})
check("geo off wrong password: refused (error flash)",
      b"was NOT turned off" in r.data and b"password incorrect" in r.data)
with app.app_context():
    check("geo off wrong password: toggle stays ON", get_setting("geolocation") == "1")
    check("geo off wrong password: no audit entry written",
          len(geo_audits()) == n_aud_before)

# ---- 3) geo OFF with correct password but empty reason -> refused ----
r = form_post("/hrms/settings", "/hrms/settings",
              {"celebrations_enabled": "1", "leave_notifications": "1",
               "mobile_checkin": "1", "geo_stepup_password": "pw-V361-ADM",
               "geo_stepup_reason": "   "})
check("geo off empty reason: refused (error flash)",
      b"was NOT turned off" in r.data and b"reason is required" in r.data)
with app.app_context():
    check("geo off empty reason: toggle stays ON", get_setting("geolocation") == "1")
    check("geo off empty reason: no audit entry written",
          len(geo_audits()) == n_aud_before)

# ---- 4) geo OFF with correct password + reason -> succeeds, audit has reason ----
reason = "factory yard has no reliable GPS coverage"
r = form_post("/hrms/settings", "/hrms/settings",
              {"celebrations_enabled": "1", "leave_notifications": "1",
               "mobile_checkin": "1",
               "geo_stepup_password": "pw-V361-ADM",
               "geo_stepup_reason": reason})
check("geo off correct stepup: save succeeds (success flash)",
      b"HR Settings saved" in r.data)
with app.app_context():
    check("geo off correct stepup: toggle persisted OFF", get_setting("geolocation") == "0")
    auds = geo_audits()
    check("geo off correct stepup: audit entry written", len(auds) == n_aud_before + 1)
    check("geo off correct stepup: audit contains the reason",
          auds and reason in (auds[-1].detail or ""), auds[-1].detail if auds else "none")

# ---- 5) geo already OFF -> saving OFF again needs no step-up ----
r = form_post("/hrms/settings", "/hrms/settings",
              {"celebrations_enabled": "1", "leave_notifications": "1",
               "mobile_checkin": "1"})
check("geo already off: save without stepup accepted",
      b"was NOT turned off" not in r.data)
with app.app_context():
    check("geo already off: stays OFF", get_setting("geolocation") == "0")

# ---- 6) geo back ON needs no step-up ----
r = form_post("/hrms/settings", "/hrms/settings",
              {"celebrations_enabled": "1", "leave_notifications": "1",
               "mobile_checkin": "1", "geolocation": "1"})
check("geo on: succeeds without stepup", b"HR Settings saved" in r.data)
with app.app_context():
    check("geo on: toggle persisted ON", get_setting("geolocation") == "1")

# ---- 7) other toggles turn off without step-up ----
r = form_post("/hrms/settings", "/hrms/settings",
              {"leave_notifications": "1", "mobile_checkin": "1", "geolocation": "1"})
check("celebrations off: succeeds without stepup", b"HR Settings saved" in r.data)
with app.app_context():
    check("celebrations off: toggle persisted OFF",
          get_setting("celebrations_enabled") == "0")

# ---- 8) non-admin cannot reach the gate at all ----
logout()
login("V361-ADM", "pw-V361-ADM")
with app.app_context():
    u = mk("V361-E1", "V361 Employee", "employee", "Production")
    db.session.commit()
logout()
login("V361-E1", "pw-V361-E1")
r = client.post("/hrms/settings", data={"csrf_token": get_token("/change-password")},
                follow_redirects=False)
check("settings POST 403 for employee", r.status_code == 403)
logout()

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
