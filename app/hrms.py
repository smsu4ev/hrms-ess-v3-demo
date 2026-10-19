"""HRMS blueprint (v2): Employee Central, Recruitment Tracker, Onboarding."""
from datetime import date, datetime, timedelta
import calendar as calmod
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import or_

from app import db
from app.models import (User, LeaveBalance, LeaveRequest, AttendanceRecord,
                        Candidate, OnboardingCase, OnboardingTask,
                        ONBOARDING_CHECKLIST_ITEMS, Company, EmployeeDocument)
from app.audit import record, audit_model_changes, snapshot, csv_cell
from app.views import manager_required  # noqa: F401
from app.files import salary_visible
import config as app_config

hrms_bp = Blueprint("hrms", __name__, url_prefix="/hrms")


def admin_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def hr_required(fn):
    """Admin OR a user granted salary visibility by the HR Manager."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not salary_visible(current_user):
            abort(403)
        return fn(*a, **kw)
    return wrapper


def admin_post_only(fn):
    """State-changing employee writes: only admins may POST.

    Users with salary visibility keep read access, but creating or editing
    employee records (salaries, IBAN, bank) is an admin-only action.
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if request.method == "POST" and not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def employee_creator_required(fn):
    """Employee creation (v3.4): admins, HR managers and HR staff may open
    the form and submit it. Records created by plain HR staff go through
    the approval matrix before activation; the HR Manager (or an admin)
    approves them on the review page."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not (current_user.is_admin or current_user.is_hr_staff
                or current_user.is_hr_manager):
            abort(403)
        return fn(*a, **kw)
    return wrapper


EMPLOYEE_FIELDS = ["name", "department", "designation", "reporting_manager",
                   "joining_date", "employment_status", "nationality",
                   "visa_expiry_date", "passport_expiry_date",
                   "emirates_id_expiry_date", "emirates_id_no", "passport_no",
                   "email", "mobile", "phone", "iban", "bank_name",
                   "visa_company_id", "exit_date",
                   "separation_date", "separation_type",  # v3.1
                   "date_of_birth",  # v3.3
                   "basic_salary", "monthly_salary"]

EMPLOYMENT_STATUSES = ["active", "probation", "resigned", "terminated", "retired"]


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_upcoming_expiries(days=30, limit=50):
    """Employees whose visa or passport expires within `days` (and not already expired long ago)."""
    today = date.today()
    cutoff = today + timedelta(days=days)
    out = []
    users = User.query.filter(User.is_active.is_(True)).all()
    for u in users:
        docs = (("visa", u.visa_expiry_date), ("passport", u.passport_expiry_date),
                ("emirates id", u.emirates_id_expiry_date))
        for doc, d in docs:
            if d and today - timedelta(days=30) <= d <= cutoff:
                out.append({"user": u, "doc": doc, "date": d,
                            "days_left": (d - today).days})
    out.sort(key=lambda r: r["days_left"])
    return out[:limit]


# ---------------- Employee Central ----------------
@hrms_bp.route("/employees")
@login_required
@hr_required
def employees():
    q = (request.args.get("q") or "").strip()
    dept = (request.args.get("dept") or "").strip()
    status = (request.args.get("status") or "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.id.ilike(like), User.name.ilike(like),
                                 User.designation.ilike(like)))
    if dept:
        query = query.filter_by(department=dept)
    if status:
        query = query.filter_by(employment_status=status)
    statuses = list(EMPLOYMENT_STATUSES)
    if "pending_approval" not in statuses:
        statuses.append("pending_approval")
    rows = query.order_by(User.name).limit(300).all()
    departments = sorted({u.department for u in User.query.all() if u.department})
    return render_template("hr_employees.html", rows=rows, q=q, dept=dept,
                           status=status, departments=departments,
                           statuses=statuses)


@hrms_bp.route("/employees/export", methods=["POST"])
@login_required
@admin_required
def employees_export():
    """V3.6 bulk export: CSV of the ticked employees (admin only).

    Uses the same formula-injection guard as the reports (csv_cell).
    """
    import csv
    import io
    from flask import Response
    uids = [u for u in request.form.getlist("uid") if u]
    if not uids:
        flash("Select at least one employee to export.", "error")
        return redirect(url_for("hrms.employees"))
    users = User.query.filter(User.id.in_(uids)).order_by(User.name).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Employee ID", "Name", "Designation", "Department",
                "Reporting Manager", "Employment Status", "Joining Date",
                "Visa Expiry", "Passport Expiry", "Email", "Mobile"])
    for u in users:
        w.writerow([csv_cell(u.id), csv_cell(u.name),
                    csv_cell(u.designation), csv_cell(u.department),
                    csv_cell(u.reporting_manager),
                    csv_cell(u.employment_status),
                    u.joining_date.isoformat() if u.joining_date else "",
                    u.visa_expiry_date.isoformat()
                    if u.visa_expiry_date else "",
                    u.passport_expiry_date.isoformat()
                    if u.passport_expiry_date else "",
                    csv_cell(u.email), csv_cell(u.mobile)])
    record("employees_exported",
           f"{len(users)} employee(s) exported to CSV: "
           f"{', '.join(u.id for u in users)}",
           entity="employee", entity_id="bulk")
    db.session.commit()
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=employees_export.csv"})


@hrms_bp.route("/employees/new", methods=["GET", "POST"])
@login_required
@employee_creator_required
def employee_new():
    error = None
    companies = Company.query.filter_by(is_active=True).order_by(Company.code).all()
    if request.method == "POST":
        uid = (request.form.get("employee_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        if not uid or not name:
            error = "Employee ID and name are required."
        elif User.query.get(uid):
            error = f"Employee ID {uid} already exists."
        else:
            # v3.4: approval matrix for employee creation. HR staff (not the
            # HR Manager) create a pending, non-loginable record; the HR
            # Manager reviews and activates it. Salary fields and admin roles
            # can never be set through the HR-staff path.
            creator = current_user
            direct = bool(creator.is_admin or creator.is_hr_manager)
            intended_status = request.form.get("employment_status") or "probation"
            if intended_status not in EMPLOYMENT_STATUSES:
                intended_status = "probation"
            if direct:
                status, active, pending_status = intended_status, None, None
                basic = float(request.form.get("basic_salary") or 0)
                monthly = float(request.form.get("monthly_salary") or 0)
            else:
                status, active = "pending_approval", False
                pending_status = intended_status
                basic, monthly = 0.0, 0.0
            user = User(id=uid, name=name,
                        department=(request.form.get("department") or "").strip() or None,
                        designation=(request.form.get("designation") or "").strip() or None,
                        reporting_manager=(request.form.get("reporting_manager") or "").strip() or None,
                        joining_date=parse_date(request.form.get("joining_date")),
                        employment_status=status,
                        is_active=True if active is None else active,
                        pending_status=pending_status,
                        nationality=(request.form.get("nationality") or "").strip() or None,
                        visa_expiry_date=parse_date(request.form.get("visa_expiry_date")),
                        passport_expiry_date=parse_date(request.form.get("passport_expiry_date")),
                        emirates_id_expiry_date=parse_date(request.form.get("emirates_id_expiry_date")),
                        emirates_id_no=(request.form.get("emirates_id_no") or "").strip() or None,
                        passport_no=(request.form.get("passport_no") or "").strip() or None,
                        email=(request.form.get("email") or "").strip() or None,
                        mobile=(request.form.get("mobile") or "").strip() or None,
                        phone=(request.form.get("phone") or "").strip() or None,
                        iban=(request.form.get("iban") or "").strip().upper() or None,
                        bank_name=(request.form.get("bank_name") or "").strip() or None,
                        visa_company_id=(int(request.form.get("visa_company_id") or 0) or None),
                        exit_date=parse_date(request.form.get("exit_date")),
                        date_of_birth=parse_date(request.form.get("date_of_birth")),
                        basic_salary=basic,
                        monthly_salary=monthly)
            # Random temporary password: secrets-based (never a guessable
            # time-derived default). The "A1a!" suffix guarantees upper,
            # lower, digit and symbol characters.
            temp_pw = secrets.token_urlsafe(9) + "A1a!"
            user.set_password(temp_pw)
            user.must_change_password = True
            db.session.add(user)
            record("employee_created", f"Employee {uid} ({name}) created",
                   entity="employee", entity_id=uid)
            for f in EMPLOYEE_FIELDS:
                record("employee_created", f"{f} set to '{getattr(user, f)}'",
                       entity="employee", entity_id=uid, field_name=f,
                       old_value="", new_value=getattr(user, f))
            db.session.commit()
            if not direct:
                # v3.4: route through the approval matrix; the HR Manager
                # activates the record after review.
                from app.approvals import start_instance
                start_instance("employee_creation", uid, uid, creator.id)
                flash(f"Employee {uid} created and sent to the HR Manager "
                      f"for approval.", "success")
                return redirect(url_for("hrms.employee_review", uid=uid))
            flash(f"Employee {uid} created. Temporary password: {temp_pw} "
                  "- share it securely; the employee must change it on login.", "success")
            return redirect(url_for("hrms.employee_detail", uid=uid))
    managers = User.query.filter(User.role.in_(("manager", "admin")),
                                 User.is_active.is_(True)).order_by(User.name).all()
    return render_template("hr_employee_form.html", user=None, error=error,
                           statuses=EMPLOYMENT_STATUSES, managers=managers,
                           companies=companies,
                           creator_is_hr_staff=not (
                               current_user.is_admin or current_user.is_hr_manager))


@hrms_bp.route("/employees/<uid>")
@login_required
@hr_required
def employee_detail(uid):
    user = User.query.get_or_404(uid)
    balances = LeaveBalance.query.filter_by(user_id=uid).all()
    leaves = LeaveRequest.query.filter_by(user_id=uid)\
        .order_by(LeaveRequest.created_at.desc()).limit(10).all()
    month_start = date.today().replace(day=1)
    att = AttendanceRecord.query.filter(
        AttendanceRecord.user_id == uid,
        AttendanceRecord.date >= month_start).all()
    present = sum(1 for r in att if r.status in ("present", "late"))
    from app.models import AuditLog
    logs = AuditLog.query.filter_by(entity="employee", entity_id=uid)\
        .order_by(AuditLog.created_at.desc()).limit(30).all()
    manager = User.query.get(user.reporting_manager) if user.reporting_manager else None
    return render_template("hr_employee_detail.html", user=user, balances=balances,
                           leaves=leaves, present_days=present, logs=logs, manager=manager)


@hrms_bp.route("/employees/<uid>/review", methods=["GET", "POST"])
@login_required
def employee_review(uid):
    """HR-approval review for an employee created by HR staff (v3.4).

    Read-only field display plus Approve/Reject for the current step's
    resolvers. Approving activates the record with the intended status.
    """
    from app.approvals import (get_instance, can_act, act, current_resolvers,
                               step_badge, self_blocked)
    user = User.query.get_or_404(uid)
    instance = get_instance("employee_creation", uid)
    if instance is None:
        flash("This employee record was not created through the approval "
              "flow.", "error")
        return redirect(url_for("hrms.employee_detail", uid=uid))
    resolvers = current_resolvers(instance) if instance.status == "pending" else []
    may_view = (current_user.is_admin or current_user.is_hr_manager
                or current_user.id in resolvers
                or current_user.id == instance.requester_id)
    if not may_view:
        abort(403)
    actions = instance.actions.all()  # already ordered by id (v3.4 model)
    approver_names = {u.id: u.name for u in User.query.filter(
        User.id.in_([a.actor_id for a in actions if a.actor_id])).all()}
    if request.method == "POST":
        if instance.status != "pending":
            flash("This request has already been decided.", "error")
            return redirect(url_for("hrms.employee_review", uid=uid))
        action = request.form.get("action")
        comment = (request.form.get("comment") or "").strip()[:500]
        if action not in ("approve", "reject"):
            abort(400)
        try:
            outcome = act(instance, current_user, action == "approve", comment)
        except PermissionError as e:
            if self_blocked(instance, current_user) and \
                    "own request" in str(e):
                flash("You cannot approve your own request.", "error")
                return redirect(url_for("hrms.employee_review", uid=uid))
            abort(403)
        if outcome == "final_approved":
            user.employment_status = user.pending_status or "probation"
            user.pending_status = None
            user.is_active = user.employment_status in ("active", "probation")
            db.session.commit()
            record("employee_approved",
                   f"Employee {uid} ({user.name}) approved and activated "
                   f"as {user.employment_status}",
                   entity="employee", entity_id=uid,
                   field_name="employment_status",
                   old_value="pending_approval",
                   new_value=user.employment_status)
            db.session.commit()
            flash(f"Employee {uid} approved and activated.", "success")
        elif outcome == "rejected":
            flash("Employee creation rejected. The record stays inactive.",
                  "success")
        else:
            flash("Approved at this step; forwarded to the next approver.",
                  "success")
        return redirect(url_for("hrms.employee_review", uid=uid))
    return render_template("hr_employee_review.html", user=user,
                           instance=instance, actions=actions,
                           approver_names=approver_names,
                           can_act=can_act(instance, current_user),
                           badge=step_badge(instance),
                           resolvers=resolvers)


@hrms_bp.route("/employees/<uid>/edit", methods=["GET", "POST"])
@login_required
@hr_required
@admin_post_only
def employee_edit(uid):
    user = User.query.get_or_404(uid)
    error = None
    if request.method == "POST":
        before = snapshot(user, EMPLOYEE_FIELDS)
        user.name = (request.form.get("name") or "").strip() or user.name
        user.department = (request.form.get("department") or "").strip() or None
        user.designation = (request.form.get("designation") or "").strip() or None
        user.reporting_manager = (request.form.get("reporting_manager") or "").strip() or None
        user.joining_date = parse_date(request.form.get("joining_date"))
        status = request.form.get("employment_status") or "active"
        user.employment_status = status if status in EMPLOYMENT_STATUSES else "active"
        user.is_active = status in ("active", "probation")
        user.nationality = (request.form.get("nationality") or "").strip() or None
        user.visa_expiry_date = parse_date(request.form.get("visa_expiry_date"))
        user.passport_expiry_date = parse_date(request.form.get("passport_expiry_date"))
        user.emirates_id_expiry_date = parse_date(request.form.get("emirates_id_expiry_date"))
        user.emirates_id_no = (request.form.get("emirates_id_no") or "").strip() or None
        user.passport_no = (request.form.get("passport_no") or "").strip() or None
        user.email = (request.form.get("email") or "").strip() or None
        user.mobile = (request.form.get("mobile") or "").strip() or None
        user.phone = (request.form.get("phone") or "").strip() or None
        user.iban = (request.form.get("iban") or "").strip().upper() or None
        user.bank_name = (request.form.get("bank_name") or "").strip() or None
        user.visa_company_id = int(request.form.get("visa_company_id") or 0) or None
        user.exit_date = parse_date(request.form.get("exit_date"))
        user.date_of_birth = parse_date(request.form.get("date_of_birth"))
        # v3.1: separation register linkage (shown in the form when the
        # employee is resigned/terminated)
        user.separation_date = parse_date(request.form.get("separation_date"))
        sep_type = (request.form.get("separation_type") or "").strip() or None
        user.separation_type = sep_type if not sep_type or sep_type in (
            "Resignation", "Termination", "Contract End", "Retirement",
            "Absconding", "Other") else None
        try:
            user.basic_salary = float(request.form.get("basic_salary") or 0)
            user.monthly_salary = float(request.form.get("monthly_salary") or 0)
        except ValueError:
            error = "Salary must be a number."
        if not error:
            audit_model_changes(user, before, EMPLOYEE_FIELDS, entity="employee")
            db.session.commit()
            flash("Employee record updated.", "success")
            return redirect(url_for("hrms.employee_detail", uid=uid))
    managers = User.query.filter(User.role.in_(("manager", "admin")),
                                 User.is_active.is_(True)).order_by(User.name).all()
    companies = Company.query.filter_by(is_active=True).order_by(Company.code).all()
    return render_template("hr_employee_form.html", user=user, error=error,
                           statuses=EMPLOYMENT_STATUSES, managers=managers,
                           companies=companies)


# ---------------- Recruitment Tracker ----------------
@hrms_bp.route("/recruitment", methods=["GET", "POST"])
@login_required
@admin_required
def recruitment():
    stage = (request.args.get("stage") or "").strip()
    q = Candidate.query
    if stage:
        q = q.filter_by(stage=stage)
    rows = q.order_by(Candidate.created_at.desc()).all()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        position = (request.form.get("position") or "").strip()
        if not name or not position:
            flash("Candidate name and position are required.", "error")
        else:
            c = Candidate(name=name, position=position,
                          phone=(request.form.get("phone") or "").strip() or None,
                          email=(request.form.get("email") or "").strip() or None,
                          source=(request.form.get("source") or "").strip() or None,
                          stage="Applied",
                          notes=(request.form.get("notes") or "").strip() or None,
                          created_by=current_user.id)
            db.session.add(c)
            db.session.flush()
            record("candidate_created", f"Candidate {c.id}: {name} for {position}",
                   entity="candidate", entity_id=c.id)
            db.session.commit()
            flash(f"Candidate {name} added.", "success")
            return redirect(url_for("hrms.recruitment"))
    return render_template("hr_recruitment.html", rows=rows, stage=stage,
                           stages=Candidate.STAGES)


@hrms_bp.route("/recruitment/<int:cid>", methods=["GET", "POST"])
@login_required
@admin_required
def candidate_detail(cid):
    c = Candidate.query.get_or_404(cid)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "stage":
            new_stage = request.form.get("stage", "")
            if new_stage in Candidate.STAGES and new_stage != c.stage:
                old = c.stage
                c.stage = new_stage
                record("candidate_stage", f"#{c.id} stage {old} -> {new_stage}",
                       entity="candidate", entity_id=c.id, field_name="stage",
                       old_value=old, new_value=new_stage)
                db.session.commit()
                flash(f"Stage moved to {new_stage}.", "success")
        elif action == "notes":
            c.notes = (request.form.get("notes") or "").strip() or None
            record("candidate_notes", f"#{c.id} notes updated",
                   entity="candidate", entity_id=c.id)
            db.session.commit()
            flash("Notes saved.", "success")
        return redirect(url_for("hrms.candidate_detail", cid=cid))
    from app.models import AuditLog
    logs = AuditLog.query.filter_by(entity="candidate", entity_id=str(cid))\
        .order_by(AuditLog.created_at.desc()).limit(30).all()
    return render_template("hr_candidate_detail.html", c=c, stages=Candidate.STAGES, logs=logs)


# ---------------- Onboarding Checklist ----------------
@hrms_bp.route("/onboarding", methods=["GET", "POST"])
@login_required
@admin_required
def onboarding():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Joiner name is required.", "error")
        else:
            case = OnboardingCase(name=name,
                                  joining_date=parse_date(request.form.get("joining_date")),
                                  employee_id=(request.form.get("employee_id") or "").strip() or None,
                                  notes=(request.form.get("notes") or "").strip() or None,
                                  created_by=current_user.id)
            db.session.add(case)
            db.session.flush()
            for label in ONBOARDING_CHECKLIST_ITEMS:
                db.session.add(OnboardingTask(case_id=case.id, label=label))
            record("onboarding_case_created", f"#{case.id}: {name}",
                   entity="onboarding", entity_id=case.id)
            db.session.commit()
            flash(f"Onboarding case for {name} created.", "success")
            return redirect(url_for("hrms.onboarding_case", oid=case.id))
    cases = OnboardingCase.query.order_by(OnboardingCase.created_at.desc()).all()
    return render_template("hr_onboarding.html", cases=cases)


@hrms_bp.route("/onboarding/<int:oid>", methods=["GET", "POST"])
@login_required
@admin_required
def onboarding_case(oid):
    case = OnboardingCase.query.get_or_404(oid)
    if request.method == "POST":
        tid = request.form.get("task_id")
        t = OnboardingTask.query.get_or_404(int(tid))
        if t.case_id != case.id:
            abort(403)
        t.is_done = not t.is_done
        t.done_at = datetime.utcnow() if t.is_done else None
        record("onboarding_task",
               f"#{case.id} '{t.label}' marked {'done' if t.is_done else 'pending'}",
               entity="onboarding", entity_id=case.id, field_name=t.label,
               old_value="pending" if t.is_done else "done",
               new_value="done" if t.is_done else "pending")
        db.session.commit()
        flash(f"'{t.label}' marked {'done' if t.is_done else 'pending'}.", "success")
        return redirect(url_for("hrms.onboarding_case", oid=oid))
    return render_template("hr_onboarding_case.html", case=case)


# ---------------- UAE Compliance Dashboard ----------------
@hrms_bp.route("/compliance", methods=["GET", "POST"])
@login_required
@hr_required
def compliance():
    """HR-admin UAE compliance overview (v2.1)."""
    from app.uae import gratuity, probation_end_date, WORKING_HOURS_NOTES
    today = date.today()
    # (a) Document expiries within 60 days: visa, passport, Emirates ID.
    expiries = get_upcoming_expiries(days=60, limit=200)
    # (b) Probation endings within 30 days.
    probation = []
    for u in User.query.filter(User.is_active.is_(True)).all():
        pend = probation_end_date(u.joining_date)
        if pend and today <= pend <= today + timedelta(days=30):
            probation.append({"user": u, "end": pend,
                              "days_left": (pend - today).days})
    probation.sort(key=lambda r: r["days_left"])
    # (c) Total gratuity liability estimate (employer-termination basis, today).
    liability = 0.0
    liability_count = 0
    for u in User.query.filter(User.is_active.is_(True)).all():
        if u.joining_date and (u.basic_salary or 0) > 0:
            g = gratuity(u.joining_date, today, u.basic_salary, "employer")
            liability += g["amount"]
            liability_count += 1
    # (d) Document-vault expiries within 60 days.
    doc_expiries = []
    for d in EmployeeDocument.query.filter(
            EmployeeDocument.superseded.is_(False),
            EmployeeDocument.expiry_date.isnot(None),
            EmployeeDocument.expiry_date <= today + timedelta(days=60)).all():
        doc_expiries.append({"doc": d,
                             "user": User.query.get(d.user_id),
                             "days_left": (d.expiry_date - today).days})
    doc_expiries.sort(key=lambda r: r["days_left"])
    # (d2) Certification expiries within 60 days (v3.1 retail registers).
    from app.models import Certification
    cert_expiries = []
    for c in Certification.query.filter(
            Certification.expiry_date.isnot(None),
            Certification.expiry_date <= today + timedelta(days=60)).all():
        cert_expiries.append({"cert": c, "user": User.query.get(c.user_id),
                              "days_left": (c.expiry_date - today).days})
    cert_expiries.sort(key=lambda r: r["days_left"])
    # (e) Rule-based insights (AI-assisted checks, no external calls).
    from app.payroll import compliance_insights, INSIGHT_LABEL
    insights = compliance_insights()
    if request.method == "POST" and request.form.get("action") == "queue_whatsapp":
        from app.payroll_views import queue_whatsapp
        queued = 0
        for r in doc_expiries:
            if r["doc"].status != "verified":
                continue
            u = r["user"]
            queue_whatsapp(u.id, "document_expiring",
                           f"Dear {u.name}, your {r['doc'].category} "
                           f"({r['doc'].title}) expires on {r['doc'].expiry_date}. "
                           "Please renew it and upload the new copy in ESS.")
            queued += 1
        record("whatsapp_queued",
               f"{queued} document-expiry reminders queued",
               entity="notification", entity_id="outbox")
        db.session.commit()
        flash(f"{queued} WhatsApp reminders queued.", "success")
        return redirect(url_for("hrms.compliance"))
    return render_template("hr_compliance.html",
                           expiries=expiries, probation=probation,
                           liability=round(liability, 2),
                           liability_count=liability_count,
                           today=today,
                           leave_types=app_config.LEAVE_TYPES,
                           hours_notes=WORKING_HOURS_NOTES,
                           doc_expiries=doc_expiries,
                           cert_expiries=cert_expiries,
                           insights=insights,
                           insight_label=INSIGHT_LABEL)


# ---------------- End-of-Service Gratuity Calculator (HR admin) ----------------
@hrms_bp.route("/gratuity", methods=["GET", "POST"])
@login_required
@hr_required
def gratuity_calc():
    """End-of-service gratuity calculator for any employee or manual values."""
    from app.uae import gratuity
    result = None
    form = {"employee_id": "", "basic": "", "join": "", "last": "",
            "separation": "employer"}
    employees = User.query.filter(User.is_active.is_(True))\
        .order_by(User.name).all()
    if request.method == "POST":
        form["employee_id"] = (request.form.get("employee_id") or "").strip()
        form["basic"] = (request.form.get("basic") or "").strip()
        form["join"] = (request.form.get("join") or "").strip()
        form["last"] = (request.form.get("last") or "").strip()
        form["separation"] = request.form.get("separation") or "employer"
        join, last, basic, who = None, None, 0.0, None
        emp = User.query.get(form["employee_id"]) if form["employee_id"] else None
        if emp:
            join = emp.joining_date
            last = date.today()
            basic = emp.basic_salary or 0.0
            who = f"{emp.name} ({emp.id})"
        else:
            join = parse_date(form["join"])
            last = parse_date(form["last"]) or date.today()
            try:
                basic = float(form["basic"] or 0)
            except ValueError:
                basic = 0.0
            who = "Manual entry"
        if not join:
            flash("Joining date is required.", "error")
        elif not basic or basic <= 0:
            flash("A basic salary above zero is required.", "error")
        else:
            result = gratuity(join, last, basic, form["separation"])
            result["who"] = who
            result["basic"] = round(basic, 2)
            result["last_working"] = last
            record("gratuity_calculated",
                   f"{who}: AED {result['amount']} ({form['separation']})",
                   entity="employee",
                   entity_id=form["employee_id"] or "manual")
            db.session.commit()
    return render_template("hr_gratuity.html", result=result, form=form,
                           employees=employees)


# ---------------- WPS Payroll Export (HR admin) ----------------
@hrms_bp.route("/wps/export")
@login_required
@admin_required
def wps_export():
    """Legacy simple WPS payroll CSV for active employees (kept working).

    This is a rough CSV working input, not the per-visa-company WPS files -
    use /hrms/wps (WPS Files) for the company-split payroll files. It is NOT
    an official MOHRE .sif file.
    """
    import csv
    import io
    from flask import Response
    rows = User.query.filter(User.is_active.is_(True)).order_by(User.name).all()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Employee ID", "Name", "Bank IBAN", "Bank Name",
                     "Basic Salary (AED)", "Monthly Salary (AED)",
                     "Fixed (AED)", "Variable (AED)"])
    count = 0
    for u in rows:
        basic = u.basic_salary or 0.0
        monthly = u.monthly_salary or 0.0
        variable = max(monthly - basic, 0.0)
        writer.writerow([csv_cell(u.id), csv_cell(u.name),
                         csv_cell(u.iban or ""), csv_cell(u.bank_name or ""),
                         f"{basic:.2f}", f"{monthly:.2f}",
                         f"{basic:.2f}", f"{variable:.2f}"])
        count += 1
    record("wps_export", f"WPS CSV downloaded for {count} active employees",
           entity="payroll", entity_id="wps")
    db.session.commit()
    filename = f"wps-payroll-{date.today().strftime('%Y%m')}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# ---------------- Employee photo upload (HR) ----------------
@hrms_bp.route("/employees/<uid>/photo", methods=["POST"])
@login_required
@hr_required
def employee_photo_upload(uid):
    """HR uploads/updates an employee's photo."""
    from app.files import (valid_photo, store_file, remove_file, PHOTO_DIR)
    user = User.query.get_or_404(uid)
    f = request.files.get("photo")
    data = f.read() if f else b""
    kind, err = valid_photo(data, f.filename if f else "")
    if err:
        flash(err, "error")
    else:
        if user.photo_path:
            remove_file(PHOTO_DIR, user.photo_path)
        user.photo_path = store_file(data, PHOTO_DIR, kind)
        record("photo_uploaded", f"HR uploaded photo for {uid}",
               entity="employee", entity_id=uid)
        db.session.commit()
        flash("Photo updated.", "success")
    return redirect(url_for("hrms.employee_detail", uid=uid))


# ---------------- HR Analytics (V3.10) ----------------
def analytics_access(fn):
    """HR Analytics page: admin + HR Manager + HR Staff (company-wide),
    managers (own department only). Plain employees get 403."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        u = current_user
        if not (u.is_admin or u.is_hr_manager or u.is_hr_staff or u.is_manager):
            abort(403)
        return fn(*a, **kw)
    return wrapper


@hrms_bp.route("/analytics")
@login_required
@analytics_access
def analytics():
    """HR Analytics dashboard: KPI widgets + 8 interactive SVG charts."""
    from app import analytics as an
    from app.files import salary_visible

    today = date.today()
    period = (request.args.get("period") or "12m").lower()
    n_months = 6 if period == "6m" else 12
    period = "6m" if n_months == 6 else "12m"

    hr_wide = bool(current_user.is_admin or current_user.is_hr_manager
                   or current_user.is_hr_staff)
    users = an.base_users(current_user)

    if hr_wide:
        dept = (request.args.get("dept") or "all").strip() or "all"
        all_depts = sorted({(u.department or "").strip() for u in users
                            if (u.department or "").strip()})
        if dept != "all" and dept not in all_depts:
            dept = "all"
    else:
        dept = (current_user.department or "").strip()
        all_depts = [dept] if dept else []

    scoped = an.dept_filtered(users, dept if hr_wide else "all")
    months = an.months_back(n_months)

    # --- 1. Attrition ---
    attr = an.attrition_series(scoped, months)
    sep_map = an.sep_by_code()
    ytd = an.attrition_ytd(scoped, sep_map)
    attr_items = [{
        "label": a["label"], "value": a["ratio"],
        "value_label": an.fmt_pct(a["ratio"]),
        "title": (f"{a['label']}: {a['leavers']} leaver(s), attrition "
                  f"{an.fmt_pct(a['ratio'])} "
                  f"(headcount start {a['hc_start']} / end {a['hc_end']})"),
        "data": {"month": a["period"], "ratio": f"{a['ratio']:.2f}" if a["ratio"] is not None else "",
                 "leavers": str(a["leavers"])},
    } for a in attr]
    attr_svg = an.line_chart(attr_items, aria="Monthly attrition ratio") \
        if any(a["leavers"] for a in attr) else None

    # --- 2. Manpower ---
    mp = an.manpower(scoped)
    company_total = sum(1 for u in users
                        if u.employment_status in an.ACTIVE_STATUSES)
    mp_svg = an.hbars(
        [{"label": d, "value": c, "value_label": an.fmt_int(c),
          "title": f"{d}: {c} active employees",
          "data": {"dept": d, "count": str(c)},
          "href": url_for("hrms.employees", dept=d)}
         for d, c in mp],
        aria="Active headcount by department", link_key="href") if mp else None

    # --- 3. Vital documents ---
    tiers, soonest = an.doc_expiry_data(scoped, today)
    docs_svg = an.stacked_bars(tiers, aria="Document expiries by tier and window") \
        if any(sum(t) for t in tiers) else None
    # Non-expired docs expiring within 30 days (window index 1 = ≤30d).
    docs_30 = sum(tiers[t][1] for t in range(3))
    docs_expired = sum(tiers[t][0] for t in range(3))

    # --- 4. Absenteeism ---
    abs_series, abs_dept, abs_latest = an.absenteeism_data(scoped, months)
    cur_ym = (today.year, today.month)
    abs_this_month = next(
        (s["pct"] for s in abs_series
         if (s["year"], s["month"]) == cur_ym), None)
    abs_items = [{
        "label": s["label"], "value": s["pct"],
        "value_label": an.fmt_pct(s["pct"]),
        "title": (f"{s['label']}: {s['absent']} absent of {s['denom']} "
                  f"attendance records → {an.fmt_pct(s['pct'])}"),
        "data": {"month": s["period"],
                 "pct": f"{s['pct']:.2f}" if s["pct"] is not None else ""},
    } for s in abs_series]
    abs_svg = an.vbars(abs_items, aria="Monthly absenteeism") \
        if any(s["denom"] for s in abs_series) else None
    abs_dept_svg = an.hbars(
        [{"label": d, "value": p, "value_label": an.fmt_pct(p),
          "title": f"{d}: absenteeism {an.fmt_pct(p)}", "data": {"dept": d}}
         for d, p in sorted(abs_dept.items(), key=lambda kv: -kv[1])],
        aria="Absenteeism by department") if abs_dept else None

    # --- 5. Annual vacation ---
    leave = an.leave_data(scoped)
    leave_dept = sorted(leave["by_dept"].items())
    leave_svg = an.donut(leave["pct"] or 0.0, aria="Annual leave utilisation",
                         data={"pct": f"{(leave['pct'] or 0):.2f}"}) \
        if leave["entitled"] else None

    # --- 6. Overtime ---
    runs = an.locked_runs(6)
    ot_per_run, ot_dept = an.ot_data(scoped, runs)
    ot_items = [{
        "label": r["period"], "value": r["hours"],
        "value_label": an.fmt_hours(r["hours"]),
        "title": f"Payroll {r['period']}: {an.fmt_hours(r['hours'])} OT hours",
        "data": {"period": r["period"], "hours": f"{r['hours']:.2f}"},
    } for r in ot_per_run]
    ot_svg = an.vbars(ot_items, aria="Overtime hours per locked payroll run",
                      ) if any(r["hours"] for r in ot_per_run) else None
    ot_dept_svg = an.hbars(
        [{"label": d, "value": h, "value_label": an.fmt_hours(h),
          "title": f"{d}: {an.fmt_hours(h)} OT hours in {runs[-1].period}",
          "data": {"dept": d}}
         for d, h in ot_dept.items()],
        aria="Overtime hours by department") if ot_dept else None
    ot_latest = ot_per_run[-1]["hours"] if ot_per_run else None

    # --- 7. Salary (gated: only when the viewer has salary visibility) ---
    can_salary = bool(salary_visible(current_user))
    sal = an.salary_data(scoped, runs) if can_salary else None
    sal_svg = None
    if sal:
        sal_items = []
        for t in sal["totals"]:
            d = t.get("delta_pct")
            sal_items.append({
                "label": t["period"], "value": t["net"],
                "value_label": (f"{d:+.1f}%" if d is not None else ""),
                "show_value": d is not None,
                "title": (f"Payroll {t['period']}: net {an.fmt_aed(t['net'])}"
                          + (f" ({d:+.1f}% vs previous run)"
                             if d is not None else "")),
                "data": {"period": t["period"], "net": f"{t['net']:.2f}"},
            })
        sal_svg = an.vbars(sal_items, aria="Net payroll per locked run")

    # --- 8. Joiners vs leavers ---
    jl = an.joiners_leavers(scoped, months)
    jl_s1 = {"name": "Joiners", "color": "var(--success)",
             "items": [{
                 "label": s["label"], "value": s["joiners"],
                 "title": f"{s['label']}: {s['joiners']} joiner(s)",
                 "data": {"month": s["period"], "joiners": str(s["joiners"])},
             } for s in jl]}
    jl_s2 = {"name": "Leavers", "color": "var(--danger)",
             "items": [{
                 "label": s["label"], "value": s["leavers"],
                 "title": f"{s['label']}: {s['leavers']} leaver(s)",
                 "data": {"month": s["period"], "leavers": str(s["leavers"])},
             } for s in jl]}
    jl_svg = an.grouped_bars(jl_s1, jl_s2,
                             aria="Joiners vs leavers per month") \
        if any(s["joiners"] or s["leavers"] for s in jl) else None
    jl_totals = {"joiners": sum(s["joiners"] for s in jl),
                 "leavers": sum(s["leavers"] for s in jl)}

    # --- KPI widgets ---
    headcount = sum(1 for u in scoped
                    if u.employment_status in an.ACTIVE_STATUSES)
    cur_joiners = next((s["joiners"] for s in jl
                        if (s["year"], s["month"]) == cur_ym), 0)
    cur_leavers = next((s["leavers"] for s in jl
                        if (s["year"], s["month"]) == cur_ym), 0)

    return render_template(
        "hr_analytics.html",
        period=period, dept=dept, all_depts=all_depts, hr_wide=hr_wide,
        can_salary=can_salary,
        headcount=headcount, ytd=ytd, abs_this_month=abs_this_month,
        ot_latest=ot_latest, docs_30=docs_30, docs_expired=docs_expired,
        cur_joiners=cur_joiners, cur_leavers=cur_leavers,
        attr_svg=attr_svg, attr=attr,
        mp_svg=mp_svg, mp=mp, company_total=company_total,
        docs_svg=docs_svg, tiers=tiers, soonest=soonest,
        tier_labels=an.TIER_LABELS, window_labels=an.WINDOW_LABELS,
        abs_svg=abs_svg, abs_dept_svg=abs_dept_svg, abs_latest=abs_latest,
        abs_series=abs_series,
        leave=leave, leave_svg=leave_svg, leave_dept=leave_dept,
        ot_svg=ot_svg, ot_dept_svg=ot_dept_svg, runs=runs, ot_per_run=ot_per_run,
        sal=sal, sal_svg=sal_svg,
        jl_svg=jl_svg, jl_totals=jl_totals, n_months=n_months,
        scope_note=("own department only" if not hr_wide
                    else ("department: " + dept if dept != "all"
                          else "company-wide")),
    )
