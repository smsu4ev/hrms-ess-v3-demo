"""HR Settings key/value store helpers (v3.6).

Thin accessor over the SettingKV table. Writes are staged on the session and
committed by the caller (same convention as app/audit.record).
"""
from app import db
from app.models import SettingKV


DEFAULTS = {
    "celebrations_enabled": "1",
    "leave_notifications": "1",
    "mobile_checkin": "1",
    "geolocation": "1",
}


def get_setting(key, default=""):
    """Return the stored value for `key`, or `default` if no row exists.

    Keys listed in DEFAULTS fall back to their default ("1" = enabled).
    """
    row = SettingKV.query.get(key)
    if row is None:
        return DEFAULTS.get(key, default)
    return row.value


def set_setting(key, value, user_id=None):
    """Upsert one setting. Staged on the session; committed by the caller."""
    row = SettingKV.query.get(key)
    if row is None:
        row = SettingKV(key=key, value=value, updated_by=user_id)
        db.session.add(row)
    else:
        row.value = value
        row.updated_by = user_id
    db.session.flush()
    return row
