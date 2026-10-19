"""Helpdesk tickets + shift swaps (v3.5).

PART A — HelpdeskTicket / TicketComment: an internal support channel that is
explicitly distinct from the formal Grievances module. Public comments are
visible to the requester; internal notes are staff-only and must never be
rendered to the requester.

PART B — ShiftSwapRequest: exchange of shift patterns between two
RosterAssignment rows, gated first by the counterpart's consent and then by
the approval matrix (process "shift_swap").
"""
from datetime import datetime, timedelta, date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)
from flask_login import login_required, current_user

from app import db
from app.models import (User, HelpdeskTicket, TicketComment, ShiftSwapRequest,
                        RosterAssignment, ShiftPattern, ApprovalAction,
                        TICKET_CATEGORIES, TICKET_PRIORITIES,
                        TICKET_SLA_HOURS, TICKET_STATUSES)
from app.audit import record as audit_record
from app.auth import log_action
from app.engagement import notify_user

helpdesk_bp = Blueprint("helpdesk", __name__)

GST = timedelta(hours=4)


def _gst_today():
    return (datetime.utcnow() + GST).date()


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


def _ticket_staff_required(fn):
    """Helpdesk staff: admin/HR roles, or anyone currently assigned a
    ticket."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if current_user.is_admin or current_user.is_hr_manager \
                or current_user.is_hr_staff:
            return fn(*a, **kw)
        assigned = HelpdeskTicket.query.filter_by(
            assignee_id=current_user.id).first()
        if assigned is None:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _staff_user_ids():
    return [u.id for u in User.query.filter(
        User.is_active == True,  # noqa: E712
        ((User.role == "admin") | (User.is_hr_staff == True)  # noqa: E712
         | (User.is_hr_manager == True))).all()]  # noqa: E712


# ==================== PART A: helpdesk tickets ====================

@helpdesk_bp.route("/my-tickets", methods=["GET", "POST"])
@login_required
def my_tickets():
    if request.method == "POST":
        category = (request.form.get("category") or "").strip()
        subject = (request.form.get("subject") or "").strip()
        description = (request.form.get("description") or "").strip()
        priority = (request.form.get("priority") or "Normal").strip()
        errors = []
        if category not in [c for c, _ in TICKET_CATEGORIES]:
            errors.append("Choose a category.")
        if not subject:
            errors.append("Subject is required.")
        if not description:
            errors.append("Description is required.")
        if priority not in TICKET_PRIORITIES:
            errors.append("Invalid priority.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            t = HelpdeskTicket(requester_id=current_user.id,
                               category=category, subject=subject,
                               description=description, priority=priority,
                               status="open")
            db.session.add(t)
            db.session.flush()
            audit_record("ticket_created",
                         f"ticket #{t.id} [{category}/{priority}]: {subject}",
                         entity="ticket", entity_id=t.id,
                         field_name="status", old_value=None,
                         new_value="open")
            log_action(current_user.id, "ticket_created",
                       f"helpdesk ticket #{t.id}")
            db.session.commit()
            flash(f"Ticket #{t.id} raised. "
                  f"Expected response within "
                  f"{TICKET_SLA_HOURS[priority]} hours.", "success")
            return redirect(url_for("helpdesk.my_tickets"))
    tickets = HelpdeskTicket.query.filter_by(
        requester_id=current_user.id).order_by(
        HelpdeskTicket.created_at.desc()).all()
    return render_template("my_tickets.html", tickets=tickets,
                           categories=TICKET_CATEGORIES,
                           priorities=TICKET_PRIORITIES,
                           sla_hours=TICKET_SLA_HOURS)


@helpdesk_bp.route("/my-tickets/<int:ticket_id>", methods=["GET", "POST"])
@login_required
def my_ticket_detail(ticket_id):
    t = HelpdeskTicket.query.get_or_404(ticket_id)
    if t.requester_id != current_user.id:
        abort(403)
    if request.method == "POST":
        if t.status in ("closed",):
            flash("Ticket is closed — reopen it to reply.", "error")
        else:
            body = (request.form.get("body") or "").strip()
            if not body:
                flash("Comment is required.", "error")
            else:
                db.session.add(TicketComment(ticket_id=t.id,
                                             author_id=current_user.id,
                                             body=body, is_internal=False))
                t.updated_at = datetime.utcnow()
                audit_record("ticket_comment",
                             f"ticket #{t.id} public comment by "
                             f"{current_user.id}", entity="ticket",
                             entity_id=t.id)
                if t.assignee_id and t.assignee_id != current_user.id:
                    notify_user(t.assignee_id, "ticket",
                                f"Ticket #{t.id} updated",
                                f"{current_user.name} replied to ticket "
                                f"#{t.id}.", link=f"/hrms/tickets/{t.id}")
                log_action(current_user.id, "ticket_comment",
                           f"helpdesk ticket #{t.id}")
                db.session.commit()
                flash("Reply added.", "success")
        return redirect(url_for("helpdesk.my_ticket_detail",
                                ticket_id=t.id))
    # Requester view: public comments only — internal notes are staff-only.
    comments = TicketComment.query.filter_by(
        ticket_id=t.id, is_internal=False).order_by(
        TicketComment.created_at).all()
    users = {u.id: u for u in User.query.all()}
    return render_template("my_ticket_detail.html", t=t, comments=comments,
                           users=users, sla_hours=TICKET_SLA_HOURS)


@helpdesk_bp.route("/my-tickets/<int:ticket_id>/close", methods=["POST"])
@login_required
def my_ticket_close(ticket_id):
    t = HelpdeskTicket.query.get_or_404(ticket_id)
    if t.requester_id != current_user.id:
        abort(403)
    if t.status != "resolved":
        flash("Only a resolved ticket can be closed.", "error")
        return redirect(url_for("helpdesk.my_ticket_detail",
                                ticket_id=t.id))
    old = t.status
    t.status = "closed"
    audit_record("ticket_status", f"ticket #{t.id} closed by requester",
                 entity="ticket", entity_id=t.id, field_name="status",
                 old_value=old, new_value="closed")
    log_action(current_user.id, "ticket_closed", f"helpdesk ticket #{t.id}")
    db.session.commit()
    flash("Ticket closed.", "success")
    return redirect(url_for("helpdesk.my_tickets"))


@helpdesk_bp.route("/my-tickets/<int:ticket_id>/reopen", methods=["POST"])
@login_required
def my_ticket_reopen(ticket_id):
    t = HelpdeskTicket.query.get_or_404(ticket_id)
    if t.requester_id != current_user.id:
        abort(403)
    if t.status not in ("resolved", "closed"):
        flash("Only a resolved or closed ticket can be reopened.", "error")
        return redirect(url_for("helpdesk.my_ticket_detail",
                                ticket_id=t.id))
    old = t.status
    t.status = "open"
    t.resolved_at = None
    audit_record("ticket_status", f"ticket #{t.id} reopened by requester",
                 entity="ticket", entity_id=t.id, field_name="status",
                 old_value=old, new_value="open")
    log_action(current_user.id, "ticket_reopened",
               f"helpdesk ticket #{t.id}")
    db.session.commit()
    flash("Ticket reopened.", "success")
    return redirect(url_for("helpdesk.my_ticket_detail",
                            ticket_id=t.id))


@helpdesk_bp.route("/hrms/tickets")
@login_required
@_ticket_staff_required
def hr_tickets():
    status = (request.args.get("status") or "").strip()
    category = (request.args.get("category") or "").strip()
    assignee = (request.args.get("assignee") or "").strip()
    # v3.6: priority filter.
    priority = (request.args.get("priority") or "").strip()
    q = HelpdeskTicket.query
    if status in TICKET_STATUSES:
        q = q.filter_by(status=status)
    if category in [c for c, _ in TICKET_CATEGORIES]:
        q = q.filter_by(category=category)
    if priority in TICKET_PRIORITIES:
        q = q.filter_by(priority=priority)
    if assignee == "mine":
        q = q.filter_by(assignee_id=current_user.id)
    elif assignee == "unassigned":
        q = q.filter(HelpdeskTicket.assignee_id.is_(None))
    tickets = q.order_by(HelpdeskTicket.created_at.desc()).all()
    users = {u.id: u for u in User.query.all()}
    staff_users = User.query.filter(
        User.id.in_(_staff_user_ids())).order_by(User.name).all()
    return render_template("hr_tickets.html", tickets=tickets, users=users,
                           status=status, category=category, assignee=assignee,
                           priority=priority, priorities=TICKET_PRIORITIES,
                           categories=TICKET_CATEGORIES,
                           statuses=TICKET_STATUSES,
                           staff_users=staff_users)


@helpdesk_bp.route("/hrms/tickets/<int:ticket_id>")
@login_required
@_ticket_staff_required
def hr_ticket_detail(ticket_id):
    from app.models import AuditLog
    t = HelpdeskTicket.query.get_or_404(ticket_id)
    comments = TicketComment.query.filter_by(ticket_id=t.id).order_by(
        TicketComment.created_at).all()
    users = {u.id: u for u in User.query.all()}
    staff_users = User.query.filter(
        User.id.in_(_staff_user_ids())).order_by(User.name).all()
    # v3.6: activity timeline for the ticket.
    logs = AuditLog.query.filter_by(entity="ticket",
                                    entity_id=str(t.id))\
        .order_by(AuditLog.created_at.desc()).limit(40).all()
    return render_template("hr_ticket_detail.html", t=t, comments=comments,
                           users=users, staff_users=staff_users,
                           statuses=TICKET_STATUSES, logs=logs)


@helpdesk_bp.route("/hrms/tickets/<int:ticket_id>/status", methods=["POST"])
@login_required
@_ticket_staff_required
def hr_ticket_status(ticket_id):
    t = HelpdeskTicket.query.get_or_404(ticket_id)
    new = (request.form.get("status") or "").strip()
    if new not in TICKET_STATUSES:
        abort(400)
    old = t.status
    if old == new:
        flash("Status unchanged.", "info")
        return redirect(url_for("helpdesk.hr_ticket_detail",
                                ticket_id=t.id))
    t.status = new
    if new == "resolved" and not t.resolved_at:
        t.resolved_at = datetime.utcnow()
    if new in ("open", "in_progress"):
        t.resolved_at = None
    audit_record("ticket_status", f"ticket #{t.id} {old} -> {new}",
                 entity="ticket", entity_id=t.id, field_name="status",
                 old_value=old, new_value=new)
    log_action(current_user.id, "ticket_status",
               f"helpdesk ticket #{t.id} -> {new}")
    notify_user(t.requester_id, "ticket",
                f"Ticket #{t.id} {new.replace('_', ' ')}",
                f"Your ticket #{t.id} ({t.subject}) is now "
                f"{new.replace('_', ' ')}.", link=f"/my-tickets/{t.id}")
    db.session.commit()
    flash(f"Ticket status set to {new.replace('_', ' ')}.", "success")
    return redirect(url_for("helpdesk.hr_ticket_detail", ticket_id=t.id))


@helpdesk_bp.route("/hrms/tickets/<int:ticket_id>/assign", methods=["POST"])
@login_required
@_ticket_staff_required
def hr_ticket_assign(ticket_id):
    t = HelpdeskTicket.query.get_or_404(ticket_id)
    new_id = (request.form.get("assignee_id") or "").strip() or None
    if new_id is not None and new_id not in _staff_user_ids():
        abort(400)
    old_id = t.assignee_id
    if old_id == new_id:
        flash("Assignee unchanged.", "info")
        return redirect(url_for("helpdesk.hr_ticket_detail",
                                ticket_id=t.id))
    t.assignee_id = new_id
    old_name = User.query.get(old_id).name if old_id else "Unassigned"
    new_name = User.query.get(new_id).name if new_id else "Unassigned"
    audit_record("ticket_assign", f"ticket #{t.id}: {old_name} -> {new_name}",
                 entity="ticket", entity_id=t.id, field_name="assignee_id",
                 old_value=old_id, new_value=new_id)
    log_action(current_user.id, "ticket_assigned",
               f"helpdesk ticket #{t.id} -> {new_id}")
    if new_id:
        notify_user(new_id, "ticket", f"Ticket #{t.id} assigned to you",
                    f"{t.subject} [{t.category} / {t.priority}].",
                    link=f"/hrms/tickets/{t.id}")
    notify_user(t.requester_id, "ticket", f"Ticket #{t.id} assigned",
                f"Your ticket #{t.id} is now handled by {new_name}.",
                link=f"/my-tickets/{t.id}")
    db.session.commit()
    flash(f"Ticket assigned to {new_name}.", "success")
    return redirect(url_for("helpdesk.hr_ticket_detail", ticket_id=t.id))


@helpdesk_bp.route("/hrms/tickets/<int:ticket_id>/comment", methods=["POST"])
@login_required
@_ticket_staff_required
def hr_ticket_comment(ticket_id):
    t = HelpdeskTicket.query.get_or_404(ticket_id)
    body = (request.form.get("body") or "").strip()
    is_internal = bool(request.form.get("is_internal"))
    if not body:
        flash("Comment is required.", "error")
        return redirect(url_for("helpdesk.hr_ticket_detail",
                                ticket_id=t.id))
    db.session.add(TicketComment(ticket_id=t.id, author_id=current_user.id,
                                 body=body, is_internal=is_internal))
    t.updated_at = datetime.utcnow()
    audit_record("ticket_comment",
                 f"ticket #{t.id} "
                 f"{'internal note' if is_internal else 'public comment'} "
                 f"by {current_user.id}", entity="ticket", entity_id=t.id)
    # Internal notes never notify (and never render for) the requester.
    if not is_internal and t.requester_id != current_user.id:
        notify_user(t.requester_id, "ticket",
                    f"Ticket #{t.id} updated",
                    f"{current_user.name} commented on your ticket "
                    f"#{t.id}.", link=f"/my-tickets/{t.id}")
    log_action(current_user.id, "ticket_comment",
               f"helpdesk ticket #{t.id}")
    db.session.commit()
    flash("Internal note saved." if is_internal else "Comment posted.",
          "success")
    return redirect(url_for("helpdesk.hr_ticket_detail", ticket_id=t.id))


# ==================== PART B: shift swaps ====================

def _upcoming_assignments(user_id):
    """Future roster assignments of one user (for the swap picker)."""
    return RosterAssignment.query.filter(
        RosterAssignment.user_id == user_id,
        RosterAssignment.date >= _gst_today()).order_by(
        RosterAssignment.date).all()


def _party_rest_gap(user_id, swap_date, new_pattern):
    """Rest-gap warnings for one party (display only, never blocks)."""
    flags = []
    if not new_pattern:
        return flags
    uname = (User.query.get(user_id).name
             if User.query.get(user_id) else user_id)
    prev = RosterAssignment.query.filter_by(
        user_id=user_id, date=swap_date - timedelta(days=1)).first()
    if prev and prev.shift and prev.shift.crosses_midnight \
            and (new_pattern.start_time or "") < "12:00":
        flags.append(
            f"{uname}: NIGHT shift on {prev.date} followed by "
            f"{new_pattern.code} starting {new_pattern.start_time}")
    if new_pattern.crosses_midnight:
        nxt = RosterAssignment.query.filter_by(
            user_id=user_id, date=swap_date + timedelta(days=1)).first()
        if nxt and nxt.shift and (nxt.shift.start_time or "") < "08:00":
            flags.append(
                f"{uname}: {new_pattern.code} runs past midnight, next day "
                f"{nxt.shift.code} starts at {nxt.shift.start_time}")
    return flags


def rest_gap_flags(swap):
    """Rest-gap warnings for the exchange (display only). After the swap,
    requester's row gets the counterpart's pattern and vice versa."""
    flags = []
    a = RosterAssignment.query.get(swap.my_assignment_id)
    b = RosterAssignment.query.get(swap.their_assignment_id)
    if not a or not b:
        return flags
    new_a = ShiftPattern.query.get(swap.their_pattern_id)
    new_b = ShiftPattern.query.get(swap.my_pattern_id)
    flags += _party_rest_gap(a.user_id, a.date, new_a)
    flags += _party_rest_gap(b.user_id, b.date, new_b)
    return flags


def validate_swap(swap):
    """Staleness + integrity check. Returns (ok, reason)."""
    if swap.status not in ("pending_counterpart", "pending_approval"):
        return False, "Swap is no longer open."
    a = RosterAssignment.query.get(swap.my_assignment_id)
    b = RosterAssignment.query.get(swap.their_assignment_id)
    if not a or not b:
        return False, "Roster changed since request"
    if a.user_id != swap.requester_id \
            or b.user_id != swap.counterpart_user_id:
        return False, "Roster changed since request"
    if swap.my_pattern_id and a.shift_id != swap.my_pattern_id:
        return False, "Roster changed since request"
    if swap.their_pattern_id and b.shift_id != swap.their_pattern_id:
        return False, "Roster changed since request"
    return True, ""


def execute_swap(swap):
    """Exchange the two pattern ids transactionally (caller commits)."""
    ok, reason = validate_swap(swap)
    if not ok:
        return False, reason
    a = RosterAssignment.query.get(swap.my_assignment_id)
    b = RosterAssignment.query.get(swap.their_assignment_id)
    a.shift_id, b.shift_id = b.shift_id, a.shift_id
    audit_record("shift_swap_executed",
                 f"swap #{swap.id}: {a.user_id} {a.date} <-> {b.user_id} "
                 f"{b.date} patterns exchanged",
                 entity="shift_swap", entity_id=swap.id)
    return True, ""


def _cancel_instance(instance, comment):
    """System-close an engine instance as rejected (requester cancelled)."""
    db.session.add(ApprovalAction(
        instance_id=instance.id, step_no=instance.current_step,
        actor_id=None, action="cancelled", comment=comment))
    old = instance.status
    instance.status = "rejected"
    instance.decided_at = datetime.utcnow()
    instance.reject_reason = comment
    audit_record("approval_cancelled",
                 f"{instance.process_key} #{instance.ref_id}: {comment}",
                 entity="approval", entity_id=instance.id,
                 field_name="status", old_value=old,
                 new_value="rejected")


@helpdesk_bp.route("/my-swaps", methods=["GET", "POST"])
@login_required
def my_swaps():
    today = _gst_today()
    if request.method == "POST":
        a_id = request.form.get("my_assignment_id")
        c_uid = (request.form.get("counterpart_user_id") or "").strip()
        b_id = request.form.get("their_assignment_id")
        reason = (request.form.get("reason") or "").strip()[:500]
        errors = []
        try:
            a = RosterAssignment.query.get(int(a_id)) if a_id else None
            b = RosterAssignment.query.get(int(b_id)) if b_id else None
        except (TypeError, ValueError):
            a = b = None
        c = User.query.get(c_uid) if c_uid else None
        if not a or a.user_id != current_user.id:
            errors.append("Pick one of your own roster assignments.")
        if not c or not c.is_active or c.id == current_user.id:
            errors.append("Pick a valid counterpart employee.")
        if not b or not c or b.user_id != c.id:
            errors.append("Pick one of the counterpart's roster assignments.")
        if a and a.date < today:
            errors.append("Your assignment is in the past.")
        if b and b.date < today:
            errors.append("The counterpart's assignment is in the past.")
        if a and b and a.shift_id == b.shift_id:
            errors.append("Both assignments already have the same shift — "
                          "nothing to swap.")
        if a and b and not errors:
            from app.models import ShiftSwapRequest as SSR
            clash = SSR.query.filter(
                SSR.status.in_(("pending_counterpart",
                                "pending_approval")),
                ((SSR.my_assignment_id.in_((a.id, b.id)))
                 | (SSR.their_assignment_id.in_((a.id, b.id))))).first()
            if clash:
                errors.append("One of these assignments is already in an "
                              "open swap request.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            swap = ShiftSwapRequest(
                requester_id=current_user.id,
                my_assignment_id=a.id, counterpart_user_id=c.id,
                their_assignment_id=b.id,
                my_pattern_id=a.shift_id, their_pattern_id=b.shift_id,
                status="pending_counterpart", reason=reason or None)
            db.session.add(swap)
            db.session.flush()
            audit_record("shift_swap_created",
                         f"swap #{swap.id}: {a.user_id} {a.date} "
                         f"{a.shift.code} <-> {c.id} {b.date} "
                         f"{b.shift.code}", entity="shift_swap",
                         entity_id=swap.id)
            notify_user(c.id, "shift_swap", "Shift swap request",
                        f"{current_user.name} wants to swap your "
                        f"{b.shift.code} on {b.date} for their "
                        f"{a.shift.code} on {a.date}.", link="/my-swaps",
                        category="important")
            log_action(current_user.id, "shift_swap_created",
                       f"swap #{swap.id}")
            db.session.commit()
            flash("Swap request sent. It starts the approval chain once "
                  "the counterpart accepts.", "success")
            return redirect(url_for("helpdesk.my_swaps"))
    mine = _upcoming_assignments(current_user.id)
    theirs = ShiftSwapRequest.query.filter_by(
        requester_id=current_user.id).order_by(
        ShiftSwapRequest.created_at.desc()).all()
    incoming = ShiftSwapRequest.query.filter_by(
        counterpart_user_id=current_user.id,
        status="pending_counterpart").order_by(
        ShiftSwapRequest.created_at.desc()).all()
    counterparts = User.query.filter(
        User.is_active == True,  # noqa: E712
        User.id != current_user.id).order_by(User.name).all()
    return render_template("my_swaps.html", mine=mine, theirs=theirs,
                           incoming=incoming, counterparts=counterparts,
                           today=today)


@helpdesk_bp.route("/my-swaps/<int:swap_id>")
@login_required
def swap_detail(swap_id):
    swap = ShiftSwapRequest.query.get_or_404(swap_id)
    if current_user.id not in (swap.requester_id,
                               swap.counterpart_user_id):
        abort(403)
    a = RosterAssignment.query.get(swap.my_assignment_id)
    b = RosterAssignment.query.get(swap.their_assignment_id)
    users = {u.id: u for u in User.query.all()}
    pats = {p.id: p for p in ShiftPattern.query.all()}
    return render_template("swap_detail.html", swap=swap, a=a, b=b,
                           users=users, pats=pats,
                           flags=rest_gap_flags(swap))


@helpdesk_bp.route("/my-swaps/<int:swap_id>/consent", methods=["POST"])
@login_required
def swap_consent(swap_id):
    from app.approvals import start_instance
    swap = ShiftSwapRequest.query.get_or_404(swap_id)
    if current_user.id != swap.counterpart_user_id:
        abort(403)
    if swap.status != "pending_counterpart":
        flash("This request is no longer awaiting your consent.", "error")
        return redirect(url_for("helpdesk.swap_detail", swap_id=swap.id))
    action = request.form.get("action")
    reason = (request.form.get("reason") or "").strip()[:500]
    if action == "reject":
        swap.status = "rejected"
        swap.reason = reason or "Declined by counterpart."
        swap.counterpart_decided_by = current_user.id
        swap.counterpart_decided_at = datetime.utcnow()
        audit_record("shift_swap_rejected",
                     f"swap #{swap.id} declined by {current_user.id}",
                     entity="shift_swap", entity_id=swap.id,
                     field_name="status",
                     old_value="pending_counterpart",
                     new_value="rejected")
        notify_user(swap.requester_id, "shift_swap", "Shift swap declined",
                    f"{current_user.name} declined your swap request "
                    f"#{swap.id}.", link="/my-swaps", category="important")
        log_action(current_user.id, "shift_swap_declined",
                   f"swap #{swap.id}")
        db.session.commit()
        flash("Swap request declined.", "success")
        return redirect(url_for("helpdesk.my_swaps"))
    if action != "accept":
        abort(400)
    swap.status = "pending_approval"
    swap.counterpart_decided_by = current_user.id
    swap.counterpart_decided_at = datetime.utcnow()
    audit_record("shift_swap_accepted",
                 f"swap #{swap.id} accepted by {current_user.id}; chain "
                 f"started", entity="shift_swap", entity_id=swap.id,
                 field_name="status", old_value="pending_counterpart",
                 new_value="pending_approval")
    start_instance("shift_swap", swap.id, swap.requester_id,
                   swap.requester_id)
    notify_user(swap.requester_id, "shift_swap", "Shift swap accepted",
                f"{current_user.name} accepted swap #{swap.id}; it is now "
                f"with the approvers.", link="/my-swaps",
                category="important")
    log_action(current_user.id, "shift_swap_accepted", f"swap #{swap.id}")
    db.session.commit()
    flash("Swap accepted — sent for approval.", "success")
    return redirect(url_for("helpdesk.my_swaps"))


@helpdesk_bp.route("/my-swaps/<int:swap_id>/cancel", methods=["POST"])
@login_required
def swap_cancel(swap_id):
    from app.approvals import get_instance
    swap = ShiftSwapRequest.query.get_or_404(swap_id)
    if current_user.id != swap.requester_id:
        abort(403)
    if swap.status not in ("pending_counterpart", "pending_approval"):
        flash("This request can no longer be cancelled.", "error")
        return redirect(url_for("helpdesk.swap_detail", swap_id=swap.id))
    reason = (request.form.get("reason") or "").strip()[:500]
    old = swap.status
    swap.status = "cancelled"
    swap.reason = reason or "Cancelled by requester."
    inst = get_instance("shift_swap", swap.id)
    if inst is not None and inst.status == "pending":
        _cancel_instance(inst, f"Swap #{swap.id} cancelled by requester.")
    audit_record("shift_swap_cancelled",
                 f"swap #{swap.id} cancelled by {current_user.id}",
                 entity="shift_swap", entity_id=swap.id,
                 field_name="status", old_value=old, new_value="cancelled")
    notify_user(swap.counterpart_user_id, "shift_swap",
                "Shift swap cancelled",
                f"{current_user.name} cancelled swap request #{swap.id}.",
                link="/my-swaps", category="important")
    log_action(current_user.id, "shift_swap_cancelled", f"swap #{swap.id}")
    db.session.commit()
    flash("Swap request cancelled.", "success")
    return redirect(url_for("helpdesk.my_swaps"))


@helpdesk_bp.route("/approvals/swap/<int:swap_id>", methods=["POST"])
@login_required
def decide_swap(swap_id):
    """Approve/reject one step of a shift swap through the engine."""
    from app.approvals import (get_or_start_instance, act, self_blocked)
    swap = ShiftSwapRequest.query.get_or_404(swap_id)
    if swap.status != "pending_approval":
        flash("Swap request already decided.", "error")
        return redirect(url_for("views.approvals"))
    instance = get_or_start_instance("shift_swap", swap.id,
                                     swap.requester_id, swap.requester_id)
    action = request.form.get("action")
    comment = (request.form.get("comment") or "").strip()[:500]
    if action not in ("approve", "reject"):
        abort(400)
    # Staleness check BEFORE the engine advances: if the roster changed
    # since the request, the final approval must become a rejection so the
    # engine instance and the swap stay consistent.
    stale = False
    if action == "approve" and instance.current_step == \
            instance.total_steps and not instance.exhausted:
        ok, _ = validate_swap(swap)
        if not ok:
            stale = True
    try:
        if stale:
            outcome = act(instance, current_user, False,
                          "Roster changed since request — swap aborted")
        else:
            outcome = act(instance, current_user, action == "approve",
                          comment)
    except PermissionError as e:
        if self_blocked(instance, current_user) and \
                "own request" in str(e):
            flash("You cannot approve your own request.", "error")
            return redirect(url_for("views.approvals"))
        abort(403)
    users = {u.id: u for u in User.query.all()}
    requester = users.get(swap.requester_id)
    counterpart = users.get(swap.counterpart_user_id)
    if outcome == "rejected":
        swap.status = "rejected"
        swap.reason = ("Roster changed since request"
                       if stale else (comment or instance.reject_reason))
        for uid in {swap.requester_id, swap.counterpart_user_id}:
            if uid != current_user.id:
                notify_user(uid, "shift_swap", "Shift swap rejected",
                            f"Swap #{swap.id} was rejected"
                            + (f": {swap.reason}" if swap.reason else "."),
                            link="/my-swaps", category="important")
        audit_record("shift_swap_rejected",
                     f"swap #{swap.id} rejected (stale={stale})",
                     entity="shift_swap", entity_id=swap.id,
                     field_name="status", old_value="pending_approval",
                     new_value="rejected")
        flash("Shift swap rejected.", "success")
    elif outcome == "final_approved":
        ok, reason = execute_swap(swap)
        if ok:
            swap.status = "approved"
            audit_record("shift_swap_approved",
                         f"swap #{swap.id} fully approved and executed",
                         entity="shift_swap", entity_id=swap.id,
                         field_name="status",
                         old_value="pending_approval",
                         new_value="approved")
            for uid in {swap.requester_id, swap.counterpart_user_id}:
                notify_user(uid, "shift_swap", "Shift swap approved",
                            f"Swap #{swap.id} approved: "
                            f"{requester.name if requester else ''} and "
                            f"{counterpart.name if counterpart else ''} "
                            f"shifts exchanged.", link="/my-swaps",
                            category="important")
            flash("Shift swap approved — shifts exchanged.", "success")
        else:
            # Defensive: pre-check above should have caught staleness.
            swap.status = "rejected"
            swap.reason = reason
            audit_record("shift_swap_rejected",
                         f"swap #{swap.id} aborted at execution: {reason}",
                         entity="shift_swap", entity_id=swap.id,
                         field_name="status", old_value="pending_approval",
                         new_value="rejected")
            flash("Swap aborted: roster changed since the request.", "error")
    else:
        flash("Approved at this step; forwarded to the next approver.",
              "success")
    log_action(current_user.id, "shift_swap_decided", f"swap #{swap.id}")
    db.session.commit()
    return redirect(url_for("views.approvals"))


@helpdesk_bp.route("/approvals/swap/<int:swap_id>/counterpart", methods=["POST"])
@login_required
def counterpart_remind(swap_id):
    """Nudge the counterpart (reminder notification) — requester only."""
    swap = ShiftSwapRequest.query.get_or_404(swap_id)
    if current_user.id != swap.requester_id:
        abort(403)
    if swap.status != "pending_counterpart":
        abort(400)
    notify_user(swap.counterpart_user_id, "shift_swap",
                "Shift swap reminder",
                f"{current_user.name} is waiting for your consent on swap "
                f"request #{swap.id}.", link="/my-swaps",
                category="important")
    db.session.commit()
    flash("Reminder sent.", "success")
    return redirect(url_for("helpdesk.my_swaps"))
