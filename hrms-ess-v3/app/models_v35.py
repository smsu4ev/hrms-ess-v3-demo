"""V3.5 model additions: helpdesk tickets + shift swaps.

These are imported into app.models at the bottom so the single-table
migration pattern keeps working (new tables are created on demand).
"""

from datetime import datetime, timedelta

from app import db


TICKET_CATEGORIES = [
    ("IT", "IT"),
    ("HR", "HR"),
    ("Admin & Facility", "Admin & Facility"),
    ("Payroll", "Payroll"),
    ("Other", "Other"),
]

TICKET_PRIORITIES = ["Low", "Normal", "High", "Urgent"]

# SLA targets, hours — display only, used for the "SLA overdue" flag.
TICKET_SLA_HOURS = {"Urgent": 4, "High": 8, "Normal": 24, "Low": 72}

TICKET_STATUSES = ["open", "in_progress", "resolved", "closed"]

SWAP_STATUSES = ["pending_counterpart", "pending_approval", "approved",
                 "rejected", "cancelled"]

_OPEN_SWAP_STATUSES = ("pending_counterpart", "pending_approval")


class HelpdeskTicket(db.Model):
    """Internal support ticket — distinct from formal workplace Grievances.

    (v3.5)
    """
    __tablename__ = "helpdesk_tickets"
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                             nullable=False, index=True)
    category = db.Column(db.String(40), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), default="Normal", nullable=False)
    status = db.Column(db.String(20), default="open", nullable=False)
    # open | in_progress | resolved | closed
    assignee_id = db.Column(db.String(20), db.ForeignKey("users.id"))
    resolved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    @property
    def due_at(self):
        return self.created_at + timedelta(
            hours=TICKET_SLA_HOURS.get(self.priority, 24))

    @property
    def sla_overdue(self):
        return (self.status in ("open", "in_progress")
                and datetime.utcnow() > self.due_at)


class TicketComment(db.Model):
    """Comment thread on a helpdesk ticket. is_internal notes are staff-only
    and must never be rendered to the requester. (v3.5)"""
    __tablename__ = "ticket_comments"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer,
                          db.ForeignKey("helpdesk_tickets.id"),
                          nullable=False, index=True)
    author_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                          nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_internal = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)


class ShiftSwapRequest(db.Model):
    """Exchange of shift patterns between two RosterAssignment rows.

    Each assignment keeps its user and date; only the pattern ids swap.
    (v3.5)
    """
    __tablename__ = "shift_swap_requests"
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                             nullable=False, index=True)
    my_assignment_id = db.Column(db.Integer,
                                 db.ForeignKey("roster_assignments.id"),
                                 nullable=False)
    counterpart_user_id = db.Column(db.String(20),
                                    db.ForeignKey("users.id"),
                                    nullable=False, index=True)
    their_assignment_id = db.Column(db.Integer,
                                    db.ForeignKey("roster_assignments.id"),
                                    nullable=False)
    # Pattern snapshots taken at consent time (detect roster changes).
    my_pattern_id = db.Column(db.Integer)
    their_pattern_id = db.Column(db.Integer)
    status = db.Column(db.String(20), default="pending_counterpart",
                       nullable=False)
    # pending_counterpart | pending_approval | approved | rejected | cancelled
    reason = db.Column(db.String(500))  # reject/cancel/stale reason
    counterpart_decided_by = db.Column(db.String(20),
                                       db.ForeignKey("users.id"))
    counterpart_decided_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    @property
    def is_open(self):
        return self.status in _OPEN_SWAP_STATUSES
