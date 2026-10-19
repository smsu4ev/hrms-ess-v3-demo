"""Database models for the HRMS + ESS portal (v2)."""
from datetime import datetime, date, timedelta
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
    # v3.4: HR team flags for the configurable approval matrix.
    is_hr_staff = db.Column(db.Boolean, default=False, nullable=False)
    is_hr_manager = db.Column(db.Boolean, default=False, nullable=False)
    # v3.4: employee-creation approvals. A record created by HR staff waits
    # as employment_status="pending_approval" (is_active=False); the HR
    # Manager's approval activates it with the intended status below.
    pending_status = db.Column(db.String(20))
    # v3.4.1: employee can opt out of birthday/anniversary celebration posts
    share_celebrations = db.Column(db.Boolean, default=True, nullable=False)
    # v3.9: theme preference key (nixtio|glass|indigo|night); cookie is the fallback
    ui_theme = db.Column(db.String(16), nullable=True)
    phone = db.Column(db.String(30))
    passport_no = db.Column(db.String(40))
    date_of_birth = db.Column(db.Date)  # v3.3: birthday celebrations
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


# ===================== HRMS+ESS V3.2 models =====================

class ShiftPattern(db.Model):
    """Shift patterns for factory/office rostering (v3.2)."""
    __tablename__ = "shift_patterns"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)  # GEN|MORN|EVE|NIGHT
    name = db.Column(db.String(80), nullable=False)
    start_time = db.Column(db.String(5), nullable=False)  # "HH:MM"
    end_time = db.Column(db.String(5), nullable=False)    # "HH:MM"
    break_minutes = db.Column(db.Integer, default=60, nullable=False)
    grace_minutes = db.Column(db.Integer, default=10, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_outdoor = db.Column(db.Boolean, default=False, nullable=False)  # v3.2.1: outdoor/site work -> midday-break warning
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def crosses_midnight(self):
        # "HH:MM" zero-padded strings compare lexicographically.
        return (self.end_time or "") <= (self.start_time or "")


class RosterAssignment(db.Model):
    """One employee's shift for one date (v3.2)."""
    __tablename__ = "roster_assignments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    shift_id = db.Column(db.Integer, db.ForeignKey("shift_patterns.id"), nullable=False)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    shift = db.relationship("ShiftPattern")
    __table_args__ = (db.UniqueConstraint("user_id", "date"),)


class RosterSettings(db.Model):
    """Singleton row (id=1): Ramadan + summer midday-break configuration (v3.2)."""
    __tablename__ = "roster_settings"
    id = db.Column(db.Integer, primary_key=True)
    ramadan_start = db.Column(db.Date, nullable=True)
    ramadan_end = db.Column(db.Date, nullable=True)
    ramadan_daily_hours = db.Column(db.Float, default=6.0, nullable=False)
    standard_daily_hours = db.Column(db.Float, default=8.0, nullable=False)
    midday_break_start = db.Column(db.String(5), default="12:30", nullable=False)
    midday_break_end = db.Column(db.String(5), default="15:00", nullable=False)
    midday_season_start_md = db.Column(db.String(5), default="06-15", nullable=False)
    midday_season_end_md = db.Column(db.String(5), default="09-15", nullable=False)


class FinalSettlement(db.Model):
    """Full & Final settlement for a separating employee (v3.2).

    Status flow: draft -> calculated -> approved -> disbursed -> paid.
    Calculate freezes computed + manual lines into lines_json.
    Approve enforces creator != approver (SoD, same rule as payroll runs).
    Disburse (v3.4.1) requires a payment proof upload + reference.
    Mark Paid (closure) requires the signed cancellation / EOS receipt.
    """
    __tablename__ = "final_settlements"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"), nullable=False, index=True)
    separation_record_id = db.Column(db.Integer, db.ForeignKey("separation_records.id"),
                                     nullable=True)
    last_working_day = db.Column(db.Date, nullable=False)
    separation_type = db.Column(db.String(30), default="Resignation", nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default="draft", nullable=False)
    # draft | calculated | approved | disbursed | paid
    notice_days = db.Column(db.Integer, default=0, nullable=False)
    notice_direction = db.Column(db.String(20), default="none", nullable=False)
    # company_pays | employee_pays | none
    lines_json = db.Column(db.Text, default="[]", nullable=False)
    total_payable = db.Column(db.Float, default=0.0, nullable=False)
    total_recoverable = db.Column(db.Float, default=0.0, nullable=False)
    net = db.Column(db.Float, default=0.0, nullable=False)
    # v3.2.1: unpaid absence days excluded from gratuity service (Art. 51),
    # signed MOHRE cancellation / EOS receipt required before Mark Paid,
    # structured repatriation-ticket option.
    unpaid_days_used = db.Column(db.Float, default=0.0, nullable=False)
    cancellation_doc_path = db.Column(db.String(120))
    ticket_option = db.Column(db.String(20), default="none", nullable=False)
    # none | cash | booked | na
    ticket_amount = db.Column(db.Float, default=0.0, nullable=False)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    approved_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    approved_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)
    paid_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    # v3.4.1: disbursement split — approved -> disbursed -> paid.
    # Gate 1 (disbursement): payment proof upload + reference.
    # Gate 2 (closure): signed MOHRE cancellation / EOS receipt.
    payment_doc_path = db.Column(db.String(120))
    payment_ref = db.Column(db.String(80))
    disbursed_at = db.Column(db.DateTime)
    disbursed_by_id = db.Column(db.String(20), db.ForeignKey("users.id"))

    user = db.relationship("User", foreign_keys="FinalSettlement.user_id")

    @property
    def lines(self):
        import json
        try:
            return json.loads(self.lines_json or "[]")
        except Exception:
            return []

    @property
    def payment_due_date(self):
        """Art. 53: all entitlements must be paid within 14 days of contract end."""
        if not self.last_working_day:
            return None
        return self.last_working_day + timedelta(days=14)

    @property
    def is_overdue(self):
        # v3.4.1: the overdue clock stops at disbursement — once the money
        # is paid out, the Art. 53 deadline is met; only the receipt gate
        # (closure) remains open.
        return (self.status in ("calculated", "approved")
                and self.payment_due_date is not None
                and date.today() > self.payment_due_date)


class SettlementLine(db.Model):
    """Manual adjustment lines entered while a settlement is a draft (v3.2)."""
    __tablename__ = "settlement_lines"
    id = db.Column(db.Integer, primary_key=True)
    settlement_id = db.Column(db.Integer, db.ForeignKey("final_settlements.id"),
                              nullable=False, index=True)
    label = db.Column(db.String(160), nullable=False)
    kind = db.Column(db.String(20), default="payable", nullable=False)  # payable|recoverable
    amount = db.Column(db.Float, default=0.0, nullable=False)
    is_manual = db.Column(db.Boolean, default=True, nullable=False)
    note = db.Column(db.String(300))
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ===================== HRMS+ESS V3.3 models =====================

class AppSetting(db.Model):
    """Simple key/value store for housekeeping state (v3.3).

    Keys used: "punch_purge_last_run" (YYYY-MM-DD),
    "celebrations_last_run" (YYYY-MM-DD).
    """
    __tablename__ = "app_settings"
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(200))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    @staticmethod
    def get(key, default=None):
        row = AppSetting.query.get(key)
        return row.value if row and row.value is not None else default

    @staticmethod
    def set(key, value):
        row = AppSetting.query.get(key)
        if row is None:
            row = AppSetting(key=key, value=value)
            db.session.add(row)
        else:
            row.value = value


class WorkLocation(db.Model):
    """Named geo-fenced work sites for mobile check-in/out (v3.3)."""
    __tablename__ = "work_locations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    radius_m = db.Column(db.Integer, default=200, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PunchRecord(db.Model):
    """Geo + selfie check-in/out record (v3.3).

    punched_at is UTC; local_date is the Asia/Dubai calendar date.
    Photos auto-delete after 45 days; the record is kept.
    """
    __tablename__ = "punch_records"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    punch_type = db.Column(db.String(10), nullable=False)  # in|out
    punched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    local_date = db.Column(db.Date, nullable=False, index=True)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    accuracy_m = db.Column(db.Float)
    location_id = db.Column(db.Integer, db.ForeignKey("work_locations.id"))
    distance_m = db.Column(db.Float)
    photo_path = db.Column(db.String(120))
    # v3.4.1: low-accuracy GPS flag + legal hold (pauses the 45-day purge)
    low_accuracy = db.Column(db.Boolean, default=False, nullable=False)
    legal_hold = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    location = db.relationship("WorkLocation")


class Notice(db.Model):
    """Company notices pushed to the notification centre (v3.3)."""
    __tablename__ = "notices"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    audience = db.Column(db.String(80), default="all", nullable=False)
    # "all" or a department name
    requires_ack = db.Column(db.Boolean, default=False, nullable=False)
    # mandatory read: employees must acknowledge this notice
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime)


class NoticeAck(db.Model):
    """Mandatory-read acknowledgement of a notice (v3.3)."""
    __tablename__ = "notice_acks"
    id = db.Column(db.Integer, primary_key=True)
    notice_id = db.Column(db.Integer, db.ForeignKey("notices.id"),
                          nullable=False, index=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    acknowledged_at = db.Column(db.DateTime, default=datetime.utcnow,
                                nullable=False)
    __table_args__ = (db.UniqueConstraint("notice_id", "user_id"),)


class Notification(db.Model):
    """Employee notification centre entries (v3.3)."""
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    kind = db.Column(db.String(30), default="system", nullable=False)
    # notice|birthday|anniversary|wish|training|jd|system
    # v3.3.1: 'important' (default) | 'event' — celebration alerts
    # (birthday/anniversary/wish) are 'event', everything else 'important'.
    category = db.Column(db.String(20), default="important", nullable=False)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    link = db.Column(db.String(200))
    ref_id = db.Column(db.Integer)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False,
                           index=True)


class CelebrationPost(db.Model):
    """One birthday / work-anniversary post per employee per day (v3.3)."""
    __tablename__ = "celebration_posts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)  # birthday|anniversary
    post_date = db.Column(db.Date, nullable=False, index=True)
    years = db.Column(db.Integer)  # anniversary years of service
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("user_id", "kind", "post_date"),)


class CelebrationWish(db.Model):
    """One row per (post, well-wisher): a message and/or a reaction (v3.3).

    Reactions use the fixed REACTIONS set on the model; re-reacting by the
    same user replaces their row (one reaction per user per post).
    """
    __tablename__ = "celebration_wishes"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("celebration_posts.id"),
                        nullable=False, index=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    message = db.Column(db.String(200))
    reaction = db.Column(db.String(16))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("post_id", "user_id"),)


REACTIONS = ["👍", "❤️", "🎉", "👏", "🎂", "💐"]


class TrainingCourse(db.Model):
    """LMS-lite course with optional material and graded quiz (v3.3)."""
    __tablename__ = "training_courses"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(30), default="Basic", nullable=False)
    # Induction|Orientation|Basic|Technical|Safety|Other
    description = db.Column(db.Text)
    material_kind = db.Column(db.String(10), default="none", nullable=False)
    # none|file|link
    material_path = db.Column(db.String(120))  # private storage filename
    material_url = db.Column(db.String(400))
    pass_percent = db.Column(db.Integer, default=70, nullable=False)
    issue_cert_name = db.Column(db.String(120))  # creates a Certification on pass
    cert_valid_months = db.Column(db.Integer)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class QuizQuestion(db.Model):
    """One multiple-choice question of a course quiz (v3.3)."""
    __tablename__ = "quiz_questions"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("training_courses.id"),
                          nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    options_json = db.Column(db.Text, nullable=False)  # JSON list of 4 strings
    correct_index = db.Column(db.Integer, default=0, nullable=False)
    sort = db.Column(db.Integer, default=0, nullable=False)

    @property
    def options(self):
        import json
        try:
            return json.loads(self.options_json or "[]")
        except Exception:
            return []


class TrainingAssignment(db.Model):
    """A course assigned to one employee (v3.3)."""
    __tablename__ = "training_assignments"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("training_courses.id"),
                          nullable=False, index=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    due_date = db.Column(db.Date)
    assigned_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("course_id", "user_id"),)


class TrainingAttempt(db.Model):
    """One graded quiz attempt (v3.3). Best score is derived from attempts."""
    __tablename__ = "training_attempts"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("training_courses.id"),
                          nullable=False, index=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    score_percent = db.Column(db.Float, default=0.0, nullable=False)
    passed = db.Column(db.Boolean, default=False, nullable=False)
    answers_json = db.Column(db.Text, default="[]", nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class JobDescription(db.Model):
    """Job description with versioning (v3.3, section 5).

    Acknowledgements are per-version: editing the body bumps version and
    older acknowledgements show as needing re-acknowledgement.
    """
    __tablename__ = "job_descriptions"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)  # position
    department = db.Column(db.String(80))
    reports_to = db.Column(db.String(120))
    body = db.Column(db.Text, nullable=False)
    material_path = db.Column(db.String(120))  # optional private PDF upload
    version = db.Column(db.Integer, default=1, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)


class JDAcknowledgement(db.Model):
    """Employee acknowledgement of a job description (v3.3, section 5)."""
    __tablename__ = "jd_acknowledgements"
    id = db.Column(db.Integer, primary_key=True)
    jd_id = db.Column(db.Integer, db.ForeignKey("job_descriptions.id"),
                      nullable=False, index=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    status = db.Column(db.String(20), default="assigned", nullable=False)
    # assigned | viewed | acknowledged
    jd_version = db.Column(db.Integer, default=1, nullable=False)
    # version acknowledged (or assigned at); stale if < current jd.version
    assigned_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    viewed_at = db.Column(db.DateTime)
    acknowledged_at = db.Column(db.DateTime)
    signature_path = db.Column(db.String(120))
    signer_ip = db.Column(db.String(45))
    typed_name = db.Column(db.String(120))
    # v3.4.1: tamper evidence — hash of the exact text acknowledged + agent
    jd_text_hash = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))
    __table_args__ = (db.UniqueConstraint("jd_id", "user_id"),)

    @property
    def needs_reack(self):
        jd = JobDescription.query.get(self.jd_id)
        return bool(jd and self.jd_version < jd.version)


# ===================== HRMS+ESS V3.4 models =====================
# Configurable approval matrix: multi-step approval chains per process
# (employee creation, leave, attendance regularisation), customisable by
# the HR Admin.

APPROVAL_PROCESSES = [
    ("employee_creation", "Employee Creation"),
    ("leave", "Leave"),
    ("regularisation", "Attendance Regularisation"),
    ("expense", "Expense Claims"),
    ("shift_swap", "Shift Swaps"),
]

APPROVER_TYPES = [
    ("reporting_manager", "Reporting Manager"),
    ("skip_level_manager", "Skip-Level Manager"),
    ("hr_staff", "HR Staff"),
    ("hr_manager", "HR Manager"),
    ("admin", "Administrator"),
    ("specific_user", "Specific User"),
]


class ApprovalMatrix(db.Model):
    """One ordered step chain per process. Admin-customisable (v3.4)."""
    __tablename__ = "approval_matrices"
    id = db.Column(db.Integer, primary_key=True)
    process_key = db.Column(db.String(40), unique=True, nullable=False,
                            index=True)  # employee_creation|leave|regularisation
    name = db.Column(db.String(80), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    steps = db.relationship("ApprovalStep",
                            backref="matrix", lazy="dynamic",
                            order_by="ApprovalStep.step_no",
                            cascade="all, delete-orphan")


class ApprovalStep(db.Model):
    """One step in a matrix chain (v3.4)."""
    __tablename__ = "approval_steps"
    id = db.Column(db.Integer, primary_key=True)
    matrix_id = db.Column(db.Integer, db.ForeignKey("approval_matrices.id"),
                          nullable=False, index=True)
    step_no = db.Column(db.Integer, nullable=False)  # 1-based order
    approver_type = db.Column(db.String(30), nullable=False)
    # reporting_manager|skip_level_manager|hr_staff|hr_manager|admin|specific_user
    approver_user_id = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("matrix_id", "step_no"),)


class ApprovalInstance(db.Model):
    """One in-flight approval for a concrete request (v3.4).

    steps_json snapshots the matrix chain at start time, so editing the
    matrix later never mutates in-flight instances.
    """
    __tablename__ = "approval_instances"
    id = db.Column(db.Integer, primary_key=True)
    process_key = db.Column(db.String(40), nullable=False, index=True)
    ref_id = db.Column(db.String(40), nullable=False, index=True)
    # leave/regularisation request id | new employee user id
    subject_user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                                nullable=False, index=True)
    # the employee the request is about
    requester_id = db.Column(db.String(20), db.ForeignKey("users.id"))
    # who submitted / created the request
    steps_json = db.Column(db.Text, nullable=False, default="[]")
    # [{"step_no": n, "approver_type": ..., "approver_user_id": ...}]
    current_step = db.Column(db.Integer, default=1, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    # pending | approved | rejected
    # v3.4.1: halted when the last step has no resolvers — only an admin
    # (not subject/requester) may then decide the instance.
    halted = db.Column(db.Boolean, default=False, nullable=False)
    reject_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    decided_at = db.Column(db.DateTime)
    __table_args__ = (db.UniqueConstraint("process_key", "ref_id"),)

    @property
    def steps(self):
        import json
        try:
            return json.loads(self.steps_json or "[]")
        except Exception:
            return []

    @property
    def total_steps(self):
        return len(self.steps)

    @property
    def current_step_def(self):
        for s in self.steps:
            if s.get("step_no") == self.current_step:
                return s
        return None

    @property
    def exhausted(self):
        return self.current_step > self.total_steps

    @property
    def process_label(self):
        return dict(APPROVAL_PROCESSES).get(self.process_key, self.process_key)


class ApprovalAction(db.Model):
    """One approve/reject/auto_skip recorded on an instance (v3.4)."""
    __tablename__ = "approval_actions"
    id = db.Column(db.Integer, primary_key=True)
    instance_id = db.Column(db.Integer,
                            db.ForeignKey("approval_instances.id"),
                            nullable=False, index=True)
    step_no = db.Column(db.Integer, nullable=False)
    actor_id = db.Column(db.String(20), db.ForeignKey("users.id"))
    # None for auto_skip
    action = db.Column(db.String(20), nullable=False)  # approve|reject|auto_skip
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False,
                           index=True)

    instance = db.relationship("ApprovalInstance",
                               backref=db.backref("actions", lazy="dynamic",
                                                  order_by="ApprovalAction.id",
                                                  cascade="all, delete-orphan"))


# ===================== HRMS+ESS V3.4.1 models =====================

class ApprovalDelegation(db.Model):
    """Temporary delegation of approval authority (v3.4.1).

    When the delegator is a resolver of a step (person-specific
    resolutions: reporting manager, skip-level manager, specific user, each
    flagged HR Manager holder), the delegate is added as an additional
    resolver while the delegation window is active. The original person
    remains a resolver too. process_key NULL = all three processes.
    """
    __tablename__ = "approval_delegations"
    id = db.Column(db.Integer, primary_key=True)
    delegator_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                             nullable=False, index=True)
    delegate_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                            nullable=False, index=True)
    process_key = db.Column(db.String(40), index=True)
    # employee_creation|leave|regularisation, NULL = all processes
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)

    @property
    def in_window(self):
        today = datetime.utcnow().date()
        return (self.is_active and self.starts_on <= today <= self.ends_on)


# ===================== HRMS+ESS V3.3.1 models =====================
# ESS extras: expense claims, employee letter requests.

EXPENSE_CATEGORIES = [
    ("Travel", "Travel"),
    ("Fuel", "Fuel"),
    ("Food", "Food"),
    ("Accommodation", "Accommodation"),
    ("Materials", "Materials"),
    ("Other", "Other"),
]


class ExpenseClaim(db.Model):
    """Employee expense claim, routed through the approval matrix (v3.3.1).

    On final approval an EARNING PayrollInput "Expense reimbursement
    (claim #<id>)" is created for the claimant in the current period.
    """
    __tablename__ = "expense_claims"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    claim_date = db.Column(db.Date, nullable=False)
    category = db.Column(db.String(40), nullable=False)
    amount = db.Column(db.Float, default=0.0, nullable=False)
    description = db.Column(db.String(300))
    receipt_path = db.Column(db.String(120))  # private upload, optional
    status = db.Column(db.String(20), default="pending", nullable=False)
    # pending | approved | rejected
    payroll_input_id = db.Column(db.Integer,
                                 db.ForeignKey("payroll_inputs.id"))
    decided_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    decision_comment = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)

    payroll_input = db.relationship("PayrollInput")


LETTER_REQUEST_TYPES = [
    ("salary_certificate", "Salary Certificate"),
    ("salary_transfer", "Salary Transfer Letter"),
    ("employment_certificate", "Employment Certificate"),
    ("experience_letter", "Experience Letter"),
    ("noc", "No Objection Certificate"),
]

LETTER_REQUEST_STATUSES = [
    ("submitted", "Submitted"),
    ("in_progress", "In Progress"),
    ("ready", "Ready"),
    ("delivered", "Delivered"),
    ("rejected", "Rejected"),
]


class LetterRequest(db.Model):
    """Employee request for an HR letter (v3.3.1).

    Fulfilled either by generating from the mail-merge letters engine
    (generated_letter_id) or by HR uploading a signed PDF (uploaded_path).
    """
    __tablename__ = "letter_requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(20), db.ForeignKey("users.id"),
                        nullable=False, index=True)
    letter_type = db.Column(db.String(40), nullable=False)
    purpose = db.Column(db.String(300))
    status = db.Column(db.String(20), default="submitted", nullable=False)
    # submitted | in_progress | ready | delivered | rejected
    generated_letter_id = db.Column(db.Integer,
                                    db.ForeignKey("generated_letters.id"))
    uploaded_path = db.Column(db.String(120))  # signed PDF uploaded by HR
    hr_comment = db.Column(db.String(500))
    processed_by = db.Column(db.String(20), db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    generated_letter = db.relationship("GeneratedLetter")


# v3.6: HR Settings key/value store (Frappe-model HR Settings page).
class SettingKV(db.Model):
    __tablename__ = "setting_kv"
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(500), nullable=False, default="")
    updated_by = db.Column(db.String(20))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)


# v3.5: helpdesk tickets + shift swaps (kept in a separate module to keep
# models.py readable; single-table creation pattern applies).
from app.models_v35 import (HelpdeskTicket, TicketComment,  # noqa: E402,F401
                            ShiftSwapRequest, TICKET_CATEGORIES,
                            TICKET_PRIORITIES, TICKET_SLA_HOURS,
                            TICKET_STATUSES, SWAP_STATUSES)
