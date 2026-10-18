"""Admin panel: user management, password resets, roles, audit log, imports."""
import csv
import io
import secrets
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app import db
from app.models import User, AuditLog, AttendanceRecord, LeaveBalance
from app.auth import log_action, password_policy_errors
from app.audit import csv_cell
import config as app_config

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


@admin_bp.route("/users")
@login_required
@admin_required
def users():
    q = (request.args.get("q") or "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(User.id.ilike(like), User.name.ilike(like),
                   User.department.ilike(like), User.designation.ilike(like)))
    users = query.order_by(User.name).limit(200).all()
    return render_template("admin_users.html", users=users, q=q)


@admin_bp.route("/users/<uid>", methods=["GET", "POST"])
@login_required
@admin_required
def user_detail(uid):
    user = User.query.get_or_404(uid)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "reset_initial":
            # Reset to initial DOB-based password is not possible (DOB not stored).
            # Admin sets a temporary password instead; user must change on next login.
            temp = "Temp" + secrets.token_hex(3) + "1A"
            user.set_password(temp)
            user.must_change_password = True
            user.failed_attempts = 0
            user.locked_until = None
            log_action(current_user.id, "password_reset", f"{uid} (temporary)")
            db.session.commit()
            flash(f"Temporary password for {uid}: {temp} - share it securely; user must change on login.",
                  "success")
        elif action == "set_password":
            new_pw = request.form.get("new_password") or ""
            errors = password_policy_errors(new_pw)
            if errors:
                flash(" ".join(errors), "error")
            else:
                user.set_password(new_pw)
                user.must_change_password = request.form.get("force_change") == "on"
                user.failed_attempts = 0
                user.locked_until = None
                log_action(current_user.id, "password_set_admin", uid)
                db.session.commit()
                flash("Password updated.", "success")
        elif action == "toggle_active":
            if user.id == current_user.id:
                flash("You cannot deactivate your own account.", "error")
            else:
                user.is_active = not user.is_active
                log_action(current_user.id, "user_toggled",
                           f"{uid} active={user.is_active}")
                db.session.commit()
                flash("Account status updated.", "success")
        elif action == "toggle_salary_view":
            user.can_view_salary = not user.can_view_salary
            log_action(current_user.id, "salary_view_toggled",
                       f"{uid} can_view_salary={user.can_view_salary}")
            db.session.commit()
            flash(f"Salary visibility for {uid}: "
                  f"{'granted' if user.can_view_salary else 'revoked'}.",
                  "success")
        elif action == "set_role":
            role = request.form.get("role", "")
            if role in ("employee", "manager", "admin"):
                if user.id == current_user.id and role != "admin":
                    flash("You cannot remove your own admin role.", "error")
                else:
                    user.role = role
                    log_action(current_user.id, "role_changed", f"{uid} -> {role}")
                    db.session.commit()
                    flash("Role updated.", "success")
        return redirect(url_for("admin.user_detail", uid=uid))
    balances = LeaveBalance.query.filter_by(user_id=uid).all()
    logs = AuditLog.query.filter_by(actor_id=uid)\
        .order_by(AuditLog.created_at.desc()).limit(20).all()
    return render_template("admin_user_detail.html", user=user,
                           balances=balances, logs=logs)


def _audit_query():
    """Apply optional entity/user/date filters from query params."""
    q = AuditLog.query
    entity = (request.args.get("entity") or "").strip()
    user = (request.args.get("user") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    if entity:
        q = q.filter_by(entity=entity)
    if user:
        q = q.filter(AuditLog.actor_id == user)
    if date_from:
        try:
            q = q.filter(AuditLog.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(AuditLog.created_at < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass
    return q, {"entity": entity, "user": user, "date_from": date_from, "date_to": date_to}


@admin_bp.route("/audit")
@login_required
@admin_required
def audit():
    q, filters = _audit_query()
    logs = q.order_by(AuditLog.created_at.desc()).limit(200).all()
    entities = sorted({l.entity for l in
                       AuditLog.query.with_entities(AuditLog.entity).distinct()
                       if l.entity})
    return render_template("admin_audit.html", logs=logs, filters=filters,
                           entities=entities)


@admin_bp.route("/audit/export")
@login_required
@admin_required
def audit_export():
    """CSV export of the filtered audit trail."""
    from flask import Response
    q, filters = _audit_query()
    logs = q.order_by(AuditLog.created_at.desc()).limit(2000).all()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["time", "actor", "action", "entity", "entity_id",
                     "field", "old_value", "new_value", "detail", "ip"])
    for l in logs:
        writer.writerow([l.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                         csv_cell(l.actor_id), csv_cell(l.action),
                         csv_cell(l.entity or ""), csv_cell(l.entity_id or ""),
                         csv_cell(l.field_name or ""),
                         csv_cell(l.old_value or ""), csv_cell(l.new_value or ""),
                         csv_cell((l.detail or "").replace("\n", " ")),
                         csv_cell(l.ip or "")])
    log_action(current_user.id, "audit_exported", str(filters))
    db.session.commit()
    filename = f"audit-log-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@admin_bp.route("/attendance/import", methods=["GET", "POST"])
@login_required
@admin_required
def attendance_import():
    result = None
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".csv"):
            flash("Please upload a CSV file.", "error")
        else:
            try:
                text = io.StringIO(f.read().decode("utf-8-sig"))
                reader = csv.DictReader(text)
                needed = {"employee_id", "date", "check_in", "check_out", "status"}
                if not needed.issubset({h.strip() for h in (reader.fieldnames or [])}):
                    flash("CSV must have columns: employee_id, date, check_in, check_out, status", "error")
                else:
                    count = 0
                    for row in reader:
                        uid = (row.get("employee_id") or "").strip()
                        if not uid or not User.query.get(uid):
                            continue
                        try:
                            d = datetime.strptime(row["date"].strip(), "%Y-%m-%d").date()
                        except (ValueError, AttributeError):
                            continue
                        rec = AttendanceRecord.query.filter_by(user_id=uid, date=d).first()
                        if not rec:
                            rec = AttendanceRecord(user_id=uid, date=d)
                            db.session.add(rec)
                        rec.check_in = (row.get("check_in") or "").strip()[:5] or None
                        rec.check_out = (row.get("check_out") or "").strip()[:5] or None
                        rec.status = (row.get("status") or "present").strip()[:20] or "present"
                        count += 1
                    log_action(current_user.id, "attendance_import", f"{count} rows")
                    db.session.commit()
                    result = f"Imported {count} attendance records."
            except Exception:
                db.session.rollback()
                flash("Import failed: invalid file content.", "error")
    return render_template("admin_attendance_import.html", result=result)



@admin_bp.route("/outbox")
@login_required
@admin_required
def outbox():
    """WhatsApp notification queue (integration-ready; sending comes later)."""
    from app.models import NotificationOutbox, User as U
    status = (request.args.get("status") or "").strip()
    q = NotificationOutbox.query
    if status in ("queued", "sent", "failed"):
        q = q.filter_by(status=status)
    items = q.order_by(NotificationOutbox.created_at.desc()).limit(200).all()
    users = {u.id: u for u in U.query.all()}
    import config as _cfg
    return render_template("admin_outbox.html", items=items, users=users,
                           status=status, whatsapp_enabled=_cfg.WHATSAPP_ENABLED)


@admin_bp.route("/outbox/<int:oid>/mark", methods=["POST"])
@login_required
@admin_required
def outbox_mark(oid):
    from app.models import NotificationOutbox
    item = NotificationOutbox.query.get_or_404(oid)
    item.status = request.form.get("status") or "queued"
    log_action(current_user.id, "outbox_updated",
               f"#{oid} -> {item.status}")
    db.session.commit()
    return redirect(url_for("admin.outbox"))
