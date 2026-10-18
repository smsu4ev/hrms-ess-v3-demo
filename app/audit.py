"""Advanced audit trail helpers for HRMS+ESS v2.

`record(...)` writes a single audit entry with entity / entity_id / field-level
old and new values, plus acting user and IP.

`audit_model_changes(obj, before, fields, ...)` diffs a SQLAlchemy model's
editable fields and writes one audit entry per changed field (old -> new).
Committed by the caller.
"""
from datetime import date, datetime

from flask import request, has_request_context
from flask_login import current_user

from app import db
from app.models import AuditLog


def _fmt(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _actor_and_ip(actor_id=None):
    if actor_id is None:
        try:
            if current_user and current_user.is_authenticated:
                actor_id = current_user.get_id()
        except Exception:
            actor_id = actor_id
    ip = None
    if has_request_context():
        ip = request.remote_addr
    return actor_id, ip


def record(action, detail="", entity=None, entity_id=None, field_name=None,
           old_value=None, new_value=None, actor_id=None):
    """Write one audit entry. Committed by the caller."""
    actor, ip = _actor_and_ip(actor_id)
    db.session.add(AuditLog(
        actor_id=actor,
        action=action,
        detail=detail,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        field_name=field_name,
        old_value=_fmt(old_value),
        new_value=_fmt(new_value),
        ip=ip,
    ))


def csv_cell(value):
    """Guard against CSV formula injection.

    Spreadsheet apps treat cells starting with =, +, - or @ as formulas, so
    user-controlled text (names, IBANs, notes) could execute on open. Prefix
    such cells with a single quote; the data itself is unchanged.
    """
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@"):
        return "'" + s
    return s


def audit_model_changes(obj, before, fields, entity, action="updated",
                        actor_id=None, detail=""):
    """Compare `before` (dict of field -> old value) with current model values.

    Writes one audit entry per changed field. Fields not in `fields` or
    unchanged are skipped.
    """
    for field in fields:
        new_val = getattr(obj, field, None)
        old_val = before.get(field)
        if _fmt(old_val) != _fmt(new_val):
            record(
                action=f"{entity}_{action}",
                detail=detail or f"{field} changed",
                entity=entity,
                entity_id=getattr(obj, "id", None),
                field_name=field,
                old_value=old_val,
                new_value=new_val,
                actor_id=actor_id,
            )


def snapshot(obj, fields):
    """Capture a dict of current field values for later diffing."""
    return {f: getattr(obj, f, None) for f in fields}
