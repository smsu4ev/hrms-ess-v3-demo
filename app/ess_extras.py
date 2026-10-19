"""HRMS+ESS V3.3.1: ESS extras.

- Unified inbox (/inbox) + dashboard "My Tasks" card for all roles.
- Expense claims routed through the approval matrix (new process "expense");
  final approval creates an EARNING PayrollInput for the claimant.
- People directory + read-only org chart (privacy-safe).
- Employee letter requests with HR fulfilment via the mail-merge engine.
- Team leave calendar (department-scoped, approved leave only).
"""

import os
from datetime import date, datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, send_file)
from flask_login import login_required, current_user

from app import db
import config as app_config
from app.models import (
    User, LeaveRequest, LeaveBalance, RegularisationRequest, ExpenseClaim,
    LetterRequest, LETTER_REQUEST_TYPES, LETTER_REQUEST_STATUSES,
    EXPENSE_CATEGORIES, PayrollInput, GeneratedLetter, LetterTemplate,
    TrainingAssignment, TrainingAttempt, TrainingCourse,
    JDAcknowledgement, JobDescription, ApprovalInstance)
from app.auth import log_action
from app.audit import record as audit_record

extras_bp = Blueprint("extras", __name__)

RECEIPT_DIR = "expenses"
RECEIPT_MAX_BYTES = 10 * 1024 * 1024
LETTER_UPLOAD_DIR = "letter_uploads"
LETTER_UPLOAD_MAX_BYTES = 10 * 1024 * 1024

LETTER_TYPE_LABELS = dict(LETTER_REQUEST_TYPES)
LETTER_STATUS_LABELS = dict(LETTER_REQUEST_STATUSES)

# LetterRequest type -> mail-merge LetterTemplate name (v3.3.1 seeds the
# two new non-salary templates; NOC uses the request's purpose text).
LETTER_TYPE_TEMPLATES = {
    "salary_certificate": "Salary Certificate",
    "salary_transfer": "Salary Transfer Letter",
    "employment_certificate": "Employment Certificate",
    "experience_letter": "Experience Letter",
    "noc": "No Objection Certificate",
}
# Types whose generated letter carries salary figures: generation is gated
# to users with salary visibility (admin or can_view_salary), mirroring the
# letters engine's own rule.
SALARY_LETTER_TYPES = {"salary_certificate", "salary_transfer"}


def _hr_required(fn):
    """Admin, HR Manager or HR Staff — the HR back-office roles."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not (current_user.is_admin or current_user.is_hr_manager
                or current_user.is_hr_staff):
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _approver_required(fn):
    """Anyone who can plausibly hold an approval step (mirrors the
    approver_required guard in app/views.py)."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not (current_user.is_manager or current_user.is_hr_staff
                or current_user.is_hr_manager):
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _instance_link(instance):
    if instance.process_key == "employee_creation":
        return f"/hrms/employees/{instance.ref_id}/review"
    if instance.process_key == "regularisation":
        return "/regularisation/approve"
    # leave, expense and anything else are decided on /approvals
    return "/approvals"


def ensure_new_letter_templates():
    """Idempotent: seed letter templates added after the initial set (the
    original seed only ran when the table was empty)."""
    from app.models import LetterTemplate
    existing = {t.name for t in LetterTemplate.query.all()}
    added = 0
    for name in ("Employment Certificate", "Experience Letter",
                 "No Objection Certificate"):
        if name not in existing and name in app_config.DEFAULT_LETTER_TEMPLATES:
            db.session.add(LetterTemplate(
                name=name, body=app_config.DEFAULT_LETTER_TEMPLATES[name],
                updated_by="SYSTEM"))
            added += 1
    if added:
        db.session.commit()
    return added


# ================= Unified inbox =================

def _jd_tasks(user):
    out = []
    for ack in JDAcknowledgement.query.filter_by(user_id=user.id).all():
        jd = JobDescription.query.get(ack.jd_id)
        if not jd or not jd.is_active:
            continue
        if ack.status != "acknowledged" or ack.jd_version < jd.version:
            out.append({"ack": ack, "jd": jd})
    return out


def _training_tasks(user):
    passed = {a.course_id for a in TrainingAttempt.query.filter_by(
        user_id=user.id, passed=True).all()}
    out = []
    for a in TrainingAssignment.query.filter_by(user_id=user.id).all():
        if a.course_id in passed:
            continue
        course = TrainingCourse.query.get(a.course_id)
        out.append({"assignment": a,
                    "course": course})
    return out


def _my_requests(user):
    """The user's own requests still in flight (informational)."""
    from app.approvals import get_or_start_instance, step_badge
    out = []
    for r in LeaveRequest.query.filter_by(
            user_id=user.id, status="pending").all():
        inst = get_or_start_instance("leave", r.id, user.id, user.id)
        out.append({"kind": "Leave",
                    "text": f"{app_config.LEAVE_TYPES.get(r.leave_type, {}).get('label', r.leave_type)} "
                            f"({r.start_date} to {r.end_date})",
                    "badge": step_badge(inst), "link": "/leave"})
    for r in RegularisationRequest.query.filter_by(
            user_id=user.id, status="pending").all():
        inst = get_or_start_instance("regularisation", r.id, user.id, user.id)
        out.append({"kind": "Attendance regularisation",
                    "text": f"{r.date}", "badge": step_badge(inst),
                    "link": "/attendance/regularisation"})
    for c in ExpenseClaim.query.filter_by(
            user_id=user.id, status="pending").all():
        inst = get_or_start_instance("expense", c.id, user.id, user.id)
        out.append({"kind": "Expense claim",
                    "text": f"#{c.id} {c.category} — AED {c.amount:.2f}",
                    "badge": step_badge(inst), "link": "/my-expenses"})
    for l in LetterRequest.query.filter(
            LetterRequest.user_id == user.id,
            LetterRequest.status.in_(["submitted", "in_progress",
                                      "ready"])).all():
        out.append({"kind": "Letter request",
                    "text": f"{LETTER_TYPE_LABELS.get(l.letter_type, l.letter_type)} "
                            f"(#{l.id})",
                    "badge": LETTER_STATUS_LABELS.get(l.status, l.status),
                    "link": "/my-letters"})
    return out


def inbox_tasks(user):
    """Full action-item breakdown for the unified inbox."""
    from app.approvals import inbox_for, step_badge, PROCESS_LABELS
    from app.engagement import pending_mandatory_notices
    approval_items = []
    for inst in inbox_for(user):
        subject = User.query.get(inst.subject_user_id)
        approval_items.append({
            "instance": inst,
            "label": PROCESS_LABELS.get(inst.process_key, inst.process_key),
            "subject": subject.name if subject else inst.subject_user_id,
            "badge": step_badge(inst),
            "link": _instance_link(inst)})
    return {
        "approvals": approval_items,
        "notices": pending_mandatory_notices(user),
        "jds": _jd_tasks(user),
        "training": _training_tasks(user),
        "mine": _my_requests(user),
    }


def inbox_summary(user):
    """Compact summary for the dashboard "My Tasks" card: total actionable
    count + up to 3 headline items."""
    tasks = inbox_tasks(user)
    items = []
    for i in tasks["approvals"]:
        items.append({"icon": "check",
                      "text": f"Approve: {i['label']} — {i['subject']} "
                              f"({i['badge']})",
                      "link": i["link"]})
    for n in tasks["notices"]:
        items.append({"icon": "bell", "text": f"Acknowledge notice: {n.title}",
                      "link": f"/notices/{n.id}"})
    for j in tasks["jds"]:
        items.append({"icon": "file",
                      "text": f"Acknowledge JD: {j['jd'].title} "
                              f"(v{j['jd'].version})",
                      "link": "/my-jd"})
    for t in tasks["training"]:
        cname = t["course"].title if t["course"] else "Training"
        items.append({"icon": "graduation",
                      "text": f"Complete training: {cname}",
                      "link": "/my-training"})
    return {"total": len(items), "items": items[:3]}


@extras_bp.route("/inbox")
@login_required
def inbox():
    tasks = inbox_tasks(current_user)
    return render_template("inbox.html", tasks=tasks)


# ================= Expense claims =================

def _expense_approve_side_effects(claim, actor_id, comment=""):
    """Final-approval side effects: the reimbursement becomes an EARNING
    payroll input in the current period, so the next payroll run picks it
    up exactly like any other addition."""
    claim.status = "approved"
    period = date.today().strftime("%Y-%m")
    inp = PayrollInput(
        period=period, user_id=claim.user_id, kind="addition",
        label=f"Expense reimbursement (claim #{claim.id})",
        amount=round(claim.amount, 2), created_by=actor_id,
        note=(claim.description or "")[:200])
    db.session.add(inp)
    db.session.flush()
    claim.payroll_input_id = inp.id
    claim.decided_by = actor_id
    claim.decision_comment = (comment or "")[:500] or None
    from app.engagement import notify_user
    notify_user(claim.user_id, "expense", "Expense claim approved",
                body=f"Claim #{claim.id} ({claim.category}, "
                     f"AED {claim.amount:.2f}) approved — added to payroll "
                     f"inputs for {period}.",
                link="/my-expenses")
    log_action(actor_id, "expense_approved",
               f"claim #{claim.id} ({claim.user_id}) AED {claim.amount:.2f}")
    audit_record("expense_approved",
                 f"claim #{claim.id} for {claim.user_id}: "
                 f"AED {claim.amount:.2f} -> payroll input {inp.id} "
                 f"({period})",
                 entity="expense", entity_id=claim.id,
                 field_name="status", old_value="pending",
                 new_value="approved")


def _expense_reject_side_effects(claim, actor_id, comment):
    claim.status = "rejected"
    claim.decided_by = actor_id
    claim.decision_comment = (comment or "")[:500] or None
    from app.engagement import notify_user
    notify_user(claim.user_id, "expense", "Expense claim rejected",
                body=f"Claim #{claim.id} ({claim.category}, "
                     f"AED {claim.amount:.2f}) was rejected." +
                     (f" Reason: {comment}" if comment else ""),
                link="/my-expenses")
    log_action(actor_id, "expense_rejected",
               f"claim #{claim.id} ({claim.user_id})")
    audit_record("expense_rejected",
                 f"claim #{claim.id} for {claim.user_id}: AED "
                 f"{claim.amount:.2f}",
                 entity="expense", entity_id=claim.id,
                 field_name="status", old_value="pending",
                 new_value="rejected")


@extras_bp.route("/my-expenses", methods=["GET", "POST"])
@login_required
def my_expenses():
    from app.approvals import (start_instance, get_or_start_instance,
                               current_resolvers, step_badge)
    from app.engagement import notify_user
    from app.files import store_file, kind_from_bytes
    if request.method == "POST":
        category = (request.form.get("category") or "").strip()
        desc = (request.form.get("description") or "").strip()[:300]
        date_raw = (request.form.get("claim_date") or "").strip()
        try:
            amount = float(request.form.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            claim_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
        except ValueError:
            claim_date = None
        errors = []
        if category not in dict(EXPENSE_CATEGORIES):
            errors.append("Choose a valid category.")
        if amount <= 0:
            errors.append("Amount must be greater than zero.")
        if claim_date is None:
            errors.append("Enter a valid expense date.")
        if claim_date and claim_date > date.today():
            errors.append("Expense date cannot be in the future.")
        receipt_path = None
        file = request.files.get("receipt")
        if file and file.filename:
            data = file.read()
            kind = kind_from_bytes(data)
            if len(data) > RECEIPT_MAX_BYTES:
                errors.append("Receipt must be under 10 MB.")
            elif kind not in ("pdf", "jpg", "png"):
                errors.append("Receipt must be a PDF, JPG or PNG file.")
            else:
                receipt_path = store_file(data, RECEIPT_DIR, kind)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            claim = ExpenseClaim(
                user_id=current_user.id, claim_date=claim_date,
                category=category, amount=round(amount, 2),
                description=desc or None, receipt_path=receipt_path)
            db.session.add(claim)
            db.session.flush()
            instance = start_instance("expense", claim.id, current_user.id,
                                      current_user.id)
            resolvers = current_resolvers(instance)
            for uid in resolvers:
                notify_user(uid, "approval",
                            "Expense claim awaiting approval",
                            body=f"{current_user.name} submitted claim "
                                 f"#{claim.id} ({category}, "
                                 f"AED {claim.amount:.2f}).",
                            link="/approvals")
            log_action(current_user.id, "expense_submitted",
                       f"claim #{claim.id} AED {claim.amount:.2f}")
            audit_record("expense_submitted",
                         f"claim #{claim.id} by {current_user.id}: "
                         f"{category} AED {claim.amount:.2f}",
                         entity="expense", entity_id=claim.id)
            db.session.commit()
            flash(f"Claim #{claim.id} submitted for approval.", "success")
            return redirect(url_for("extras.my_expenses"))
    claims = ExpenseClaim.query.filter_by(
        user_id=current_user.id).order_by(
        ExpenseClaim.created_at.desc()).all()
    from app.approvals import get_or_start_instance, step_badge
    badges = {}
    for c in claims:
        if c.status == "pending":
            badges[c.id] = step_badge(
                get_or_start_instance("expense", c.id, c.user_id, c.user_id))
    return render_template("my_expenses.html", claims=claims, badges=badges,
                           categories=EXPENSE_CATEGORIES,
                           today=date.today().isoformat())


@extras_bp.route("/expenses/<int:claim_id>", methods=["POST"])
@login_required
@_approver_required
def decide_expense(claim_id):
    """Approve/reject one step of an expense claim through the engine."""
    from app.approvals import (get_or_start_instance, act, self_blocked,
                               current_resolvers)
    claim = ExpenseClaim.query.get_or_404(claim_id)
    if claim.status != "pending":
        flash("Claim already decided.", "error")
        return redirect(url_for("views.approvals"))
    instance = get_or_start_instance("expense", claim.id, claim.user_id,
                                     claim.user_id)
    action = request.form.get("action")
    comment = (request.form.get("comment") or "").strip()[:500]
    if action not in ("approve", "reject"):
        abort(400)
    try:
        outcome = act(instance, current_user, action == "approve", comment)
    except PermissionError as e:
        if self_blocked(instance, current_user) and "own request" in str(e):
            flash("You cannot approve your own request.", "error")
            return redirect(url_for("views.approvals"))
        abort(403)
    if outcome == "rejected":
        _expense_reject_side_effects(claim, current_user.id, comment)
        flash("Expense claim rejected.", "success")
    elif outcome == "final_approved":
        _expense_approve_side_effects(claim, current_user.id, comment)
        flash("Expense claim approved — added to payroll inputs.", "success")
    else:
        flash("Approved at this step; forwarded to the next approver.",
              "success")
    db.session.commit()
    return redirect(url_for("views.approvals"))


@extras_bp.route("/hrms/expenses")
@login_required
@_hr_required
def hr_expenses():
    status = (request.args.get("status") or "").strip()
    dept = (request.args.get("dept") or "").strip()
    q = ExpenseClaim.query
    if status in ("pending", "approved", "rejected"):
        q = q.filter_by(status=status)
    claims = q.order_by(ExpenseClaim.created_at.desc()).all()
    users = {u.id: u for u in User.query.all()}
    if dept:
        claims = [c for c in claims
                  if users.get(c.user_id)
                  and users[c.user_id].department == dept]
    approved_total = sum(c.amount for c in ExpenseClaim.query.filter_by(
        status="approved").all())
    departments = sorted({u.department for u in users.values()
                          if u.department and u.is_active})
    return render_template("hr_expenses.html", claims=claims, users=users,
                           status=status, dept=dept,
                           departments=departments,
                           approved_total=approved_total,
                           categories=dict(EXPENSE_CATEGORIES))


# ================= Letter requests =================

def _letter_notify(lr, title, body, actor_id=None):
    from app.engagement import notify_user
    notify_user(lr.user_id, "letter", title, body=body, link="/my-letters")
    audit_record("letter_request_" + lr.status,
                 f"letter request #{lr.id} ({lr.letter_type}) for "
                 f"{lr.user_id}: {title}",
                 entity="letter_request", entity_id=lr.id,
                 field_name="status", actor_id=actor_id)


@extras_bp.route("/my-letters", methods=["GET", "POST"])
@login_required
def my_letters():
    if request.method == "POST":
        ltype = (request.form.get("letter_type") or "").strip()
        purpose = (request.form.get("purpose") or "").strip()[:300]
        if ltype not in LETTER_TYPE_LABELS:
            flash("Choose a valid letter type.", "error")
        else:
            lr = LetterRequest(user_id=current_user.id, letter_type=ltype,
                               purpose=purpose or None)
            db.session.add(lr)
            db.session.flush()
            from app.engagement import notify_user
            # Tell HR staff/managers someone needs a letter.
            hr_ids = [u.id for u in User.query.filter(
                User.is_active.is_(True),
                (User.role == "admin") | (User.is_hr_staff.is_(True))
                | (User.is_hr_manager.is_(True))).all()]
            for uid in set(hr_ids):
                notify_user(uid, "letter",
                            f"Letter request #{lr.id}: "
                            f"{LETTER_TYPE_LABELS[ltype]}",
                            body=f"{current_user.name} requested a "
                                 f"{LETTER_TYPE_LABELS[ltype].lower()}.",
                            link="/hrms/letter-requests")
            log_action(current_user.id, "letter_requested",
                       f"request #{lr.id} ({ltype})")
            audit_record("letter_requested",
                         f"request #{lr.id} by {current_user.id}: {ltype}",
                         entity="letter_request", entity_id=lr.id)
            db.session.commit()
            flash(f"{LETTER_TYPE_LABELS[ltype]} requested.", "success")
            return redirect(url_for("extras.my_letters"))
    reqs = LetterRequest.query.filter_by(
        user_id=current_user.id).order_by(
        LetterRequest.created_at.desc()).all()
    return render_template("my_letters.html", reqs=reqs,
                           types=LETTER_REQUEST_TYPES,
                           type_labels=LETTER_TYPE_LABELS,
                           status_labels=LETTER_STATUS_LABELS)


@extras_bp.route("/my-letters/<int:lid>/download")
@login_required
def letter_download(lid):
    """The employee downloads their own fulfilled letter."""
    lr = LetterRequest.query.get_or_404(lid)
    if lr.user_id != current_user.id:
        abort(403)
    if lr.status not in ("ready", "delivered"):
        flash("This letter is not ready yet.", "error")
        return redirect(url_for("extras.my_letters"))
    if lr.generated_letter_id:
        letter = GeneratedLetter.query.get(lr.generated_letter_id)
        user = User.query.get(lr.user_id)
        audit_record("letter_downloaded",
                     f"letter request #{lr.id} by owner",
                     entity="letter_request", entity_id=lr.id,
                     actor_id=current_user.id)
        db.session.commit()
        # Reuse the HR letter print view; the owner is entitled to their own
        # letter regardless of the salary-visibility permission.
        return render_template("hr_letter_view.html", letter=letter,
                               user=user)
    if lr.uploaded_path:
        from app.files import upload_root
        path = os.path.join(upload_root(), LETTER_UPLOAD_DIR,
                            lr.uploaded_path)
        if not os.path.isfile(path):
            flash("The uploaded file is missing.", "error")
            return redirect(url_for("extras.my_letters"))
        audit_record("letter_downloaded",
                     f"letter request #{lr.id} by owner (uploaded file)",
                     entity="letter_request", entity_id=lr.id,
                     actor_id=current_user.id)
        db.session.commit()
        return send_file(path, as_attachment=True,
                         download_name=f"letter-request-{lr.id}.pdf")
    flash("No letter file is attached yet.", "error")
    return redirect(url_for("extras.my_letters"))


@extras_bp.route("/my-letters/<int:lid>/received", methods=["POST"])
@login_required
def letter_received(lid):
    lr = LetterRequest.query.get_or_404(lid)
    if lr.user_id != current_user.id:
        abort(403)
    if lr.status != "ready":
        flash("Only a ready letter can be marked as received.", "error")
    else:
        lr.status = "delivered"
        _letter_notify(lr, "Letter received",
                       f"Letter request #{lr.id} marked as received.",
                       actor_id=current_user.id)
        db.session.commit()
        flash("Marked as received. Thank you.", "success")
    return redirect(url_for("extras.my_letters"))


@extras_bp.route("/hrms/letter-requests", methods=["GET", "POST"])
@login_required
@_hr_required
def hr_letter_requests():
    from app.files import salary_visible, store_file, kind_from_bytes
    from app.payroll_views import (merge_placeholders, PLACEHOLDER_RE,
                                   SALARY_PLACEHOLDERS)
    status = (request.args.get("status") or "").strip()
    if request.method == "POST":
        action = request.form.get("action")
        lr = LetterRequest.query.get_or_404(
            int(request.form.get("request_id", 0)))
        comment = (request.form.get("hr_comment") or "").strip()[:500]
        if action == "start" and lr.status == "submitted":
            lr.status = "in_progress"
            lr.processed_by = current_user.id
            if comment:
                lr.hr_comment = comment
            _letter_notify(lr, "Letter request in progress",
                           f"HR is preparing your "
                           f"{LETTER_TYPE_LABELS.get(lr.letter_type)}. "
                           f"(request #{lr.id})",
                           actor_id=current_user.id)
            flash("Request marked in progress.", "success")
        elif action == "generate" and lr.status in ("submitted",
                                                    "in_progress"):
            tpl_name = LETTER_TYPE_TEMPLATES.get(lr.letter_type)
            tpl = LetterTemplate.query.filter_by(
                name=tpl_name, is_active=True).first()
            if not tpl:
                flash(f"Template '{tpl_name}' is not available.", "error")
            else:
                used = set(PLACEHOLDER_RE.findall(tpl.body))
                if used & SALARY_PLACEHOLDERS \
                        and not salary_visible(current_user):
                    flash("This letter contains salary figures. Salary "
                          "visibility is required (assigned by the HR "
                          "Manager).", "error")
                else:
                    user = User.query.get(lr.user_id)
                    extra = {"purpose": lr.purpose or ""} \
                        if lr.letter_type == "noc" else None
                    rendered = merge_placeholders(tpl.body, user,
                                                  extra=extra)
                    letter = GeneratedLetter(
                        user_id=lr.user_id, template_id=tpl.id,
                        rendered_text=rendered,
                        has_salary_data=bool(used & SALARY_PLACEHOLDERS),
                        generated_by=current_user.id)
                    db.session.add(letter)
                    db.session.flush()
                    lr.generated_letter_id = letter.id
                    lr.status = "ready"
                    lr.processed_by = current_user.id
                    if comment:
                        lr.hr_comment = comment
                    audit_record("letter_generated",
                                 f"{tpl.name} for {lr.user_id} "
                                 f"(request #{lr.id})",
                                 entity="letter", entity_id=letter.id,
                                 field_name=tpl.name,
                                 actor_id=current_user.id)
                    _letter_notify(lr, "Your letter is ready",
                                   f"Your "
                                   f"{LETTER_TYPE_LABELS.get(lr.letter_type)} "
                                   f"(request #{lr.id}) is ready to "
                                   f"download.",
                                   actor_id=current_user.id)
                    flash(f"{tpl.name} generated and linked.", "success")
        elif action == "upload" and lr.status in ("submitted",
                                                  "in_progress"):
            file = request.files.get("signed_pdf")
            if not file or not file.filename:
                flash("Choose a signed PDF to upload.", "error")
            else:
                data = file.read()
                if len(data) > LETTER_UPLOAD_MAX_BYTES:
                    flash("File must be under 10 MB.", "error")
                elif kind_from_bytes(data) != "pdf":
                    flash("The signed letter must be a PDF file.", "error")
                else:
                    lr.uploaded_path = store_file(data, LETTER_UPLOAD_DIR,
                                                  "pdf")
                    lr.status = "ready"
                    lr.processed_by = current_user.id
                    if comment:
                        lr.hr_comment = comment
                    audit_record("letter_uploaded",
                                 f"signed letter for request #{lr.id}",
                                 entity="letter_request", entity_id=lr.id,
                                 actor_id=current_user.id)
                    _letter_notify(lr, "Your letter is ready",
                                   f"Your "
                                   f"{LETTER_TYPE_LABELS.get(lr.letter_type)} "
                                   f"(request #{lr.id}) is ready to "
                                   f"download.",
                                   actor_id=current_user.id)
                    flash("Signed letter uploaded; request is ready.",
                          "success")
        elif action == "reject" and lr.status in ("submitted",
                                                  "in_progress"):
            lr.status = "rejected"
            lr.processed_by = current_user.id
            if comment:
                lr.hr_comment = comment
            _letter_notify(lr, "Letter request rejected",
                           f"Your letter request #{lr.id} was rejected."
                           + (f" Reason: {comment}" if comment else ""),
                           actor_id=current_user.id)
            flash("Request rejected.", "success")
        else:
            flash("That action is not available for this request.", "error")
        db.session.commit()
        return redirect(url_for("extras.hr_letter_requests",
                                status=status or None))
    q = LetterRequest.query
    if status in dict(LETTER_REQUEST_STATUSES):
        q = q.filter_by(status=status)
    reqs = q.order_by(LetterRequest.created_at.desc()).all()
    users = {u.id: u for u in User.query.all()}
    return render_template("hr_letter_requests.html", reqs=reqs, users=users,
                           status=status,
                           type_labels=LETTER_TYPE_LABELS,
                           status_labels=LETTER_STATUS_LABELS)


# ================= People directory + org chart =================

@extras_bp.route("/directory")
@login_required
def directory():
    """Searchable employee directory. Privacy: no phone, bank, IBAN, salary,
    DOB or identity numbers are ever rendered here."""
    s = (request.args.get("q") or "").strip()
    q = User.query.filter_by(is_active=True)
    if s:
        like = f"%{s}%"
        q = q.filter((User.name.ilike(like)) | (User.designation.ilike(like))
                     | (User.department.ilike(like)))
    people = q.order_by(User.name).all()
    managers = {u.id: u for u in User.query.filter(
        User.id.in_([p.reporting_manager for p in people
                     if p.reporting_manager])).all()}
    return render_template("directory.html", people=people,
                           managers=managers, q=s)


@extras_bp.route("/org-chart")
@login_required
def org_chart():
    """Read-only org tree from reporting_manager links (cycle-guarded)."""
    users = {u.id: u for u in User.query.filter_by(is_active=True).all()}
    children = {}
    roots = []
    for u in users.values():
        mgr = u.reporting_manager
        if mgr and mgr in users and mgr != u.id:
            children.setdefault(mgr, []).append(u.id)
        else:
            roots.append(u.id)

    def node(uid, depth, seen):
        if depth > 12 or uid in seen:  # cycle / runaway guard
            return None
        u = users.get(uid)
        if not u:
            return None
        seen = seen | {uid}
        kids = []
        for cid in sorted(children.get(uid, []),
                          key=lambda c: users[c].name):
            n = node(cid, depth + 1, seen)
            if n:
                kids.append(n)
        return {"user": u, "children": kids}

    tree = [n for n in (node(r, 0, set())
                        for r in sorted(roots,
                                        key=lambda r: users[r].name))
            if n]
    return render_template("org_chart.html", tree=tree)


# ================= Team leave calendar =================

@extras_bp.route("/team-calendar")
@login_required
def team_calendar():
    """Approved leave of the viewer's department for one month. Admins see
    all departments with a filter. No balances, no reasons."""
    raw = (request.args.get("month") or "").strip()
    try:
        first = datetime.strptime(raw, "%Y-%m").date().replace(day=1)
    except ValueError:
        first = date.today().replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    prev = (first - timedelta(days=1)).replace(day=1)

    if current_user.is_admin:
        dept = (request.args.get("dept") or "").strip() or None
    else:
        dept = current_user.department
    q = LeaveRequest.query.filter(
        LeaveRequest.status == "approved",
        LeaveRequest.start_date < nxt,
        LeaveRequest.end_date >= first)
    rows = q.order_by(LeaveRequest.start_date).all()
    users = {u.id: u for u in User.query.all()}
    items = []
    for r in rows:
        u = users.get(r.user_id)
        if not u or not u.is_active:
            continue
        if dept and u.department != dept:
            continue
        items.append({"user": u, "req": r,
                      "label": app_config.LEAVE_TYPES.get(
                          r.leave_type, {}).get("label", r.leave_type)})
    departments = sorted({u.department for u in users.values()
                          if u.department and u.is_active})
    return render_template("team_calendar.html", items=items,
                           month=first.strftime("%Y-%m"),
                           month_label=first.strftime("%B %Y"),
                           prev=prev.strftime("%Y-%m"),
                           next=nxt.strftime("%Y-%m"),
                           dept=dept, departments=departments,
                           is_admin=current_user.is_admin)


# ================= Department / stakeholder overview =================

def _dxb_today():
    """Asia/Dubai calendar date via the app's fixed UTC+4 helper."""
    return (datetime.utcnow() + timedelta(hours=4)).date()


def _overview_scope(user):
    """Departments this user may see on the team overview.

    Returns None for the all-departments scope (admin/HR roles), a set of
    department names for managers (own department + departments of their
    direct reports), or an empty set for plain employees (403).
    """
    if user.is_admin or user.is_hr_manager or user.is_hr_staff:
        return None
    if not user.is_manager:
        return set()
    depts = {user.department} if user.department else set()
    for r in User.query.filter_by(reporting_manager=user.id,
                                  is_active=True).all():
        if r.department:
            depts.add(r.department)
    return depts


def _dept_day_stats(dept, today):
    """One department row: staff / absent today / on vacation / joined
    last month. Returns (stats dict, absent users list)."""
    from app.models import AttendanceRecord
    staff = User.query.filter_by(is_active=True, department=dept).all()
    ids = [u.id for u in staff]
    absent_users = []
    vacation_names = 0
    if ids:
        absent_users = User.query.join(
            AttendanceRecord,
            AttendanceRecord.user_id == User.id).filter(
            AttendanceRecord.date == today,
            AttendanceRecord.status == "absent",
            User.id.in_(ids),
            User.is_active.is_(True)).all()
        vacation_names = LeaveRequest.query.filter(
            LeaveRequest.user_id.in_(ids),
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today).count()
    first_this = today.replace(day=1)
    prev_first = (first_this - timedelta(days=1)).replace(day=1)
    joined = sum(1 for u in staff
                 if u.joining_date and prev_first <= u.joining_date
                 <= first_this - timedelta(days=1))
    return ({"department": dept, "staff": len(staff),
             "absent": len(absent_users), "vacation": vacation_names,
             "joined": joined}, absent_users)


@extras_bp.route("/team-overview")
@login_required
def team_overview():
    scope = _overview_scope(current_user)
    if scope is not None and not scope:
        abort(403)
    today = _dxb_today()
    if scope is None:
        depts = sorted({u.department for u in User.query.filter_by(
            is_active=True).all() if u.department})
    else:
        depts = sorted(scope)
    rows, absent_map = [], {}
    for d in depts:
        stats, absent_users = _dept_day_stats(d, today)
        rows.append(stats)
        absent_map[d] = absent_users
    totals = {"department": "Total", "staff": sum(r["staff"] for r in rows),
              "absent": sum(r["absent"] for r in rows),
              "vacation": sum(r["vacation"] for r in rows),
              "joined": sum(r["joined"] for r in rows)}
    return render_template("team_overview.html", rows=rows, totals=totals,
                           today=today)


@extras_bp.route("/team-overview/absent")
@login_required
def team_overview_absent():
    """Today's absent employees within the viewer's scope (403 outside)."""
    scope = _overview_scope(current_user)
    if scope is not None and not scope:
        abort(403)
    dept = (request.args.get("dept") or "").strip() or None
    if scope is not None and dept and dept not in scope:
        abort(403)
    today = _dxb_today()
    depts = sorted(scope) if scope is not None else sorted(
        {u.department for u in User.query.filter_by(is_active=True).all()
         if u.department})
    if dept:
        depts = [dept]
    people = []
    for d in depts:
        _, absent_users = _dept_day_stats(d, today)
        people.extend(absent_users)
    people.sort(key=lambda u: (u.department or "", u.name or ""))
    return render_template("team_overview_absent.html", people=people,
                           dept=dept, today=today)


def dept_today(user):
    """Dashboard mini-card data for managers/admins/HR: staff, absent
    today and on vacation within their overview scope. None for plain
    employees."""
    scope = _overview_scope(user)
    if scope is not None and not scope:
        return None
    today = _dxb_today()
    if scope is None:
        depts = sorted({u.department for u in User.query.filter_by(
            is_active=True).all() if u.department})
    else:
        depts = sorted(scope)
    staff = absent = vacation = 0
    for d in depts:
        stats, _ = _dept_day_stats(d, today)
        staff += stats["staff"]
        absent += stats["absent"]
        vacation += stats["vacation"]
    return {"staff": staff, "absent": absent, "vacation": vacation,
            "date": today}
