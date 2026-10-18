"""Database models for the HRMS + ESS portal (v2)."""
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


class User(UserMixin, db.Model):
    __tablename__ = "users"
    # Username of the ESS is the Employee ID
    id = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(80))
    designation = db.Column(db.String(80))
    reporting_manager = db.Column(db.String(20), db.ForeignKey("users.id"))
    joining_date = db.Column(db.Date)
    employment_status = db.Column(db.String(20), default="active", nullable=False)
    # active | probation | resigned | terminated | retired
    nationality = db.Column(db.String(60))
    visa_expiry_date = db.Column(db.Date)
    passport_expiry_date = db.Column(db.Date)
    emirates_id_expiry_date = db.Column(db.Date)  # v2.1 UAE compliance
    emirates_id_no = db.Column(db.String(40))  # v3: Emirates ID number (not the expiry date)
    email = db.Column(db.String(120))
    mobile = db.Column(db.String(30))
    iban = db.Column(db.String(60))      # v2.1 WPS payroll export
    bank_name = db.Column(db.String(80))  # v2.1 WPS payroll export
    # v3: visa company (WPS split), photo, salary visibility, WhatsApp phone
    visa_company_id = db.Column(db.Integer, db.ForeignKey("companies.id"))
    photo_path = db.Column(db.String(120))
    can_view_salary = db.Column(db.Boolean, default=False, nullable=False)
    phone = db.Column(db.String(30))
    passport_no = db.Column(db.String(40))
    exit_date = db.Column(db.Date)
    separation_date = db.Column(db.Date)      # v3.1: separation register
    separation_type = db.Column(db.String(30))
    basic_salary = db.Column(db.Float, default=0.0)
    monthly_salary = db.Column(db.Float, default=0.0)
    password_hash = db.Column(db.String(255), nullable=False)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    role = db.Column(db.String(20), default="employee", nullable=False)  # employee|manager|admin
    failed_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_manager(self):
        return self.role in ("manager", "admin")

    @property
    def is_admin(self):
        return self.role == "admin"


class LeaveBalance(db.Model):
    __tablename__ = "leave_balances"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    leave_type = db.Column(db.String(20), nullable=False)
    entitled = db.Column(db.Float, default=0.0, nullable=False)
    used = db.Column(db.Float, default=0.0, nullable=False)
    __table_args__ = (db.UniqueConstraint("user_id", "leave_type"),)


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    leave_type = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    days = db.Column(db.Float, nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default="pending", nullable=False)  # pending|approved|rejected|cancelled
    approver_id = db.Column(db.String(20), db.ForeignKey("users.id"))
    approver_comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    decided_at = db.Column(db.DateTime)


class AttendanceRecord(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    check_in = db.Column(db.String(5))   # HH:MM
    check_out = db.Column(db.String(5))  # HH:MM
    status = db.Column(db.String(20), default="present")  # present|absent|late|leave|holiday|weekoff
    __table_args__ = (db.UniqueConstraint("user_id", "date"),)


class RegularisationRequest(db.Model):
    """Employee request to correct attendance in/out times (ESS + manager approval)."""
    __tablename__ = "regularisation_requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    corrected_in = db.Column(db.String(5))
    corrected_out = db.Column(db.String(5))
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)  # pending|approved|rejected
    approver_id = db.Column(db.String(20), db.ForeignKey("users.id"))
    approver_comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    decided_at = db.Column(db.DateTime)


class Grievance(db.Model):
    __tablename__ = "grievances"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    category = db.Column(db.String(60), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    is_anonymous = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(20), default="submitted", nullable=False)  # submitted|under_review|in_progress|resolved|closed
    resolution = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Evaluation(db.Model):
    __tablename__ = "evaluations"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    period = db.Column(db.String(40), nullable=False)  # e.g. "2026 H2"
    evaluator_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False)
    scores_json = db.Column(db.Text, nullable=False)  # JSON: [{criterion, rating, comment}, ...]
    overall = db.Column(db.Float)
    remarks = db.Column(db.Text)
    acknowledged = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Candidate(db.Model):
    """Recruitment tracker: a candidate in the hiring pipeline."""
    __tablename__ = "candidates"
    STAGES = ["Applied", "Screening", "Interview", "Offer", "Joined", "Rejected"]
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    position = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    source = db.Column(db.String(80))
    stage = db.Column(db.String(20), default="Applied", nullable=False)
    notes = db.Column(db.Text)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class OnboardingCase(db.Model):
    """Onboarding checklist case for a new joiner."""
    __tablename__ = "onboarding_cases"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    joining_date = db.Column(db.Date)
    employee_id = db.Column(db.String(20))  # linked employee once created
    notes = db.Column(db.Text)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def progress(self):
        items = list(self.tasks)
        if not items:
            return 0
        done = sum(1 for t in items if t.is_done)
        return round(done * 100 / len(items))


class OnboardingTask(db.Model):
    __tablename__ = "onboarding_tasks"
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("onboarding_cases.id"), nullable=False, index=True)
    label = db.Column(db.String(200), nullable=False)
    is_done = db.Column(db.Boolean, default=False, nullable=False)
    done_at = db.Column(db.DateTime)
    case = db.relationship("OnboardingCase", backref=db.backref("tasks", lazy="dynamic",
                                                                order_by="OnboardingTask.id"))


ONBOARDING_CHECKLIST_ITEMS = [
    "Offer letter issued",
    "Employment contract signed",
    "Emirates ID copy collected",
    "Visa / work permit processed",
    "Bank account details collected",
    "IT / ERP access created",
    "Workstation / tools assigned",
    "HR induction completed",
]


class AuditLog(db.Model):
    """Advanced audit trail: every recorded action, with per-field old/new values."""
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.String(20))
    action = db.Column(db.String(80), nullable=False)
    detail = db.Column(db.Text)
    # v2 advanced fields
    entity = db.Column(db.String(40), index=True)       # e.g. "employee", "candidate", "leave"
    entity_id = db.Column(db.String(40), index=True)   # e.g. employee ID, candidate id
    field_name = db.Column(db.String(60))              # changed field, for per-field entries
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    ip = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


# ===================== HRMS+ESS V3 models =====================

class Company(db.Model):
    """Visa company: the legal entity sponsoring the employee's visa.

    One payroll run covers everyone at the same time, but WPS files are
    generated split by visa company.
    """
    __tablename__ = "companies"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)  # e.g. ALA
    name = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Company {self.code}>"


# New fields on User (v3). The app factory adds them idempotently to
# existing databases; see app/__init__.py migrations.
#   visa_company_id -> Company.id
#   photo_path      -> stored photo filename (under instance/uploads/photos)
#   can_view_salary -> HR Manager assigns salary visibility to this user
#   phone           -> WhatsApp/mobile contact number for ESS notifications


class SalaryComponent(db.Model):
    """Salary component master. Admin can add/delete custom components."""
    __tablename__ = "salary_components"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)  # e.g. BASIC
    name = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(20), default="earning", nullable=False)  # earning|deduction
    is_fixed = db.Column(db.Boolean, default=True, nullable=False)  # fixed vs variable
    prorate = db.Column(db.Boolean, default=True, nullable=False)   # prorate on payable-days basis
    wps_bucket = db.Column(db.String(20), default="variable", nullable=False)  # basic|variable
    is_statutory = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class SalaryAssignment(db.Model):
    """Per-employee component amount, effective-dated.

    Current value = latest row with effective_from <= as-of date.
    """
    __tablename__ = "salary_assignments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    component_id = db.Column(db.Integer, db.ForeignKey("salary_components.id"),
                             nullable=False, index=True)
    amount = db.Column(db.Float, default=0.0, nullable=False)
    effective_from = db.Column(db.Date, nullable=False, index=True)
    note = db.Column(db.String(200))
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    component = db.relationship("SalaryComponent")


class OTRateRule(db.Model):
    """Overtime rate multipliers settable at company / group(dept) / individual level.

    Precedence when resolving: individual > group > company > system default
    (normal 1.25, night 1.50, rest-day 1.50). Hourly rate = monthly basic / 30 / 8.
    """
    __tablename__ = "ot_rate_rules"
    id = db.Column(db.Integer, primary_key=True)
    level = db.Column(db.String(20), nullable=False)  # company|group|individual
    ref_id = db.Column(db.String(60), nullable=False)  # company code | department | user id
    normal_mult = db.Column(db.Float, default=1.25, nullable=False)
    night_mult = db.Column(db.Float, default=1.50, nullable=False)
    restday_mult = db.Column(db.Float, default=1.50, nullable=False)
    updated_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("level", "ref_id"),)


class Loan(db.Model):
    """Loans and salary advances with auto-recovery schedules."""
    __tablename__ = "loans"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    loan_type = db.Column(db.String(20), default="loan", nullable=False)  # loan|advance
    principal = db.Column(db.Float, nullable=False)
    installment = db.Column(db.Float, nullable=False)
    start_period = db.Column(db.String(7), nullable=False)  # YYYY-MM
    balance = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="active", nullable=False)  # active|paused|closed
    note = db.Column(db.String(200))
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PayrollInput(db.Model):
    """Per-period additions / deductions / arrears entered by HR."""
    __tablename__ = "payroll_inputs"
    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(7), nullable=False, index=True)  # YYYY-MM
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    component_id = db.Column(db.Integer, db.ForeignKey("salary_components.id"), nullable=True)
    label = db.Column(db.String(80), nullable=False)  # free label; "LOP" with days = manual LOP
    amount = db.Column(db.Float, default=0.0, nullable=False)
    days = db.Column(db.Float, nullable=True)  # for manual LOP day entries
    kind = db.Column(db.String(20), default="addition", nullable=False)  # addition|deduction
    is_arrears = db.Column(db.Boolean, default=False, nullable=False)
    original_period = db.Column(db.String(7))  # original period for arrears
    note = db.Column(db.String(200))
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    component = db.relationship("SalaryComponent")


class PayrollOTHours(db.Model):
    """Overtime hours entered per payroll run (resolved via OTRateRule)."""
    __tablename__ = "payroll_ot_hours"
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("payroll_runs.id"), nullable=False, index=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    category = db.Column(db.String(20), nullable=False, default="normal")  # normal|night|restday
    hours = db.Column(db.Float, default=0.0, nullable=False)


class PayrollRun(db.Model):
    """One payroll run for everyone at the same time (single pay group).

    Lifecycle: draft -> in_review -> locked -> approved.
    """
    __tablename__ = "payroll_runs"
    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(7), nullable=False, unique=True, index=True)  # YYYY-MM
    status = db.Column(db.String(20), default="draft", nullable=False)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    approved_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    locked_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)


class PayrollLine(db.Model):
    """Frozen per-employee result of a payroll run."""
    __tablename__ = "payroll_lines"
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("payroll_runs.id"), nullable=False, index=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    payable_days = db.Column(db.Float, default=0.0, nullable=False)
    lop_days = db.Column(db.Float, default=0.0, nullable=False)
    basic_earned = db.Column(db.Float, default=0.0, nullable=False)
    earnings_json = db.Column(db.Text, nullable=False, default="[]")
    deductions_json = db.Column(db.Text, nullable=False, default="[]")
    ot_json = db.Column(db.Text, nullable=False, default="[]")
    gross = db.Column(db.Float, default=0.0, nullable=False)
    total_deductions = db.Column(db.Float, default=0.0, nullable=False)
    net = db.Column(db.Float, default=0.0, nullable=False)

    run = db.relationship("PayrollRun",
                          backref=db.backref("lines", lazy="dynamic",
                                            cascade="all, delete-orphan"))


class PayrollLoanRecovery(db.Model):
    """Idempotent record of loan/advance recovery applied for a payroll run.

    apply_loan_recovery() writes one row per (run, loan) and skips loans that
    already have a row for that run, so a lock -> unlock -> lock cycle can
    never double-recover. run_unlock reverses the run's recoveries by
    restoring Loan.balance and marking the rows reversed (kept for audit).
    """
    __tablename__ = "payroll_loan_recoveries"
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("payroll_runs.id"),
                       nullable=False, index=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"),
                        nullable=False, index=True)
    amount = db.Column(db.Float, default=0.0, nullable=False)
    reversed = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # No UNIQUE(run_id, loan_id): a lock -> unlock -> lock cycle legitimately
    # writes a second row for the same pair (the first marked reversed).
    # Idempotency is enforced in code by skipping rows with reversed=False.
    __table_args__ = (db.Index("ix_loan_recovery_run_loan", "run_id", "loan_id"),)


class EmployeeDocument(db.Model):
    """Employee vital-document vault with verification workflow."""
    __tablename__ = "employee_documents"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    category = db.Column(db.String(60), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    stored_name = db.Column(db.String(120), nullable=False)  # random filename on disk
    original_name = db.Column(db.String(200))
    mime = db.Column(db.String(80))
    issue_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date, index=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    # pending | verified | rejected ; superseded rows keep history
    superseded = db.Column(db.Boolean, default=False, nullable=False)
    uploaded_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    verified_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    verify_note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class LetterTemplate(db.Model):
    """Letter templates with {{placeholders}} for mail merge."""
    __tablename__ = "letter_templates"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    updated_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class GeneratedLetter(db.Model):
    """A rendered letter snapshot for one employee."""
    __tablename__ = "generated_letters"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey("letter_templates.id"), nullable=False)
    rendered_text = db.Column(db.Text, nullable=False)
    # Set at generation: does the letter carry salary figures? Used to gate
    # the view route and the letters list for users without salary visibility.
    has_salary_data = db.Column(db.Boolean, default=False, nullable=False)
    generated_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    template = db.relationship("LetterTemplate")


class NotificationOutbox(db.Model):
    """Integration-ready notification queue (WhatsApp channel planned).

    Messages are QUEUED here; live sending activates when the WhatsApp
    Business connection is configured (WHATSAPP_ENABLED in config).
    """
    __tablename__ = "notification_outbox"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    channel = db.Column(db.String(20), default="whatsapp", nullable=False)
    event = db.Column(db.String(80), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="queued", nullable=False)  # queued|sent|failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ===================== HRMS+ESS V3.1 models =====================

class SeparationRecord(db.Model):
    """Separation register (ex-employees): resignation, termination, etc."""
    __tablename__ = "separation_records"
    id = db.Column(db.Integer, primary_key=True)
    employee_code = db.Column(db.String(20), nullable=False, index=True)
    employee_name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(80))
    designation = db.Column(db.String(80))
    joining_date = db.Column(db.Date)
    separation_date = db.Column(db.Date, index=True)
    separation_type = db.Column(db.String(30), nullable=False, default="Resignation")
    # Resignation | Termination | Contract End | Retirement | Absconding | Other
    reason = db.Column(db.Text)
    notice_days = db.Column(db.Integer)
    final_settlement_status = db.Column(db.String(20), default="Pending")
    # Pending | In Process | Settled
    settlement_amount = db.Column(db.Float, default=0.0)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def tenure_years(self):
        if self.joining_date and self.separation_date:
            return round((self.separation_date - self.joining_date).days / 365.25, 1)
        return None

    @staticmethod
    def voluntary_types():
        return {"Resignation", "Retirement"}

    @staticmethod
    def involuntary_types():
        return {"Termination", "Absconding"}


class ReportTemplate(db.Model):
    """Saved report filter sets per user."""
    __tablename__ = "report_templates"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    report_key = db.Column(db.String(40), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    params_json = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ----- Retail-fixture specialisation registers (V3.1) -----

class Certification(db.Model):
    """Skills & certification register (HSE, trade licences, driving, ...)."""
    __tablename__ = "certifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    issuer = db.Column(db.String(120))
    issue_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date, index=True)
    notes = db.Column(db.String(300))
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Project(db.Model):
    """Retail-fixture project / site (store fit-out, warehouse racking, ...)."""
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    client = db.Column(db.String(160))
    location = db.Column(db.String(160))
    status = db.Column(db.String(20), default="active", nullable=False)  # active|completed
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Deployment(db.Model):
    """Employee deployed to a project/site with a site allowance."""
    __tablename__ = "deployments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    from_date = db.Column(db.Date)
    to_date = db.Column(db.Date)
    site_allowance = db.Column(db.Float, default=0.0, nullable=False)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    project = db.relationship("Project", backref=db.backref("deployments", lazy="dynamic"))


class FacilityAssignment(db.Model):
    """Accommodation & transport assignments for factory staff."""
    __tablename__ = "facility_assignments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, default="accommodation")  # accommodation|transport
    location_or_route = db.Column(db.String(160))
    monthly_cost = db.Column(db.Float, default=0.0, nullable=False)
    from_date = db.Column(db.Date)
    to_date = db.Column(db.Date)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PPEIssue(db.Model):
    """PPE & uniform issuance/return log."""
    __tablename__ = "ppe_issues"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    item = db.Column(db.String(120), nullable=False)
    size = db.Column(db.String(30))
    qty = db.Column(db.Integer, default=1, nullable=False)
    issued_date = db.Column(db.Date)
    returned_date = db.Column(db.Date)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class HSEIncident(db.Model):
    """HSE incident log (injury, near miss, property damage, unsafe act)."""
    __tablename__ = "hse_incidents"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    location = db.Column(db.String(160))
    employee_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=True)
    type = db.Column(db.String(30), nullable=False, default="Near Miss")
    # Injury | Near Miss | Property Damage | Unsafe Act
    severity = db.Column(db.String(20), default="Low", nullable=False)
    # Low | Medium | High | Critical
    description = db.Column(db.Text)
    action_taken = db.Column(db.Text)
    status = db.Column(db.String(20), default="Open", nullable=False)  # Open|Closed
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ContractorWorker(db.Model):
    """Contractor / seasonal worker register with billing rate."""
    __tablename__ = "contractor_workers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    company = db.Column(db.String(120), nullable=False, index=True)
    trade = db.Column(db.String(80))
    site = db.Column(db.String(160))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    monthly_rate = db.Column(db.Float, default=0.0, nullable=False)
    status = db.Column(db.String(20), default="active", nullable=False)  # active|ended
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AssetCustody(db.Model):
    """Asset & tool custody register (V3.1.1).

    Returnable assets (tools, IT assets, vehicles) issued to employees;
    unreturned items surface in the separation register as a clearance warning.
    """
    __tablename__ = "asset_custody"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    item_name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(30), nullable=False, default="Tool")
    # Tool | IT Asset | Vehicle | Other
    serial_no = db.Column(db.String(80))
    replacement_value = db.Column(db.Float, default=0.0, nullable=False)
    issued_date = db.Column(db.Date)
    returned_date = db.Column(db.Date)
    status = db.Column(db.String(20), default="Issued", nullable=False)
    # Issued | Returned | Lost
    note = db.Column(db.String(300))
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def is_outstanding(self):
        return self.status == "Issued" and not self.returned_date
