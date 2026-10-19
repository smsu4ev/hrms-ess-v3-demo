"""HR Settings page (v3.6 Frappe-model clone): /hrms/settings.

Tabbed admin-only settings. Only four toggles are wired to behaviour
(celebrations_enabled, leave_notifications, mobile_checkin, geolocation);
every other row is an honest display/link row pointing at the real page.
All saves are audited via app.audit.record.
"""
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, \
    flash, abort
from flask_login import login_required, current_user

from app import db
from app.settingsutil import get_setting, set_setting, DEFAULTS
from app.audit import record as audit_record


settings_bp = Blueprint("settings", __name__, url_prefix="/hrms")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


# Toggles actually wired into behaviour (see engagement.py wiring notes).
WIRED_KEYS = ["celebrations_enabled", "leave_notifications",
              "mobile_checkin", "geolocation"]


@settings_bp.route("/settings", methods=["GET"], endpoint="page")
@login_required
@admin_required
def settings_page():
    """Render the tabbed HR Settings form (admin only)."""
    values = {k: get_setting(k, DEFAULTS[k]) for k in WIRED_KEYS}
    return render_template("hr_settings.html", values=values)


@settings_bp.route("/settings", methods=["POST"], endpoint="save")
@login_required
@admin_required
def settings_save():
    """Save the wired toggles. Unchecked boxes post nothing -> "0".

    Step-up gate (v3.6.1): turning 'geolocation' OFF (from on) requires the
    admin's own password plus a mandatory free-text reason. A step-up
    failure REFUSES THE WHOLE SAVE (atomic: nothing is persisted, the toggle
    stays on) — this is simpler and safer than partial saves. Turning
    geolocation back ON needs no step-up; the password+reason fields are a
    pure convenience in the template — the server re-checks everything, so a
    crafted POST without them fails.
    """
    geo_old = get_setting("geolocation", DEFAULTS["geolocation"])
    geo_new = "1" if request.form.get("geolocation") == "1" else "0"
    geo_reason = ""
    if geo_old == "1" and geo_new == "0":
        pw = request.form.get("geo_stepup_password") or ""
        geo_reason = (request.form.get("geo_stepup_reason") or "").strip()
        if not current_user.check_password(pw):
            flash("Geolocation tracking was NOT turned off (password incorrect). "
                  "The toggle stays ON and no settings were saved.", "error")
            return redirect(url_for("settings.page"))
        if not geo_reason:
            flash("Geolocation tracking was NOT turned off (a reason is required). "
                  "The toggle stays ON and no settings were saved.", "error")
            return redirect(url_for("settings.page"))

    changed = []
    for key in WIRED_KEYS:
        new = "1" if request.form.get(key) == "1" else "0"
        old = get_setting(key, DEFAULTS[key])
        if new != old:
            set_setting(key, new, user_id=current_user.get_id())
            detail = f"HR setting '{key}' changed {old} -> {new}"
            if key == "geolocation" and geo_reason:
                detail += f" (reason: {geo_reason})"
            audit_record(action="update", entity="setting", entity_id=key,
                         field_name=key, old_value=old, new_value=new,
                         detail=detail, actor_id=current_user.get_id())
            changed.append(key)
    db.session.commit()
    if changed:
        flash(f"HR Settings saved ({len(changed)} change(s): "
              f"{', '.join(changed)}).", "success")
    else:
        flash("No changes to save.", "info")
    return redirect(url_for("settings.page"))
