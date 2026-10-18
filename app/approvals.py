"""Configurable approval matrix (v3.4).

Multi-step approval chains per process (employee creation, leave,
attendance regularisation), customisable by the HR Admin. Chains can
differ per case, e.g. Employee -> Supervisor -> Manager -> HR Staff ->
HR Manager.

The engine resolves each step to concrete users at runtime:
  reporting_manager  -> the subject employee's reporting manager
  skip_level_manager -> that manager's own reporting manager
  hr_staff           -> all users flagged is_hr_staff (any one may act;
                        first action wins)
  hr_manager         -> all users flagged is_hr_manager (fall back to all
                        admins when nobody is flagged)
  admin              -> all admins
  specific_user      -> the chosen user

Steps with no resolvable users auto-skip (recorded as auto_skip actions
plus an audit entry) so a chain never deadlocks. Editing a matrix never
mutates in-flight instances: each instance snapshots its step list at
start time.

Permissions are strict: only the current step's resolvers may act, with
two admin backstops - admins may act on hr_manager/admin steps (they are
resolvers there by definition), and admins alone may decide instances
whose steps are all exhausted.
"""
import json
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app import db
from app.models import (User, ApprovalMatrix, ApprovalStep, ApprovalInstance,
                        ApprovalAction, APPROVAL_PROCESSES, APPROVER_TYPES)
from app.auth import log_action
from app.audit import record

approvals_bp = Blueprint("approvals", __name__, url_prefix="/hrms")

PROCESS_LABELS = dict(APPROVAL_PROCESSES)
APPROVER_TYPE_LABELS = dict(APPROVER_TYPES)

DEFAULT_MATRICES = {
    "employee_creation": ["hr_manager"],
    "leave": ["reporting_manager"],
    "regularisation": ["reporting_manager"],
}


# ---------------- matrix maintenance ----------------

def ensure_default_matrices():
    """Idempotent: seed the default chain for any process that has no
    matrix yet. Keeps legacy single-manager behaviour on upgrade."""
    for process_key, types in DEFAULT_MATRICES.items():
        if ApprovalMatrix.query.filter_by(process_key=process_key).first():
            continue
        matrix = ApprovalMatrix(
            process_key=process_key,
            name=PROCESS_LABELS.get(process_key, process_key),
            is_active=True)
        db.session.add(matrix)
        db.session.flush()
        for i, atype in enumerate(types, start=1):
            db.session.add(ApprovalStep(matrix_id=matrix.id, step_no=i,
                                        approver_type=atype))
    db.session.commit()


def get_matrix(process_key):
    matrix = ApprovalMatrix.query.filter_by(process_key=process_key).first()
    if matrix is None:
        ensure_default_matrices()
        matrix = ApprovalMatrix.query.filter_by(process_key=process_key).first()
    return matrix


def snapshot_steps(matrix):
    return [{"step_no": s.step_no, "approver_type": s.approver_type,
             "approver_user_id": s.approver_user_id}
            for s in matrix.steps.order_by(ApprovalStep.step_no).all()]


def set_demo_matrices():
    """Replace matrix chains with the demo configuration (ESS_SEED_DEMO).
    Idempotent."""
    demo = {
        "employee_creation": ["hr_manager"],
        "leave": ["reporting_manager", "hr_staff", "hr_manager"],
        "regularisation": ["reporting_manager", "hr_staff"],
    }
    for process_key, types in demo.items():
        matrix = get_matrix(process_key)
        ApprovalStep.query.filter_by(matrix_id=matrix.id).delete()
        for i, atype in enumerate(types, start=1):
            db.session.add(ApprovalStep(matrix_id=matrix.id, step_no=i,
                                        approver_type=atype))
    db.session.commit()


# ---------------- step resolution ----------------

def _active(ids):
    return [u for u in ids
            if u and u.is_active]


def resolve_step_users(step, subject_user):
    """Return (user_ids, label) for one step definition.

    step: dict with approver_type / approver_user_id (from the instance
    snapshot or the live matrix).
    """
    atype = step.get("approver_type")
    users, label = [], APPROVER_TYPE_LABELS.get(atype, atype)
    if subject_user is None:
        return users, label
    if atype == "reporting_manager":
        mgr = User.query.get(subject_user.reporting_manager) \
            if subject_user.reporting_manager else None
        users = _active([mgr])
        if not users and subject_user.department:
            # Legacy fallback: when no reporting manager is assigned, the
            # active managers/admins of the employee's own department act as
            # the supervisor (matches pre-v3.4 department-scoped approvals).
            users = User.query.filter(
                User.department == subject_user.department,
                User.role.in_(("manager", "admin")),
                User.is_active.is_(True)).all()
            if users:
                label = "Reporting Manager (department managers)"
    elif atype == "skip_level_manager":
        mgr = User.query.get(subject_user.reporting_manager) \
            if subject_user.reporting_manager else None
        skip = User.query.get(mgr.reporting_manager) \
            if mgr and mgr.reporting_manager else None
        users = _active([skip])
    elif atype == "hr_staff":
        users = User.query.filter_by(is_hr_staff=True,
                                     is_active=True).all()
    elif atype == "hr_manager":
        users = User.query.filter_by(is_hr_manager=True,
                                     is_active=True).all()
        if not users:  # fallback so the chain never deadlocks
            users = User.query.filter_by(role="admin",
                                         is_active=True).all()
            label = "HR Manager (fallback: administrators)"
    elif atype == "admin":
        users = User.query.filter_by(role="admin", is_active=True).all()
    elif atype == "specific_user":
        target = User.query.get(step.get("approver_user_id")) \
            if step.get("approver_user_id") else None
        users = _active([target])
        if target:
            label = f"{target.name} ({target.id})"
    return [u.id for u in users], label


# ---------------- engine ----------------

def _notify(user_ids, title, body, link):
    # Note: ref_id is intentionally left unset — Notification.ref_id is a
    # shared integer namespace (notices use it for fan-out counting), so an
    # approval instance id could collide with a notice id. The link carries
    # the destination instead.
    from app.engagement import notify_user
    for uid in set(user_ids):
        notify_user(uid, "approval", title, body=body, link=link)


def _auto_skip_empty(instance):
    """Advance past steps with no resolvers, recording auto_skip."""
    while not instance.exhausted:
        step = instance.current_step_def
        subject = User.query.get(instance.subject_user_id)
        resolvers, _ = resolve_step_users(step, subject)
        if resolvers:
            break
        db.session.add(ApprovalAction(
            instance_id=instance.id, step_no=instance.current_step,
            actor_id=None, action="auto_skip",
            comment="No eligible approver for this step; skipped."))
        record("approval_auto_skip",
               f"{instance.process_key} #{instance.ref_id} step "
               f"{instance.current_step} skipped (no resolver)",
               entity="approval", entity_id=instance.id)
        instance.current_step += 1


def start_instance(process_key, ref_id, subject_user_id, requester_id):
    """Begin an approval chain; idempotent per (process_key, ref_id)."""
    existing = ApprovalInstance.query.filter_by(
        process_key=process_key, ref_id=str(ref_id)).first()
    if existing:
        return existing
    matrix = get_matrix(process_key)
    instance = ApprovalInstance(
        process_key=process_key, ref_id=str(ref_id),
        subject_user_id=subject_user_id, requester_id=requester_id,
        steps_json=json.dumps(snapshot_steps(matrix)),
        current_step=1, status="pending")
    db.session.add(instance)
    db.session.flush()
    _auto_skip_empty(instance)
    if not instance.exhausted:
        subject = User.query.get(subject_user_id)
        step = instance.current_step_def
        resolvers, label = resolve_step_users(step, subject)
        _notify(resolvers,
                f"Approval needed: {instance.process_label}",
                f"{subject.name if subject else subject_user_id} — "
                f"step {instance.current_step} of {instance.total_steps} "
                f"({label}).",
                _instance_link(instance))
    record("approval_started",
           f"{process_key} #{ref_id} chain started "
           f"({instance.total_steps} step(s))",
           entity="approval", entity_id=instance.id)
    db.session.commit()
    return instance


def get_instance(process_key, ref_id):
    return ApprovalInstance.query.filter_by(
        process_key=process_key, ref_id=str(ref_id)).first()


def get_or_start_instance(process_key, ref_id, subject_user_id, requester_id):
    return start_instance(process_key, ref_id, subject_user_id, requester_id)


def current_resolvers(instance):
    """User ids that may act on the instance's current step."""
    if instance.status != "pending" or instance.exhausted:
        return []
    step = instance.current_step_def
    subject = User.query.get(instance.subject_user_id)
    resolvers, _ = resolve_step_users(step, subject)
    return resolvers


def can_act(instance, actor):
    if instance.status != "pending":
        return False
    if actor.id in current_resolvers(instance):
        return True
    if actor.is_admin:
        # Admin backstops: hr_manager/admin steps (admins resolve there by
        # definition) and fully auto-skipped chains.
        step = instance.current_step_def
        if instance.exhausted:
            return True
        return bool(step and step.get("approver_type") in ("hr_manager",
                                                           "admin"))
    return False


def _instance_link(instance):
    if instance.process_key == "employee_creation":
        return f"/hrms/employees/{instance.ref_id}/review"
    if instance.process_key == "regularisation":
        return "/regularisation/approve"
    return "/approvals"


def act(instance, actor, approve, comment=""):
    """Record an approve/reject on the current step.

    Returns "advanced" | "final_approved" | "rejected".
    Raises PermissionError when the actor may not decide.
    """
    if not can_act(instance, actor):
        raise PermissionError("You are not an approver for this step.")
    comment = (comment or "").strip()[:500]
    if not approve:
        db.session.add(ApprovalAction(
            instance_id=instance.id, step_no=instance.current_step,
            actor_id=actor.id, action="reject", comment=comment))
        instance.status = "rejected"
        instance.decided_at = datetime.utcnow()
        instance.reject_reason = comment or None
        _notify([instance.requester_id or instance.subject_user_id],
                f"{instance.process_label} rejected",
                f"Your {instance.process_label.lower()} request was rejected "
                f"by {actor.name}." +
                (f" Reason: {comment}" if comment else ""),
                _instance_link(instance))
        log_action(actor.id, "approval_rejected",
                   f"{instance.process_key} #{instance.ref_id}")
        record("approval_rejected",
               f"{instance.process_key} #{instance.ref_id} rejected at step "
               f"{instance.current_step} by {actor.id}",
               entity="approval", entity_id=instance.id,
               field_name="status", old_value="pending", new_value="rejected")
        db.session.commit()
        return "rejected"

    db.session.add(ApprovalAction(
        instance_id=instance.id, step_no=instance.current_step,
        actor_id=actor.id, action="approve", comment=comment))
    log_action(actor.id, "approval_approved",
               f"{instance.process_key} #{instance.ref_id} step "
               f"{instance.current_step}")
    instance.current_step += 1
    _auto_skip_empty(instance)
    if instance.exhausted:
        instance.status = "approved"
        instance.decided_at = datetime.utcnow()
        record("approval_final_approved",
               f"{instance.process_key} #{instance.ref_id} fully approved",
               entity="approval", entity_id=instance.id,
               field_name="status", old_value="pending", new_value="approved")
        _notify([instance.requester_id or instance.subject_user_id],
                f"{instance.process_label} approved",
                f"Your {instance.process_label.lower()} request was fully "
                f"approved.",
                _instance_link(instance))
        db.session.commit()
        return "final_approved"
    subject = User.query.get(instance.subject_user_id)
    step = instance.current_step_def
    resolvers, label = resolve_step_users(step, subject)
    _notify(resolvers,
            f"Approval needed: {instance.process_label}",
            f"{subject.name if subject else instance.subject_user_id} — "
            f"step {instance.current_step} of {instance.total_steps} "
            f"({label}).",
            _instance_link(instance))
    record("approval_advanced",
           f"{instance.process_key} #{instance.ref_id} advanced to step "
           f"{instance.current_step}",
           entity="approval", entity_id=instance.id)
    db.session.commit()
    return "advanced"


def step_badge(instance):
    """Short progress label, e.g. 'Step 2 of 3 — HR Staff'."""
    if not instance or not instance.steps:
        return ""
    step = instance.current_step_def
    subject = User.query.get(instance.subject_user_id)
    _, label = resolve_step_users(step or {}, subject)
    if instance.exhausted:
        return f"All {instance.total_steps} step(s) cleared — awaiting decision"
    return f"Step {instance.current_step} of {instance.total_steps} — {label}"


def inbox_for(user):
    """Instances awaiting this user's action (or all, for admins)."""
    q = ApprovalInstance.query.filter_by(status="pending")\
        .order_by(ApprovalInstance.created_at.desc())
    if user.is_admin:
        return q.all()
    out = []
    for inst in q.all():
        if user.id in current_resolvers(inst):
            out.append(inst)
    return out


# ---------------- matrix admin UI ----------------

def _admin_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _preview_for_type(atype):
    """'Who resolves this today' preview for the matrix admin page."""
    if atype == "hr_staff":
        users = User.query.filter_by(is_hr_staff=True,
                                     is_active=True).all()
    elif atype == "hr_manager":
        users = User.query.filter_by(is_hr_manager=True,
                                     is_active=True).all()
        if not users:
            users = User.query.filter_by(role="admin",
                                         is_active=True).all()
            return users, "no one flagged — falls back to administrators"
    elif atype == "admin":
        users = User.query.filter_by(role="admin", is_active=True).all()
    else:
        return [], "depends on the employee (resolved per request)"
    if not users:
        return [], "no one flagged — this step will auto-skip"
    return users, ", ".join(f"{u.name} ({u.id})" for u in users)


@approvals_bp.route("/approval-matrix", methods=["GET", "POST"])
@login_required
@_admin_required
def matrix_admin():
    if request.method == "POST":
        action = request.form.get("action")
        process_key = request.form.get("process_key", "")
        matrix = ApprovalMatrix.query.filter_by(
            process_key=process_key).first()
        if not matrix:
            abort(404)
        if action == "add_step":
            atype = request.form.get("approver_type", "")
            if atype not in APPROVER_TYPE_LABELS:
                flash("Invalid approver type.", "error")
            else:
                uid = (request.form.get("approver_user_id") or "").strip() or None
                if atype == "specific_user":
                    if not uid or not User.query.get(uid):
                        flash("Choose a user for a Specific User step.",
                              "error")
                        return redirect(url_for("approvals.matrix_admin"))
                else:
                    uid = None
                last = db.session.query(db.func.max(ApprovalStep.step_no))\
                    .filter_by(matrix_id=matrix.id).scalar() or 0
                db.session.add(ApprovalStep(
                    matrix_id=matrix.id, step_no=last + 1,
                    approver_type=atype, approver_user_id=uid))
                db.session.commit()
                record("approval_matrix_changed",
                       f"{process_key}: added {atype} step",
                       entity="approval_matrix", entity_id=matrix.id)
                flash("Step added. In-flight requests keep their old chain.",
                      "success")
        elif action in ("delete_step", "move_up", "move_down"):
            step_id = int(request.form.get("step_id", 0))
            step = ApprovalStep.query.filter_by(
                id=step_id, matrix_id=matrix.id).first()
            if not step:
                abort(404)
            if action == "delete_step":
                remaining = ApprovalStep.query.filter_by(
                    matrix_id=matrix.id).count()
                if remaining <= 1:
                    flash("A chain must keep at least one step.", "error")
                else:
                    db.session.delete(step)
                    db.session.flush()
                    for i, s in enumerate(
                            ApprovalStep.query.filter_by(
                                matrix_id=matrix.id)
                            .order_by(ApprovalStep.step_no).all(), start=1):
                        s.step_no = i
                    db.session.commit()
                    record("approval_matrix_changed",
                           f"{process_key}: removed step",
                           entity="approval_matrix", entity_id=matrix.id)
                    flash("Step removed.", "success")
            else:
                sib_no = step.step_no + (-1 if action == "move_up" else 1)
                sib = ApprovalStep.query.filter_by(
                    matrix_id=matrix.id, step_no=sib_no).first()
                if sib:
                    # swap via a temp value: (matrix_id, step_no) is unique
                    a, b = step.step_no, sib.step_no
                    step.step_no = -1
                    db.session.flush()
                    sib.step_no = a
                    db.session.flush()
                    step.step_no = b
                    db.session.commit()
                    record("approval_matrix_changed",
                           f"{process_key}: step reordered",
                           entity="approval_matrix", entity_id=matrix.id)
                    flash("Step order updated.", "success")
        return redirect(url_for("approvals.matrix_admin"))
    matrices = []
    for process_key, label in APPROVAL_PROCESSES:
        matrix = get_matrix(process_key)
        steps = matrix.steps.order_by(ApprovalStep.step_no).all()
        step_views = []
        for s in steps:
            users, preview = _preview_for_type(s.approver_type)
            if s.approver_type == "specific_user":
                target = User.query.get(s.approver_user_id) \
                    if s.approver_user_id else None
                preview = f"{target.name} ({target.id})" if target \
                    else "user missing — this step will auto-skip"
            step_views.append({"step": s,
                               "label": APPROVER_TYPE_LABELS.get(
                                   s.approver_type, s.approver_type),
                               "preview": preview})
        matrices.append({"key": process_key, "label": label,
                         "matrix": matrix, "steps": step_views})
    all_users = User.query.filter_by(is_active=True).order_by(User.name).all()
    return render_template("hr_approval_matrix.html", matrices=matrices,
                           approver_types=APPROVER_TYPES, users=all_users)
