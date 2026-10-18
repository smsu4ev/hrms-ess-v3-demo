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


EMPLOYEE_FIELDS = ["name", "department", "designation", "reporting_manager",
                   "joining_date", "employment_status", "nationality",
                   "visa_expiry_date", "passport_expiry_date",
                   "emirates_id_expiry_date", "emirates_id_no", "passport_no",
                   "email", "mobile", "phone", "iban", "bank_name",
                   "visa_company_id", "exit_date",
                   "separation_date", "separation_type",  # v3.1
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
    rows = query.order_by(User.name).limit(300).all()
    departments = sorted({u.department for u in User.query.all() if u.department})
    return render_template("hr_employees.html", rows=rows, q=q, dept=dept,
                           status=status, departments=departments,
                           statuses=EMPLOYMENT_STATUSES)


@hrms_bp.route("/employees/new", methods=["GET", "POST"])
@login_required
@hr_required
@admin_post_only
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
            user = User(id=uid, name=name,
                        department=(request.form.get("department") or "").strip() or None,
                        designation=(request.form.get("designation") or "").strip() or None,
                        reporting_manager=(request.form.get("reporting_manager") or "").strip() or None,
                        joining_date=parse_date(request.form.get("joining_date")),
                        employment_status=request.form.get("employment_status") or "active",
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
                        basic_salary=float(request.form.get("basic_salary") or 0),
                        monthly_salary=float(request.form.get("monthly_salary") or 0))
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
            flash(f"Employee {uid} created. Temporary password: {temp_pw} "
                  "- share it securely; the employee must change it on login.", "success")
            return redirect(url_for("hrms.employee_detail", uid=uid))
    managers = User.query.filter(User.role.in_(("manager", "admin")),
                                 User.is_active.is_(True)).order_by(User.name).all()
    return render_template("hr_employee_form.html", user=None, error=error,
                           statuses=EMPLOYMENT_STATUSES, managers=managers,
                           companies=companies)


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
