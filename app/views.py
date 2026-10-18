"""Employee-facing views: dashboard, leave, attendance, payslip, profile,
grievances, evaluations, approvals."""
import json
from datetime import date, datetime, timedelta
import calendar as calmod

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app import db
from app.models import (User, LeaveBalance, LeaveRequest, AttendanceRecord,
                        Grievance, Evaluation, AuditLog, RegularisationRequest)
from app.auth import log_action
from app.audit import record as audit_record
import config as app_config

views_bp = Blueprint("views", __name__)


def manager_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_manager:
            abort(403)
        return fn(*a, **kw)
    return wrapper


# ---------------- Dashboard ----------------
@views_bp.route("/")
@login_required
def dashboard():
    balances = LeaveBalance.query.filter_by(user_id=current_user.id).all()
    pending_leaves = LeaveRequest.query.filter_by(
        user_id=current_user.id, status="pending").count()
    open_grievances = Grievance.query.filter(
        Grievance.user_id == current_user.id,
        Grievance.status.in_(["submitted", "under_review", "in_progress"])).count()
    month_start = date.today().replace(day=1)
    att = AttendanceRecord.query.filter(
        AttendanceRecord.user_id == current_user.id,
        AttendanceRecord.date >= month_start).all()
    present = sum(1 for r in att if r.status in ("present", "late"))
    approvals_pending = 0
    if current_user.is_manager:
        approvals_pending = LeaveRequest.query.filter_by(status="pending").count()
    expiring_docs = []
    if current_user.is_admin:
        from app.hrms import get_upcoming_expiries
        expiring_docs = get_upcoming_expiries(30)
    return render_template("dashboard.html", balances=balances,
                           pending_leaves=pending_leaves,
                           open_grievances=open_grievances,
                           present_days=present,
                           approvals_pending=approvals_pending,
                           expiring_docs=expiring_docs)


# ---------------- Leave ----------------
@views_bp.route("/leave", methods=["GET", "POST"])
@login_required
def leave():
    if request.method == "POST":
        ltype = request.form.get("leave_type", "")
        start = request.form.get("start_date", "")
        end = request.form.get("end_date", "")
        reason = (request.form.get("reason") or "").strip()[:1000]
        if ltype not in app_config.LEAVE_TYPES:
            flash("Invalid leave type.", "error")
        else:
            try:
                s = datetime.strptime(start, "%Y-%m-%d").date()
                e = datetime.strptime(end, "%Y-%m-%d").date()
            except ValueError:
                s = e = None
            if not s or not e or e < s:
                flash("Invalid date range.", "error")
            elif s < date.today():
                flash("Leave cannot start in the past.", "error")
            else:
                days = (e - s).days + 1
                bal = LeaveBalance.query.filter_by(
                    user_id=current_user.id, leave_type=ltype).first()
                entitled = app_config.LEAVE_TYPES[ltype]["entitled"]
                used = bal.used if bal else 0
                if entitled > 0 and used + days > entitled:
                    flash(f"Insufficient {app_config.LEAVE_TYPES[ltype]['label']} balance.", "error")
                else:
                    req = LeaveRequest(user_id=current_user.id, leave_type=ltype,
                                       start_date=s, end_date=e, days=days, reason=reason)
                    db.session.add(req)
                    log_action(current_user.id, "leave_applied",
                               f"{ltype} {s} to {e} ({days}d)")
                    db.session.commit()
                    flash("Leave request submitted.", "success")
                    return redirect(url_for("views.leave"))
    balances = LeaveBalance.query.filter_by(user_id=current_user.id).all()
    requests = LeaveRequest.query.filter_by(user_id=current_user.id)\
        .order_by(LeaveRequest.created_at.desc()).limit(20).all()
    return render_template("leave.html", balances=balances, requests=requests,
                           leave_types=app_config.LEAVE_TYPES)


@views_bp.route("/leave/<int:req_id>/cancel", methods=["POST"])
@login_required
def cancel_leave(req_id):
    req = LeaveRequest.query.get_or_404(req_id)
    if req.user_id != current_user.id or req.status != "pending":
        abort(403)
    req.status = "cancelled"
    log_action(current_user.id, "leave_cancelled", f"request #{req.id}")
    db.session.commit()
    flash("Leave request cancelled.", "success")
    return redirect(url_for("views.leave"))


# ---------------- Attendance ----------------
@views_bp.route("/attendance")
@login_required
def attendance():
    today = date.today()
    try:
        year = int(request.args.get("y", today.year))
        month = int(request.args.get("m", today.month))
        view_date = date(year, month, 1)
    except ValueError:
        view_date = today.replace(day=1)
    month_start = view_date
    month_end = date(view_date.year, view_date.month,
                     calmod.monthrange(view_date.year, view_date.month)[1])
    records = AttendanceRecord.query.filter(
        AttendanceRecord.user_id == current_user.id,
        AttendanceRecord.date >= month_start,
        AttendanceRecord.date <= month_end).all()
    by_date = {r.date: r for r in records}
    weeks = calmod.monthcalendar(view_date.year, view_date.month)
    prev_m = month_start - timedelta(days=1)
    next_m = (month_end + timedelta(days=1)).replace(day=1)
    return render_template("attendance.html", weeks=weeks, by_date=by_date,
                           view_date=view_date, prev_m=prev_m, next_m=next_m,
                           today=today)


# ---------------- Payslip ----------------
def build_payslip(user, year, month):
    basic = user.basic_salary or 0.0
    gross = user.monthly_salary or 0.0
    allowances = max(gross - basic, 0.0)
    deductions = 0.0
    net = gross - deductions
    return {
        "year": year, "month": month,
        "month_name": calmod.month_name[month],
        "earnings": [("Basic Salary", basic), ("Allowances", allowances)],
        "deductions": [("Total Deductions", deductions)],
        "gross": gross, "total_deductions": deductions, "net": net,
    }


@views_bp.route("/payslip")
@login_required
def payslips():
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        months.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return render_template("payslips.html", months=months)


@views_bp.route("/payslip/<int:year>/<int:month>")
@login_required
def payslip(year, month):
    if not (1 <= month <= 12) or year < 2020 or year > date.today().year:
        abort(404)
    slip = build_payslip(current_user, year, month)
    run = None
    run_slip = None
    from app.models import PayrollRun, PayrollLine
    period = f"{year:04d}-{month:02d}"
    run = PayrollRun.query.filter_by(period=period, status="approved").first()
    if run:
        line = PayrollLine.query.filter_by(run_id=run.id,
                                            user_id=current_user.id).first()
        if line:
            import json as _json
            run_slip = {
                "period": period, "user": current_user,
                "payable_days": line.payable_days,
                "lop_days": line.lop_days,
                "basic_earned": line.basic_earned,
                "earnings": _json.loads(line.earnings_json),
                "deductions": _json.loads(line.deductions_json),
                "ot": _json.loads(line.ot_json),
                "gross": line.gross,
                "total_deductions": line.total_deductions,
                "net": line.net,
            }
    return render_template("payslip.html", slip=slip, run=run, run_slip=run_slip)


# ---------------- Profile ----------------
@views_bp.route("/profile")
@login_required
def profile():
    from app.uae import gratuity
    est = None
    if current_user.joining_date and (current_user.basic_salary or 0) > 0:
        est = gratuity(current_user.joining_date, date.today(),
                       current_user.basic_salary, "employer")
    return render_template("profile.html", gratuity_est=est)


# ---------------- My End-of-Service Estimate (employee) ----------------
@views_bp.route("/gratuity")
@login_required
def my_gratuity():
    """Employee-facing end-of-service estimate (employer-termination basis)."""
    from app.uae import gratuity
    est = None
    if current_user.joining_date and (current_user.basic_salary or 0) > 0:
        est = gratuity(current_user.joining_date, date.today(),
                       current_user.basic_salary, "employer")
    return render_template("my_gratuity.html", est=est)


# ---------------- Grievances ----------------
@views_bp.route("/grievances", methods=["GET", "POST"])
@login_required
def grievances():
    if request.method == "POST":
        category = request.form.get("category", "")
        subject = (request.form.get("subject") or "").strip()[:200]
        description = (request.form.get("description") or "").strip()[:5000]
        anonymous = request.form.get("anonymous") == "on"
        if category not in app_config.GRIEVANCE_CATEGORIES or not subject or not description:
            flash("Please complete category, subject and description.", "error")
        else:
            g = Grievance(user_id=current_user.id, category=category,
                          subject=subject, description=description,
                          is_anonymous=anonymous)
            db.session.add(g)
            log_action(current_user.id, "grievance_raised", f"{category}: {subject[:60]}")
            db.session.commit()
            flash("Grievance submitted. HR will review it confidentially.", "success")
            return redirect(url_for("views.grievances"))
    items = Grievance.query.filter_by(user_id=current_user.id)\
        .order_by(Grievance.created_at.desc()).all()
    return render_template("grievances.html", items=items,
                           categories=app_config.GRIEVANCE_CATEGORIES)


@views_bp.route("/grievances/<int:gid>")
@login_required
def grievance_detail(gid):
    g = Grievance.query.get_or_404(gid)
    if g.user_id != current_user.id and not current_user.is_manager:
        abort(403)
    return render_template("grievance_detail.html", g=g)


# ---------------- Evaluations (employee view) ----------------
@views_bp.route("/evaluations")
@login_required
def evaluations():
    items = Evaluation.query.filter_by(user_id=current_user.id)\
        .order_by(Evaluation.created_at.desc()).all()
    for e in items:
        e.scores = json.loads(e.scores_json)
    return render_template("evaluations.html", items=items)


@views_bp.route("/evaluations/<int:eid>/acknowledge", methods=["POST"])
@login_required
def acknowledge_evaluation(eid):
    e = Evaluation.query.get_or_404(eid)
    if e.user_id != current_user.id:
        abort(403)
    e.acknowledged = True
    log_action(current_user.id, "evaluation_acknowledged", f"evaluation #{e.id}")
    db.session.commit()
    flash("Evaluation acknowledged.", "success")
    return redirect(url_for("views.evaluations"))


# ---------------- Approvals (manager/admin) ----------------
@views_bp.route("/approvals")
@login_required
@manager_required
def approvals():
    q = LeaveRequest.query.filter_by(status="pending")
    if not current_user.is_admin:
        # Managers see requests from their own department only.
        dept_users = [u.id for u in User.query.filter_by(
            department=current_user.department, is_active=True).all()]
        q = q.filter(LeaveRequest.user_id.in_(dept_users))
    items = q.order_by(LeaveRequest.created_at.desc()).all()
    users = {u.id: u for u in User.query.filter(
        User.id.in_([r.user_id for r in items])).all()} if items else {}
    return render_template("approvals.html", items=items, users=users,
                           leave_types=app_config.LEAVE_TYPES)


@views_bp.route("/approvals/<int:req_id>", methods=["POST"])
@login_required
@manager_required
def decide_leave(req_id):
    req = LeaveRequest.query.get_or_404(req_id)
    if req.status != "pending":
        flash("Request already decided.", "error")
        return redirect(url_for("views.approvals"))
    target = User.query.get(req.user_id)
    if not current_user.is_admin and (not target or target.department != current_user.department):
        abort(403)
    action = request.form.get("action")
    comment = (request.form.get("comment") or "").strip()[:500]
    if action == "approve":
        req.status = "approved"
        bal = LeaveBalance.query.filter_by(
            user_id=req.user_id, leave_type=req.leave_type).first()
        if bal:
            bal.used += req.days
        else:
            entitled = app_config.LEAVE_TYPES.get(req.leave_type, {}).get("entitled", 0)
            db.session.add(LeaveBalance(user_id=req.user_id,
                                        leave_type=req.leave_type,
                                        entitled=entitled, used=req.days))
        flash("Leave approved.", "success")
    elif action == "reject":
        req.status = "rejected"
        flash("Leave rejected.", "success")
    else:
        abort(400)
    req.approver_id = current_user.id
    req.approver_comment = comment
    req.decided_at = datetime.utcnow()
    log_action(current_user.id, f"leave_{req.status}", f"request #{req.id} ({req.user_id})")
    from app.models import NotificationOutbox
    db.session.add(NotificationOutbox(
        user_id=req.user_id, channel="whatsapp",
        event=f"leave_{req.status}",
        message=f"Your leave request ({app_config.LEAVE_TYPES.get(req.leave_type, {}).get('label', req.leave_type)}, "
                f"{req.start_date} to {req.end_date}) has been {req.status}.",
        status="queued"))
    db.session.commit()
    return redirect(url_for("views.approvals"))


# ---------------- Evaluations (manager/admin create) ----------------
@views_bp.route("/evaluations/manage")
@login_required
@manager_required
def evaluations_manage():
    q = User.query.filter_by(is_active=True)
    if not current_user.is_admin:
        q = q.filter_by(department=current_user.department)
    employees = q.order_by(User.name).all()
    recent = Evaluation.query.order_by(Evaluation.created_at.desc()).limit(20).all()
    users = {u.id: u for u in User.query.all()}
    return render_template("evaluations_manage.html", employees=employees,
                           recent=recent, users=users)


@views_bp.route("/evaluations/new/<uid>", methods=["GET", "POST"])
@login_required
@manager_required
def evaluation_new(uid):
    target = User.query.get_or_404(uid)
    if not target.is_active:
        abort(404)
    if not current_user.is_admin and target.department != current_user.department:
        abort(403)
    if request.method == "POST":
        period = (request.form.get("period") or "").strip()[:40] or f"{date.today().year} H2"
        remarks = (request.form.get("remarks") or "").strip()[:2000]
        scores = []
        total = 0
        for i, criterion in enumerate(app_config.EVAL_CRITERIA):
            try:
                rating = int(request.form.get(f"rating_{i}", "0"))
            except ValueError:
                rating = 0
            comment_choice = request.form.get(f"comment_{i}", "")
            comment_text = (request.form.get(f"comment_text_{i}") or "").strip()[:500]
            if rating < 1 or rating > 5:
                flash(f"Rating required for: {criterion} (1-5).", "error")
                return render_template("evaluation_form.html", target=target,
                                       criteria=app_config.EVAL_CRITERIA,
                                       options=app_config.EVAL_COMMENT_OPTIONS)
            if comment_choice not in app_config.EVAL_COMMENT_OPTIONS:
                comment_choice = "Meets Expectations"
            scores.append({"criterion": criterion, "rating": rating,
                           "comment": comment_choice, "note": comment_text})
            total += rating
        overall = round(total / len(scores), 2)
        ev = Evaluation(user_id=target.id, period=period,
                        evaluator_id=current_user.id,
                        scores_json=json.dumps(scores),
                        overall=overall, remarks=remarks)
        db.session.add(ev)
        log_action(current_user.id, "evaluation_created",
                   f"{target.id} period {period} overall {overall}")
        db.session.commit()
        flash("Evaluation saved.", "success")
        return redirect(url_for("views.evaluations_manage"))
    return render_template("evaluation_form.html", target=target,
                           criteria=app_config.EVAL_CRITERIA,
                           options=app_config.EVAL_COMMENT_OPTIONS)


# ---------------- Grievance inbox (manager/admin) ----------------
@views_bp.route("/grievances/inbox", methods=["GET", "POST"])
@login_required
@manager_required
def grievance_inbox():
    if request.method == "POST":
        gid = int(request.form.get("gid", 0))
        g = Grievance.query.get_or_404(gid)
        new_status = request.form.get("status", "")
        resolution = (request.form.get("resolution") or "").strip()[:2000]
        if new_status in ("submitted", "under_review", "in_progress", "resolved", "closed"):
            g.status = new_status
            g.resolution = resolution
            log_action(current_user.id, "grievance_updated",
                       f"#{g.id} -> {new_status}")
            db.session.commit()
            flash("Grievance updated.", "success")
        return redirect(url_for("views.grievance_inbox"))
    items = Grievance.query.order_by(Grievance.created_at.desc()).all()
    users = {u.id: u for u in User.query.all()}
    return render_template("grievance_inbox.html", items=items, users=users)


# ---------------- Attendance Regularisation (ESS + MSS) ----------------
@views_bp.route("/regularisation", methods=["GET", "POST"])
@login_required
def regularisation():
    """Employee: request a correction to an attendance in/out entry."""
    if request.method == "POST":
        d_raw = request.form.get("date", "")
        cin = (request.form.get("corrected_in") or "").strip()
        cout = (request.form.get("corrected_out") or "").strip()
        reason = (request.form.get("reason") or "").strip()[:500]
        try:
            d = datetime.strptime(d_raw, "%Y-%m-%d").date()
        except ValueError:
            d = None
        if not d:
            flash("Invalid date.", "error")
        elif d > date.today():
            flash("Regularisation cannot be for a future date.", "error")
        elif not reason:
            flash("A reason is required.", "error")
        elif RegularisationRequest.query.filter_by(
                user_id=current_user.id, date=d, status="pending").first():
            flash("A pending request already exists for this date.", "error")
        else:
            rr = RegularisationRequest(user_id=current_user.id, date=d,
                                       corrected_in=cin or None,
                                       corrected_out=cout or None, reason=reason)
            db.session.add(rr)
            log_action(current_user.id, "regularisation_applied",
                       f"{d} in={cin or '-'} out={cout or '-'}")
            db.session.commit()
            flash("Regularisation request submitted.", "success")
            return redirect(url_for("views.regularisation"))
    items = RegularisationRequest.query.filter_by(user_id=current_user.id)\
        .order_by(RegularisationRequest.date.desc()).all()
    return render_template("regularisation.html", items=items)


@views_bp.route("/regularisation/approve", methods=["GET", "POST"])
@login_required
@manager_required
def regularisation_approvals():
    """Manager/HR approval list for regularisation requests."""
    q = RegularisationRequest.query.filter_by(status="pending")
    if not current_user.is_admin:
        dept_users = [u.id for u in User.query.filter_by(
            department=current_user.department, is_active=True).all()]
        q = q.filter(RegularisationRequest.user_id.in_(dept_users))
    if request.method == "POST":
        rid = int(request.form.get("rid", 0))
        rr = RegularisationRequest.query.get_or_404(rid)
        if rr.status != "pending":
            flash("Request already decided.", "error")
            return redirect(url_for("views.regularisation_approvals"))
        target = User.query.get(rr.user_id)
        if not current_user.is_admin and (not target or target.department != current_user.department):
            abort(403)
        action = request.form.get("action")
        comment = (request.form.get("comment") or "").strip()[:500]
        if action == "approve":
            rr.status = "approved"
            # Apply the correction to the attendance record on approval.
            rec = AttendanceRecord.query.filter_by(user_id=rr.user_id, date=rr.date).first()
            if not rec:
                rec = AttendanceRecord(user_id=rr.user_id, date=rr.date, status="present")
                db.session.add(rec)
            if rr.corrected_in:
                rec.check_in = rr.corrected_in
            if rr.corrected_out:
                rec.check_out = rr.corrected_out
            flash("Regularisation approved and attendance updated.", "success")
        elif action == "reject":
            rr.status = "rejected"
            flash("Regularisation rejected.", "success")
        else:
            abort(400)
        rr.approver_id = current_user.id
        rr.approver_comment = comment
        rr.decided_at = datetime.utcnow()
        log_action(current_user.id, f"regularisation_{rr.status}",
                   f"request #{rr.id} ({rr.user_id})")
        audit_record(f"regularisation_{rr.status}",
                     f"request #{rr.id} ({rr.user_id}) date {rr.date}",
                     entity="regularisation", entity_id=rr.id,
                     field_name="status", old_value="pending", new_value=rr.status)
        db.session.commit()
        return redirect(url_for("views.regularisation_approvals"))
    items = q.order_by(RegularisationRequest.created_at.desc()).all()
    users = {u.id: u for u in User.query.filter(
        User.id.in_([r.user_id for r in items])).all()} if items else {}
    return render_template("regularisation_approvals.html", items=items, users=users)
