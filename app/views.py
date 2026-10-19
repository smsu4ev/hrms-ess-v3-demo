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


def approver_required(fn):
    """v3.4: anyone who can plausibly hold an approval step — managers and
    admins plus users flagged as HR staff or HR manager."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not (current_user.is_manager or current_user.is_hr_staff
                or current_user.is_hr_manager):
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
    # v3.3: engagement — today's celebrations, latest notices, mandatory reads.
    from app.engagement import (ensure_todays_celebrations, visible_notices,
                                pending_mandatory_notices)
    ensure_todays_celebrations()
    notices = visible_notices(current_user, limit=3)
    mandatory = pending_mandatory_notices(current_user)
    # v3.3.1: unified inbox summary for the My Tasks card.
    from app.ess_extras import inbox_summary, dept_today
    _inbox = inbox_summary(current_user)
    inbox_total, inbox_items = _inbox["total"], _inbox["items"]
    dept_card = dept_today(current_user)
    from app.models import CelebrationPost
    from datetime import date as _date
    today = _date.today()
    todays_posts = CelebrationPost.query.filter_by(
        post_date=today).order_by(CelebrationPost.id).all()
    users = {u.id: u for u in User.query.filter(
        User.id.in_([p.user_id for p in todays_posts])).all()} \
        if todays_posts else {}
    return render_template("dashboard.html", balances=balances,
                           pending_leaves=pending_leaves,
                           open_grievances=open_grievances,
                           present_days=present,
                           approvals_pending=approvals_pending,
                           expiring_docs=expiring_docs,
                           notices=notices, mandatory=mandatory,
                           inbox_total=inbox_total, inbox_items=inbox_items,
                           dept_card=dept_card,
                           todays_posts=todays_posts, post_users=users)


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
                cfg_entitled = app_config.LEAVE_TYPES[ltype]["entitled"]
                # v3.4.1: the employee's own balance row is authoritative
                # when present (pro-rated entitlements), else the config.
                entitled = (bal.entitled if bal and bal.entitled is not None
                            else cfg_entitled)
                used = bal.used if bal else 0
                # v3.4.1: pending requests of the same type count against
                # the balance, so two pending requests cannot together
                # exceed the entitlement (double-spend guard).
                pending_days = sum(
                    r.days for r in LeaveRequest.query.filter_by(
                        user_id=current_user.id, leave_type=ltype,
                        status="pending").all())
                if entitled > 0 and used + pending_days + days > entitled:
                    flash(f"Insufficient {app_config.LEAVE_TYPES[ltype]['label']} balance "
                          f"(used {used}, {pending_days} day(s) pending approval).",
                          "error")
                else:
                    req = LeaveRequest(user_id=current_user.id, leave_type=ltype,
                                       start_date=s, end_date=e, days=days, reason=reason)
                    db.session.add(req)
                    log_action(current_user.id, "leave_applied",
                               f"{ltype} {s} to {e} ({days}d)")
                    db.session.commit()
                    # v3.4: start the approval-matrix chain for this request.
                    from app.approvals import start_instance
                    start_instance("leave", req.id, current_user.id,
                                   current_user.id)
                    flash("Leave request submitted.", "success")
                    return redirect(url_for("views.leave"))
    balances = LeaveBalance.query.filter_by(user_id=current_user.id).all()
    requests = LeaveRequest.query.filter_by(user_id=current_user.id)\
        .order_by(LeaveRequest.created_at.desc()).limit(20).all()
    # v3.4: per-request approval-chain progress for the employee's own list.
    from app.approvals import get_instance, step_badge
    badges = {}
    for r in requests:
        if r.status == "pending":
            inst = get_instance("leave", r.id)
            if inst:
                badges[r.id] = step_badge(inst)
    return render_template("leave.html", balances=balances, requests=requests,
                           leave_types=app_config.LEAVE_TYPES, badges=badges)


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
    # v3.6: month can come from a date/month picker (ym=YYYY-MM) or the
    # legacy y/m params.
    ym = (request.args.get("ym") or "").strip()
    if ym:
        try:
            y, m = ym.split("-")
            view_date = date(int(y), int(m), 1)
        except (ValueError, TypeError):
            view_date = today.replace(day=1)
    else:
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
    # v3.3: recent geo/selfie punches for this month.
    from app.models import PunchRecord, WorkLocation
    # v3.6: punch-type filter (check-in / check-out).
    punch = (request.args.get("punch") or "").strip()
    if punch not in ("in", "out"):
        punch = ""
    pq = PunchRecord.query.filter(
        PunchRecord.user_id == current_user.id,
        PunchRecord.local_date >= month_start,
        PunchRecord.local_date <= month_end)
    if punch:
        pq = pq.filter(PunchRecord.punch_type == punch)
    punches = pq.order_by(PunchRecord.punched_at.desc()).limit(60).all()
    locations = {l.id: l for l in WorkLocation.query.all()}
    return render_template("attendance.html", weeks=weeks, by_date=by_date,
                           view_date=view_date, prev_m=prev_m, next_m=next_m,
                           today=today, punches=punches, locations=locations,
                           punch=punch)


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
    from app.models import Certification, PPEIssue, Deployment, Project, AssetCustody
    est = None
    if current_user.joining_date and (current_user.basic_salary or 0) > 0:
        est = gratuity(current_user.joining_date, date.today(),
                       current_user.basic_salary, "employer")
    my_certs = Certification.query.filter_by(
        user_id=current_user.id).order_by(Certification.expiry_date).all()
    my_ppe = PPEIssue.query.filter_by(
        user_id=current_user.id).order_by(PPEIssue.issued_date.desc()).all()
    my_assets = AssetCustody.query.filter_by(
        user_id=current_user.id).order_by(
        AssetCustody.returned_date.isnot(None),
        AssetCustody.issued_date.desc()).all()
    today = date.today()
    my_deps = (Deployment.query.filter(
        Deployment.user_id == current_user.id,
        (Deployment.to_date.is_(None)) | (Deployment.to_date >= today))
        .order_by(Deployment.from_date.desc()).all())
    dep_projects = {p.id: p for p in Project.query.all()}
    return render_template("profile.html", gratuity_est=est, my_certs=my_certs,
                           my_ppe=my_ppe, my_deps=my_deps, my_assets=my_assets,
                           dep_projects=dep_projects)


@views_bp.route("/profile/celebrations", methods=["POST"])
@login_required
def profile_celebrations():
    """v3.4.1: employee toggles birthday/anniversary feed sharing."""
    share = request.form.get("share_celebrations") == "on"
    current_user.share_celebrations = share
    log_action(current_user.id, "celebration_pref_updated",
               f"share_celebrations={share}")
    db.session.commit()
    flash("Celebration feed preference saved.", "success")
    return redirect(url_for("views.profile"))


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


# ---------------- Approvals (v3.4: unified matrix-driven inbox) ----------------
@views_bp.route("/approvals")
@login_required
@approver_required
def approvals():
    from app.approvals import (get_or_start_instance, can_act, step_badge,
                               inbox_for)
    from app.models import RegularisationRequest
    # v3.6: process-type + status filters (GET params). Default status
    # "pending" preserves the original inbox behaviour exactly; the
    # "approved"/"rejected" options list decided leave requests
    # (other chains only exist in a pending state).
    process = (request.args.get("process") or "all").strip()
    if process not in ("all", "leave", "regularisation", "employee_creation",
                       "expense", "shift_swap"):
        process = "all"
    status = (request.args.get("status") or "pending").strip()
    if status not in ("pending", "approved", "rejected"):
        status = "pending"
    # --- leave section (legacy default 1-step behaviour preserved)
    q = LeaveRequest.query.filter_by(status=status)
    if not current_user.is_admin:
        # Non-admins see requests from their own department only, and only
        # the ones sitting on their own approval step.
        dept_users = [u.id for u in User.query.filter_by(
            department=current_user.department, is_active=True).all()]
        q = q.filter(LeaveRequest.user_id.in_(dept_users))
    leave_items = []
    for r in q.order_by(LeaveRequest.created_at.desc()).all():
        inst = get_or_start_instance("leave", r.id, r.user_id, r.user_id)
        item_can_act = can_act(inst, current_user)
        if current_user.is_admin or item_can_act:
            leave_items.append({"req": r, "instance": inst,
                                "badge": step_badge(inst),
                                "can_act": item_can_act})
    users = {u.id: u for u in User.query.filter(
        User.id.in_([i["req"].user_id for i in leave_items])).all()} \
        if leave_items else {}
    # --- unified inbox: other chains awaiting this user (pending only)
    reg_items, creation_items, expense_items, swap_items = [], [], [], []
    if status == "pending":
        for inst in inbox_for(current_user):
            if inst.process_key == "regularisation":
                rr = RegularisationRequest.query.get(int(inst.ref_id))
                if rr is not None and rr.status == "pending":
                    reg_items.append({"req": rr, "instance": inst,
                                      "badge": step_badge(inst)})
            elif inst.process_key == "employee_creation":
                u = User.query.get(inst.ref_id)
                if u is not None and u.employment_status == "pending_approval":
                    creation_items.append({"user": u, "instance": inst,
                                           "badge": step_badge(inst)})
            elif inst.process_key == "expense":
                from app.models import ExpenseClaim
                claim = ExpenseClaim.query.get(int(inst.ref_id))
                if claim is not None and claim.status == "pending":
                    expense_items.append({"claim": claim, "instance": inst,
                                          "badge": step_badge(inst)})
            elif inst.process_key == "shift_swap":
                from app.models import ShiftSwapRequest
                from app.helpdesk import rest_gap_flags
                swap = ShiftSwapRequest.query.get(int(inst.ref_id))
                if swap is not None and swap.status == "pending_approval":
                    swap_items.append({"swap": swap, "instance": inst,
                                       "badge": step_badge(inst),
                                       "flags": rest_gap_flags(swap)})
    # --- shift swaps: names + pattern labels for the table
    swap_user_ids, swap_pattern_ids = set(), set()
    for it in swap_items:
        s = it["swap"]
        swap_user_ids.update([s.requester_id, s.counterpart_user_id])
        if s.my_pattern_id:
            swap_pattern_ids.add(s.my_pattern_id)
        if s.their_pattern_id:
            swap_pattern_ids.add(s.their_pattern_id)
    from app.models import ShiftPattern as _ShiftPattern
    swap_users = {u.id: u for u in User.query.filter(
        User.id.in_(swap_user_ids)).all()} if swap_user_ids else {}
    swap_patterns = {p.id: p for p in _ShiftPattern.query.filter(
        _ShiftPattern.id.in_(swap_pattern_ids)).all()} \
        if swap_pattern_ids else {}
    return render_template("approvals.html",
                           items=[i["req"] for i in leave_items],
                           meta={i["req"].id: i for i in leave_items},
                           users=users, leave_types=app_config.LEAVE_TYPES,
                           process=process, status=status,
                           reg_items=reg_items,
                           creation_items=creation_items,
                           expense_items=expense_items,
                           swap_items=swap_items,
                           swap_users=swap_users,
                           swap_patterns=swap_patterns)


def _leave_approve_side_effects(req, comment):
    """Final-approval side-effects for leave (balance, status, outbox)."""
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
    req.approver_id = current_user.id
    req.approver_comment = comment
    req.decided_at = datetime.utcnow()
    log_action(current_user.id, f"leave_{req.status}",
               f"request #{req.id} ({req.user_id})")
    from app.models import NotificationOutbox
    db.session.add(NotificationOutbox(
        user_id=req.user_id, channel="whatsapp",
        event=f"leave_{req.status}",
        message=f"Your leave request ({app_config.LEAVE_TYPES.get(req.leave_type, {}).get('label', req.leave_type)}, "
                f"{req.start_date} to {req.end_date}) has been {req.status}.",
        status="queued"))


def _leave_reject_side_effects(req, comment):
    req.status = "rejected"
    req.approver_id = current_user.id
    req.approver_comment = comment
    req.decided_at = datetime.utcnow()
    log_action(current_user.id, f"leave_{req.status}",
               f"request #{req.id} ({req.user_id})")
    from app.models import NotificationOutbox
    db.session.add(NotificationOutbox(
        user_id=req.user_id, channel="whatsapp",
        event=f"leave_{req.status}",
        message=f"Your leave request ({app_config.LEAVE_TYPES.get(req.leave_type, {}).get('label', req.leave_type)}, "
                f"{req.start_date} to {req.end_date}) has been {req.status}.",
        status="queued"))


@views_bp.route("/approvals/<int:req_id>", methods=["POST"])
@login_required
@approver_required
def decide_leave(req_id):
    from app.approvals import get_or_start_instance, act, self_blocked
    req = LeaveRequest.query.get_or_404(req_id)
    if req.status != "pending":
        flash("Request already decided.", "error")
        return redirect(url_for("views.approvals"))
    instance = get_or_start_instance("leave", req.id, req.user_id,
                                     req.user_id)
    action = request.form.get("action")
    comment = (request.form.get("comment") or "").strip()[:500]
    if action not in ("approve", "reject"):
        abort(400)
    # v3.4.1: re-validate the balance at the FINAL step before the chain
    # advances — a request must never overdraw, even if earlier steps were
    # approved while the balance was still intact.
    if action == "approve" and instance.current_step == instance.total_steps \
            and not instance.exhausted:
        bal = LeaveBalance.query.filter_by(
            user_id=req.user_id, leave_type=req.leave_type).first()
        cfg_entitled = app_config.LEAVE_TYPES.get(req.leave_type, {}).get(
            "entitled", 0)
        entitled = (bal.entitled if bal and bal.entitled is not None
                    else cfg_entitled)
        used = bal.used if bal else 0
        if entitled > 0 and used + req.days > entitled:
            flash("Insufficient remaining balance — request stays pending.",
                  "error")
            return redirect(url_for("views.approvals"))
    try:
        outcome = act(instance, current_user, action == "approve", comment)
    except PermissionError as e:
        if self_blocked(instance, current_user) and \
                "own request" in str(e):
            flash("You cannot approve your own request.", "error")
            return redirect(url_for("views.approvals"))
        abort(403)
    if outcome == "rejected":
        _leave_reject_side_effects(req, comment)
        flash("Leave rejected.", "success")
    elif outcome == "final_approved":
        _leave_approve_side_effects(req, comment)
        flash("Leave approved.", "success")
    else:
        flash("Approved at this step; forwarded to the next approver.",
              "success")
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
            # v3.4: start the approval-matrix chain for this request.
            from app.approvals import start_instance
            start_instance("regularisation", rr.id, current_user.id,
                           current_user.id)
            flash("Regularisation request submitted.", "success")
            return redirect(url_for("views.regularisation"))
    items = RegularisationRequest.query.filter_by(user_id=current_user.id)\
        .order_by(RegularisationRequest.date.desc()).all()
    return render_template("regularisation.html", items=items)


@views_bp.route("/regularisation/approve", methods=["GET", "POST"])
@login_required
@approver_required
def regularisation_approvals():
    """Matrix-driven approval list for regularisation requests (v3.4)."""
    from app.approvals import (get_or_start_instance, act, can_act,
                               step_badge, self_blocked)
    q = RegularisationRequest.query.filter_by(status="pending")
    if request.method == "POST":
        rid = int(request.form.get("rid", 0))
        rr = RegularisationRequest.query.get_or_404(rid)
        if rr.status != "pending":
            flash("Request already decided.", "error")
            return redirect(url_for("views.regularisation_approvals"))
        instance = get_or_start_instance("regularisation", rr.id,
                                         rr.user_id, rr.user_id)
        action = request.form.get("action")
        comment = (request.form.get("comment") or "").strip()[:500]
        if action not in ("approve", "reject"):
            abort(400)
        try:
            outcome = act(instance, current_user, action == "approve",
                          comment)
        except PermissionError as e:
            if self_blocked(instance, current_user) and \
                    "own request" in str(e):
                flash("You cannot approve your own request.", "error")
                return redirect(url_for("views.regularisation_approvals"))
            abort(403)
        if outcome == "rejected":
            rr.status = "rejected"
            flash("Regularisation rejected.", "success")
        elif outcome == "final_approved":
            rr.status = "approved"
            # Apply the correction to the attendance record on final approval.
            rec = AttendanceRecord.query.filter_by(user_id=rr.user_id, date=rr.date).first()
            if not rec:
                rec = AttendanceRecord(user_id=rr.user_id, date=rr.date, status="present")
                db.session.add(rec)
            if rr.corrected_in:
                rec.check_in = rr.corrected_in
            if rr.corrected_out:
                rec.check_out = rr.corrected_out
            flash("Regularisation approved and attendance updated.", "success")
        else:
            flash("Approved at this step; forwarded to the next approver.",
                  "success")
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
    items = []
    for rr in q.order_by(RegularisationRequest.created_at.desc()).all():
        inst = get_or_start_instance("regularisation", rr.id, rr.user_id,
                                     rr.user_id)
        item_can_act = can_act(inst, current_user)
        if not current_user.is_admin:
            # Non-admins see their own department's requests only, and only
            # the ones sitting on their own approval step.
            target = User.query.get(rr.user_id)
            if not target or target.department != current_user.department:
                continue
            if not item_can_act:
                continue
        items.append({"rr": rr, "badge": step_badge(inst),
                      "can_act": item_can_act})
    users = {u.id: u for u in User.query.filter(
        User.id.in_([i["rr"].user_id for i in items])).all()} if items else {}
    return render_template("regularisation_approvals.html", items=items,
                           users=users)


# ---------------- My Roster (employee, v3.2) ----------------
@views_bp.route("/my-roster")
@login_required
def my_roster():
    """Employee's own shift assignments for the next 14 days."""
    from app.models import RosterAssignment
    today = date.today()
    rows = []
    for i in range(14):
        d = today + timedelta(days=i)
        a = RosterAssignment.query.filter_by(user_id=current_user.id,
                                             date=d).first()
        rows.append({"date": d, "assignment": a, "today": d == today})
    return render_template("my_roster.html", rows=rows)
