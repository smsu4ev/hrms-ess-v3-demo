"""Payroll module (v3): companies, salary components, OT rules, payroll runs,
WPS per visa company, letters with mail merge.

Access: admin OR can_view_salary (HR Manager assigns salary visibility).
"""
import csv
import io
import json
import re
from datetime import date, datetime

from flask import Blueprint, render_template, request, redirect, url_for, \
    flash, abort, Response
from flask_login import login_required, current_user

from app import db
from app.models import (User, Company, SalaryComponent, SalaryAssignment,
                        OTRateRule, Loan, PayrollInput, PayrollOTHours,
                        PayrollRun, PayrollLine, LetterTemplate,
                        GeneratedLetter, NotificationOutbox, AuditLog)
from app.audit import record, csv_cell
from app import payroll as eng
from app.files import salary_visible

payroll_bp = Blueprint("payroll", __name__, url_prefix="/hrms")


def hr_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not salary_visible(current_user):
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _mgr_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_manager:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _admin_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _strip_pay_amounts(message):
    """Privacy guard: salary amounts must never leave the system inside a
    notification. Strips "Net pay: AED <number>" and bare "AED <number>"
    fragments so a future call site cannot leak pay figures into WhatsApp
    messages. Dates and other digits are left untouched."""
    message = re.sub(r"(?i)\bnet pay\s*:?\s*(?:AED\s*)?[\d,]+(?:\.\d+)?",
                     "net pay [see ESS]", message)
    message = re.sub(r"\bAED\s*[\d,]+(?:\.\d+)?", "AED [hidden]", message)
    return message


def queue_whatsapp(user_id, event, message):
    """Queue a WhatsApp notification (sending activates later).

    Message text is generic alerts only - pay amounts are stripped by
    _strip_pay_amounts before the row is written.
    """
    db.session.add(NotificationOutbox(user_id=user_id, channel="whatsapp",
                                      event=event,
                                      message=_strip_pay_amounts(message),
                                      status="queued"))


# ================= Companies (visa companies) =================

@payroll_bp.route("/companies", methods=["GET", "POST"])
@login_required
@hr_required
def companies():
    if request.method == "POST" and current_user.is_admin:
        code = (request.form.get("code") or "").strip().upper()[:20]
        name = (request.form.get("name") or "").strip()[:120]
        if not code or not name:
            flash("Code and name are required.", "error")
        elif Company.query.filter_by(code=code).first():
            flash(f"Company code {code} already exists.", "error")
        else:
            db.session.add(Company(code=code, name=name))
            record("company_created", f"{code} - {name}",
                   entity="company", entity_id=code)
            db.session.commit()
            flash(f"Company {code} created.", "success")
            return redirect(url_for("payroll.companies"))
    rows = Company.query.order_by(Company.code).all()
    counts = {}
    for c in rows:
        counts[c.id] = User.query.filter_by(visa_company_id=c.id,
                                            is_active=True).count()
    return render_template("hr_companies.html", rows=rows, counts=counts)


@payroll_bp.route("/companies/<int:cid>/toggle", methods=["POST"])
@login_required
@_admin_required
def company_toggle(cid):
    c = Company.query.get_or_404(cid)
    c.is_active = not c.is_active
    record("company_toggled", f"{c.code} active={c.is_active}",
           entity="company", entity_id=c.code)
    db.session.commit()
    return redirect(url_for("payroll.companies"))


# ================= Salary components =================

@payroll_bp.route("/salary-components", methods=["GET", "POST"])
@login_required
@hr_required
def components():
    if request.method == "POST" and current_user.is_admin:
        action = request.form.get("action")
        if action == "add":
            code = (request.form.get("code") or "").strip().upper()[:30]
            name = (request.form.get("name") or "").strip()[:80]
            kind = request.form.get("kind") or "earning"
            if not code or not name:
                flash("Code and name are required.", "error")
            elif not re.match(r"^[A-Z0-9_]+$", code):
                flash("Code may contain only A-Z, 0-9 and _.", "error")
            elif SalaryComponent.query.filter_by(code=code).first():
                flash(f"Component {code} already exists.", "error")
            else:
                db.session.add(SalaryComponent(
                    code=code, name=name,
                    kind="deduction" if kind == "deduction" else "earning",
                    is_fixed=request.form.get("is_fixed") == "on",
                    prorate=request.form.get("prorate") == "on",
                    wps_bucket="basic" if request.form.get("wps_bucket") == "basic"
                    else "variable"))
                record("component_created", f"{code} - {name}",
                       entity="component", entity_id=code)
                db.session.commit()
                flash(f"Component {code} added.", "success")
        elif action == "toggle":
            comp = SalaryComponent.query.get_or_404(
                int(request.form.get("comp_id", 0)))
            used = SalaryAssignment.query.filter_by(
                component_id=comp.id).count()
            if used and comp.is_active:
                # Soft-deactivate: assignments are history; stop future use.
                comp.is_active = False
                record("component_deactivated",
                       f"{comp.code} soft-deactivated ({used} assignments kept)",
                       entity="component", entity_id=comp.code)
                flash(f"{comp.code} deactivated (kept on {used} past "
                      "assignments).", "success")
            else:
                comp.is_active = not comp.is_active
                record("component_toggled",
                       f"{comp.code} active={comp.is_active}",
                       entity="component", entity_id=comp.code)
                flash(f"{comp.code} updated.", "success")
            db.session.commit()
        elif action == "delete":
            comp = SalaryComponent.query.get_or_404(
                int(request.form.get("comp_id", 0)))
            used = SalaryAssignment.query.filter_by(
                component_id=comp.id).count()
            if used:
                flash(f"{comp.code} is used in {used} assignments and cannot "
                      "be deleted. Deactivate it instead.", "error")
            else:
                record("component_deleted", f"{comp.code} - {comp.name}",
                       entity="component", entity_id=comp.code)
                db.session.delete(comp)
                db.session.commit()
                flash(f"{comp.code} deleted.", "success")
        return redirect(url_for("payroll.components"))
    rows = SalaryComponent.query.order_by(SalaryComponent.kind,
                                          SalaryComponent.code).all()
    usage = {c.id: SalaryAssignment.query.filter_by(component_id=c.id).count()
             for c in rows}
    return render_template("hr_components.html", rows=rows, usage=usage)


# ================= Per-employee salary assignment =================

@payroll_bp.route("/employees/<uid>/salary", methods=["GET", "POST"])
@login_required
@hr_required
def employee_salary(uid):
    user = User.query.get_or_404(uid)
    if request.method == "POST":
        if not current_user.is_admin:
            abort(403)
        eff = (request.form.get("effective_from") or "").strip()
        try:
            eff_date = datetime.strptime(eff, "%Y-%m-%d").date()
        except ValueError:
            flash("Effective date is required.", "error")
            return redirect(url_for("payroll.employee_salary", uid=uid))
        note = (request.form.get("note") or "").strip()[:200]
        changed = 0
        for comp in SalaryComponent.query.filter_by(is_active=True).all():
            raw = request.form.get(f"amt_{comp.code}")
            if raw is None or raw == "":
                continue
            try:
                amount = float(raw)
            except ValueError:
                continue
            latest = (SalaryAssignment.query
                      .filter_by(user_id=uid, component_id=comp.id)
                      .order_by(SalaryAssignment.effective_from.desc(),
                                SalaryAssignment.id.desc()).first())
            if latest and latest.effective_from == eff_date \
                    and latest.amount == amount:
                continue
            db.session.add(SalaryAssignment(
                user_id=uid, component_id=comp.id, amount=amount,
                effective_from=eff_date, note=note or None,
                created_by=current_user.id))
            record("salary_assigned",
                   f"{uid} {comp.code} = AED {amount:.2f} from {eff_date}",
                   entity="employee", entity_id=uid, field_name=comp.code,
                   old_value=latest.amount if latest else "",
                   new_value=amount)
            changed += 1
        db.session.commit()
        flash(f"Saved {changed} salary assignment(s).", "success")
        return redirect(url_for("payroll.employee_salary", uid=uid))
    comps = SalaryComponent.query.filter_by(is_active=True)\
        .order_by(SalaryComponent.kind, SalaryComponent.code).all()
    today = date.today()
    current = {code: amt for code, (_r, amt)
               in eng.assignments_as_of(uid, today).items()}
    history = (SalaryAssignment.query.filter_by(user_id=uid)
               .order_by(SalaryAssignment.effective_from.desc(),
                         SalaryAssignment.id.desc()).limit(60).all())
    return render_template("hr_employee_salary.html", user=user, comps=comps,
                           current=current, history=history, today=today)


# ================= OT rate rules =================

@payroll_bp.route("/ot-rules", methods=["GET", "POST"])
@login_required
@hr_required
def ot_rules():
    if request.method == "POST" and current_user.is_admin:
        level = request.form.get("level")
        ref = (request.form.get("ref_id") or "").strip()
        try:
            n = float(request.form.get("normal_mult") or 0)
            ni = float(request.form.get("night_mult") or 0)
            r = float(request.form.get("restday_mult") or 0)
        except ValueError:
            n = ni = r = 0
        if level not in ("company", "group", "individual") or not ref \
                or not (0.5 <= n <= 5 and 0.5 <= ni <= 5 and 0.5 <= r <= 5):
            flash("Valid level, reference and multipliers (0.5-5) required.",
                  "error")
        else:
            rule = OTRateRule.query.filter_by(level=level, ref_id=ref).first()
            if not rule:
                rule = OTRateRule(level=level, ref_id=ref)
                db.session.add(rule)
            rule.normal_mult, rule.night_mult, rule.restday_mult = n, ni, r
            rule.updated_by = current_user.id
            record("ot_rule_saved",
                   f"{level}/{ref}: normal {n}x night {ni}x restday {r}x",
                   entity="ot_rule", entity_id=f"{level}:{ref}")
            db.session.commit()
            flash("OT rule saved.", "success")
        return redirect(url_for("payroll.ot_rules"))
    rules = OTRateRule.query.order_by(OTRateRule.level, OTRateRule.ref_id).all()
    companies = Company.query.filter_by(is_active=True).order_by(Company.code).all()
    depts = sorted({u.department for u in User.query.all() if u.department})
    employees = User.query.filter_by(is_active=True).order_by(User.name).all()
    return render_template("hr_ot_rules.html", rules=rules,
                           defaults=eng.OT_DEFAULTS, companies=companies,
                           depts=depts, employees=employees)


# ================= Loans =================

@payroll_bp.route("/loans", methods=["GET", "POST"])
@login_required
@hr_required
def loans():
    if request.method == "POST" and current_user.is_admin:
        action = request.form.get("action")
        if action == "add":
            uid = (request.form.get("user_id") or "").strip()
            ltype = request.form.get("loan_type") or "loan"
            try:
                principal = float(request.form.get("principal") or 0)
                inst = float(request.form.get("installment") or 0)
            except ValueError:
                principal, inst = 0, 0
            start = (request.form.get("start_period") or "").strip()
            target = User.query.get(uid)
            if not target or principal <= 0 or inst <= 0 or len(start) != 7:
                flash("Valid employee, principal, installment and start "
                      "period (YYYY-MM) required.", "error")
            else:
                loan = Loan(user_id=uid, loan_type=ltype, principal=principal,
                            installment=inst, start_period=start,
                            balance=principal, note=(request.form.get("note")
                                                     or "").strip()[:200],
                            created_by=current_user.id)
                db.session.add(loan)
                record("loan_created",
                       f"{ltype} AED {principal:.2f} for {uid}, "
                       f"{inst:.2f}/month from {start}",
                       entity="loan", entity_id=uid)
                db.session.commit()
                flash("Loan recorded.", "success")
        elif action in ("pause", "resume"):
            loan = Loan.query.get_or_404(int(request.form.get("loan_id", 0)))
            loan.status = "paused" if action == "pause" else "active"
            record("loan_status", f"loan #{loan.id} -> {loan.status}",
                   entity="loan", entity_id=loan.user_id)
            db.session.commit()
            flash(f"Loan {loan.status}.", "success")
        return redirect(url_for("payroll.loans"))
    rows = Loan.query.order_by(Loan.status, Loan.created_at.desc()).all()
    employees = User.query.filter_by(is_active=True).order_by(User.name).all()
    users = {u.id: u for u in User.query.all()}
    return render_template("hr_loans.html", rows=rows, employees=employees,
                           users=users)


# ================= Payroll runs =================

@payroll_bp.route("/payroll")
@login_required
@hr_required
def runs():
    rows = PayrollRun.query.order_by(PayrollRun.period.desc()).all()
    return render_template("hr_payroll_runs.html", runs=rows)


@payroll_bp.route("/payroll/new", methods=["POST"])
@login_required
@_admin_required
def run_new():
    period = (request.form.get("period") or "").strip()
    try:
        eng.parse_period(period)
    except ValueError:
        flash("Period must be YYYY-MM.", "error")
        return redirect(url_for("payroll.runs"))
    if PayrollRun.query.filter_by(period=period).first():
        flash(f"A payroll run for {period} already exists.", "error")
        return redirect(url_for("payroll.runs"))
    run = PayrollRun(period=period, status="draft",
                     created_by=current_user.id)
    db.session.add(run)
    db.session.flush()
    eng.recalc_run(run)
    record("payroll_run_created", f"period {period}",
           entity="payroll_run", entity_id=run.id)
    db.session.commit()
    flash(f"Draft payroll run {period} created and calculated.", "success")
    return redirect(url_for("payroll.run_detail", run_id=run.id))


@payroll_bp.route("/payroll/<int:run_id>")
@login_required
@hr_required
def run_detail(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    record("payroll_run_viewed", f"period {run.period} ({run.status})",
           entity="payroll_run", entity_id=run.id)
    db.session.commit()
    lines = (PayrollLine.query.filter_by(run_id=run.id)
             .order_by(PayrollLine.user_id).all())
    users = {u.id: u for u in User.query.all()}
    totals = {"gross": 0.0, "deductions": 0.0, "net": 0.0,
              "count": len(lines)}
    for l in lines:
        totals["gross"] += l.gross
        totals["deductions"] += l.total_deductions
        totals["net"] += l.net
    return render_template("hr_payroll_run.html", run=run, lines=lines,
                           users=users, totals=totals)


@payroll_bp.route("/payroll/<int:run_id>/recalc", methods=["POST"])
@login_required
@_admin_required
def run_recalc(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    if run.status not in ("draft", "in_review"):
        # State-machine fix: locked/approved runs are frozen; recalc after
        # unlock requires going back to draft first.
        flash(f"Recalculation is not allowed on a {run.status} run. "
              "Unlock it back to draft first.", "error")
        return redirect(url_for("payroll.run_detail", run_id=run_id))
    eng.recalc_run(run)
    record("payroll_recalculated", f"period {run.period}",
           entity="payroll_run", entity_id=run.id)
    db.session.commit()
    flash("Payroll recalculated.", "success")
    return redirect(url_for("payroll.run_detail", run_id=run_id))


@payroll_bp.route("/payroll/<int:run_id>/inputs", methods=["GET", "POST"])
@login_required
@hr_required
def run_inputs(run_id):
    """OT hours + manual additions/deductions/arrears/LOP for a run."""
    run = PayrollRun.query.get_or_404(run_id)
    if request.method == "POST":
        if run.status == "locked":
            flash("Run is locked.", "error")
            return redirect(url_for("payroll.run_inputs", run_id=run_id))
        if not current_user.is_admin:
            abort(403)
        action = request.form.get("action")
        if action == "ot":
            uid = (request.form.get("user_id") or "").strip()
            cat = request.form.get("category") or "normal"
            try:
                hours = float(request.form.get("hours") or 0)
            except ValueError:
                hours = 0
            if not User.query.get(uid) or hours <= 0 \
                    or cat not in eng.OT_CATEGORIES:
                flash("Valid employee, category and hours required.", "error")
            else:
                db.session.add(PayrollOTHours(run_id=run.id, user_id=uid,
                                              category=cat, hours=hours))
                db.session.commit()
                flash(f"OT recorded: {uid} {hours}h ({cat}).", "success")
        elif action == "input":
            uid = (request.form.get("user_id") or "").strip()
            label = (request.form.get("label") or "").strip()[:80]
            kind = request.form.get("kind") or "addition"
            try:
                amount = float(request.form.get("amount") or 0)
            except ValueError:
                flash("Amount must be a number.", "error")
                return redirect(url_for("payroll.run_inputs", run_id=run_id))
            days_raw = (request.form.get("days") or "").strip()
            try:
                days = float(days_raw) if days_raw else None
            except ValueError:
                flash("LOP days must be a number.", "error")
                return redirect(url_for("payroll.run_inputs", run_id=run_id))
            is_arrears = request.form.get("is_arrears") == "on"
            orig = (request.form.get("original_period") or "").strip()
            comp = None
            comp_code = (request.form.get("component") or "").strip()
            if comp_code:
                comp = SalaryComponent.query.filter_by(code=comp_code).first()
            target = User.query.get(uid)
            if not target or not label or (amount == 0 and not days) \
                    or kind not in ("addition", "deduction"):
                flash("Valid employee, label, amount (or LOP days) required.",
                      "error")
            else:
                db.session.add(PayrollInput(
                    period=run.period, user_id=uid,
                    component_id=comp.id if comp else None,
                    label=label, amount=amount, days=days, kind=kind,
                    is_arrears=is_arrears, original_period=orig or None,
                    note=(request.form.get("note") or "").strip()[:200],
                    created_by=current_user.id))
                db.session.commit()
                flash(f"{kind.title()} recorded for {uid}.", "success")
        elif action == "del_input":
            inp = PayrollInput.query.get_or_404(
                int(request.form.get("input_id", 0)))
            if inp.period != run.period:
                abort(400)
            record("payroll_input_deleted",
                   f"{inp.kind} {inp.label} AED {inp.amount:.2f} for "
                   f"{inp.user_id} ({inp.period})",
                   entity="payroll_input", entity_id=inp.user_id)
            db.session.delete(inp)
            db.session.commit()
            flash("Input deleted.", "success")
        elif action == "del_ot":
            ot = PayrollOTHours.query.get_or_404(
                int(request.form.get("ot_id", 0)))
            if ot.run_id != run.id:
                abort(400)
            db.session.delete(ot)
            db.session.commit()
            flash("OT entry deleted.", "success")
        return redirect(url_for("payroll.run_inputs", run_id=run_id))
    employees = User.query.filter_by(is_active=True).order_by(User.name).all()
    ot_rows = PayrollOTHours.query.filter_by(run_id=run.id).all()
    inputs = (PayrollInput.query.filter_by(period=run.period)
              .order_by(PayrollInput.user_id).all())
    comps = SalaryComponent.query.filter_by(is_active=True)\
        .order_by(SalaryComponent.code).all()
    return render_template("hr_payroll_inputs.html", run=run,
                           employees=employees, ot_rows=ot_rows,
                           inputs=inputs, comps=comps)


@payroll_bp.route("/payroll/<int:run_id>/review")
@login_required
@hr_required
def run_review(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    record("payroll_review_viewed", f"period {run.period} ({run.status})",
           entity="payroll_run", entity_id=run.id)
    db.session.commit()
    exceptions = eng.run_exceptions(run)
    variance = eng.run_variance(run)
    insights = eng.payroll_insights(run)
    users = {u.id: u for u in User.query.all()}
    return render_template("hr_payroll_review.html", run=run,
                           exceptions=exceptions, variance=variance,
                           insights=insights, users=users,
                           insight_label=eng.INSIGHT_LABEL)


@payroll_bp.route("/payroll/<int:run_id>/submit", methods=["POST"])
@login_required
@_admin_required
def run_submit(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    if run.status != "draft":
        flash("Only draft runs can be submitted.", "error")
    else:
        run.status = "in_review"
        record("payroll_submitted", f"period {run.period} -> in review",
               entity="payroll_run", entity_id=run.id)
        db.session.commit()
        flash("Run submitted for review.", "success")
    return redirect(url_for("payroll.run_detail", run_id=run_id))


@payroll_bp.route("/payroll/<int:run_id>/lock", methods=["POST"])
@login_required
@_admin_required
def run_lock(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    if run.status == "locked":
        flash("Already locked.", "error")
    elif run.status != "in_review":
        # State-machine fix: runs must go through review before locking;
        # locking straight from draft is not allowed.
        flash(f"Only a run in review can be locked (this one is "
              f"{run.status}). Submit it for review first.", "error")
    else:
        run.status = "locked"
        run.locked_at = datetime.utcnow()
        eng.apply_loan_recovery(run)  # balances reduce on lock, never on draft
        record("payroll_locked", f"period {run.period} locked; loan balances "
                                 "applied", entity="payroll_run", entity_id=run.id)
        db.session.commit()
        flash("Run locked. Loan balances updated.", "success")
    return redirect(url_for("payroll.run_detail", run_id=run_id))


@payroll_bp.route("/payroll/<int:run_id>/approve", methods=["POST"])
@login_required
@_admin_required
def run_approve(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    if run.status != "locked":
        flash("Only a locked run can be approved.", "error")
    elif run.created_by == current_user.id:
        # SoD: the creator may not approve their own run.
        flash("Segregation of duties: the run creator cannot approve it. "
              "Another admin must approve.", "error")
    else:
        run.status = "approved"
        run.approved_by = current_user.id
        run.approved_at = datetime.utcnow()
        record("payroll_approved", f"period {run.period} approved by "
               f"{current_user.id}", entity="payroll_run", entity_id=run.id)
        # Queue WhatsApp notifications (integration-ready; sending later).
        # Generic alert only - no pay amounts (privacy).
        for line in PayrollLine.query.filter_by(run_id=run.id).all():
            queue_whatsapp(line.user_id, "payslip_ready",
                           f"Your salary for {run.period} has been processed. "
                           "View your payslip in the ESS portal.")
        db.session.commit()
        flash("Run approved. Payslips released; WhatsApp notifications "
              "queued.", "success")
    return redirect(url_for("payroll.run_detail", run_id=run_id))


@payroll_bp.route("/payroll/<int:run_id>/unlock", methods=["POST"])
@login_required
@_admin_required
def run_unlock(run_id):
    run = PayrollRun.query.get_or_404(run_id)
    if run.status not in ("locked", "in_review"):
        flash("Nothing to unlock.", "error")
    else:
        was_locked = run.status == "locked"
        if was_locked:
            # Reverse this run's loan recoveries so a later re-lock applies
            # exactly one recovery per loan (idempotency rows are kept and
            # marked reversed for the audit trail).
            eng.reverse_loan_recovery(run)
            run.locked_at = None
        run.status = "draft"
        record("payroll_unlocked", f"period {run.period} unlocked back to "
               "draft by admin"
               + ("; this run's loan recoveries reversed" if was_locked else ""),
               entity="payroll_run", entity_id=run.id)
        db.session.commit()
        flash("Run unlocked back to draft (audited).", "success")
    return redirect(url_for("payroll.run_detail", run_id=run_id))


# ================= WPS files per visa company =================

@payroll_bp.route("/wps")
@login_required
@hr_required
def wps():
    """WPS working files, one per visa company, for a locked/approved run."""
    period = (request.args.get("period") or "").strip()
    run = None
    groups = {}
    if period:
        try:
            eng.parse_period(period)
            run = PayrollRun.query.filter_by(period=period).first()
        except ValueError:
            flash("Period must be YYYY-MM.", "error")
    if run:
        if run.status not in ("locked", "approved"):
            flash(f"Run {period} is {run.status}; lock it before generating "
                  "WPS files.", "error")
        else:
            groups = eng.wps_by_company(run)
            record("wps_page_viewed", f"period {period} ({run.status})",
                   entity="payroll_run", entity_id=run.id)
            db.session.commit()
    runs = PayrollRun.query.order_by(PayrollRun.period.desc()).all()
    return render_template("hr_wps.html", runs=runs, run=run, groups=groups,
                           period=period)


@payroll_bp.route("/wps/<company_code>/<period>")
@login_required
@hr_required
def wps_export_company(company_code, period):
    """Per-company WPS working CSV.

    This is the working file for the WPS agent (LuLu Exchange); it is NOT
    LuLu's exact SIF format - that specification is pending from LuLu.
    """
    try:
        eng.parse_period(period)
    except ValueError:
        abort(404)
    run = PayrollRun.query.filter_by(period=period).first_or_404()
    if run.status not in ("locked", "approved"):
        flash("Run must be locked before WPS export.", "error")
        return redirect(url_for("payroll.wps", period=period))
    company = Company.query.filter_by(code=company_code.upper()).first_or_404()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["# WPS working file - NOT the official LuLu Exchange SIF format.",
                     f"Company: {company.code} {company.name}",
                     f"Period: {period}", f"Run: {run.status}"])
    writer.writerow(["Employee ID", "Name", "Emirates ID", "IBAN", "Bank",
                     "Basic (AED)", "Variable (AED)", "Total Net (AED)",
                     "Payable Days"])
    total_net = 0.0
    count = 0
    for line in (PayrollLine.query.filter_by(run_id=run.id)
                 .order_by(PayrollLine.user_id).all()):
        user = User.query.get(line.user_id)
        if getattr(user, "visa_company_id", None) != company.id:
            continue
        earnings = json.loads(line.earnings_json)
        variable = round(sum(e["amount"] for e in earnings
                             if e.get("code") not in ("BASIC", "OVERTIME")), 2)
        writer.writerow([csv_cell(user.id), csv_cell(user.name),
                         csv_cell(user.emirates_id_no or ""),
                         csv_cell(user.iban or ""), csv_cell(user.bank_name or ""),
                         f"{line.basic_earned:.2f}", f"{variable:.2f}",
                         f"{line.net:.2f}", f"{line.payable_days:.1f}"])
        total_net += line.net
        count += 1
    writer.writerow([])
    writer.writerow(["Employee count", count, "Total Net (AED)",
                     f"{total_net:.2f}"])
    record("wps_export_company",
           f"WPS working CSV for {company.code} {period}: {count} employees, "
           f"AED {total_net:.2f}",
           entity="payroll", entity_id=f"{company.code}/{period}")
    db.session.commit()
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=wps-{company.code.lower()}"
                             f"-{period.replace('-', '')}.csv"})


# ================= Letters + mail merge =================

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
SALARY_PLACEHOLDERS = {"basic_salary", "monthly_salary"}


def merge_placeholders(body, user):
    """Mail-merge a letter template. Salary figures prefer the current
    SalaryAssignment values (component-based), falling back to the legacy
    User.basic_salary / User.monthly_salary fields for old records."""
    comp = None
    if getattr(user, "visa_company_id", None):
        comp = Company.query.get(user.visa_company_id)
    assigns = eng.assignments_as_of(user.id, date.today())
    if "BASIC" in assigns:
        basic = assigns["BASIC"][1]
    else:
        basic = user.basic_salary or 0.0
    earning_total = 0.0
    for row, amount in assigns.values():
        if row.component and row.component.kind == "earning" \
                and row.component.is_active:
            earning_total += amount or 0.0
    monthly = earning_total if assigns else (user.monthly_salary or 0.0)
    values = {
        "employee_name": user.name or "",
        "employee_id": user.id or "",
        "designation": user.designation or "",
        "department": user.department or "",
        "company": comp.name if comp else "",
        "joining_date": user.joining_date.isoformat()
        if user.joining_date else "",
        "basic_salary": f"{basic:.2f}",
        "monthly_salary": f"{monthly:.2f}",
        "date": date.today().isoformat(),
    }

    def repl(m):
        return values.get(m.group(1), "")

    return PLACEHOLDER_RE.sub(repl, body)


@payroll_bp.route("/letters")
@login_required
@_mgr_required
def letters():
    q = GeneratedLetter.query
    if not salary_visible(current_user):
        # Users without salary visibility never see letters carrying salary
        # figures (no read-through via the list or ID guessing).
        q = q.filter(GeneratedLetter.has_salary_data.is_(False))
    rows = q.order_by(GeneratedLetter.created_at.desc()).limit(100).all()
    users = {u.id: u for u in User.query.all()}
    templates = LetterTemplate.query.filter_by(is_active=True)\
        .order_by(LetterTemplate.name).all()
    return render_template("hr_letters.html", rows=rows, users=users,
                           templates=templates)


@payroll_bp.route("/letters/new", methods=["GET", "POST"])
@login_required
@_mgr_required
def letter_new():
    employees = User.query.filter_by(is_active=True).order_by(User.name).all()
    templates = LetterTemplate.query.filter_by(is_active=True)\
        .order_by(LetterTemplate.name).all()
    if request.method == "POST":
        uid = (request.form.get("user_id") or "").strip()
        tid = int(request.form.get("template_id") or 0)
        user = User.query.get(uid)
        tpl = LetterTemplate.query.get(tid)
        if not user or not tpl:
            flash("Select an employee and a template.", "error")
        else:
            used = set(PLACEHOLDER_RE.findall(tpl.body))
            if used & SALARY_PLACEHOLDERS \
                    and not salary_visible(current_user):
                flash("This letter contains salary figures. Salary "
                      "visibility is required (assigned by the HR Manager).",
                      "error")
            else:
                rendered = merge_placeholders(tpl.body, user)
                letter = GeneratedLetter(
                    user_id=uid, template_id=tid,
                    rendered_text=rendered,
                    has_salary_data=bool(used & SALARY_PLACEHOLDERS),
                    generated_by=current_user.id)
                db.session.add(letter)
                record("letter_generated", f"{tpl.name} for {uid}",
                       entity="letter", entity_id=uid,
                       field_name=tpl.name)
                db.session.commit()
                flash(f"{tpl.name} generated.", "success")
                return redirect(url_for("payroll.letter_view",
                                        letter_id=letter.id))
    return render_template("hr_letter_new.html", employees=employees,
                           templates=templates)


@payroll_bp.route("/letters/<int:letter_id>")
@login_required
@_mgr_required
def letter_view(letter_id):
    letter = GeneratedLetter.query.get_or_404(letter_id)
    if letter.has_salary_data and not salary_visible(current_user):
        # IDOR fix: salary letters are only viewable with salary permission.
        abort(403)
    record("letter_viewed", f"{letter.template.name} for {letter.user_id}",
           entity="letter", entity_id=letter.id, field_name=letter.template.name)
    db.session.commit()
    user = User.query.get(letter.user_id)
    return render_template("hr_letter_view.html", letter=letter, user=user)


@payroll_bp.route("/letters/templates", methods=["GET", "POST"])
@login_required
@_mgr_required
def letter_templates():
    if request.method == "POST" and current_user.is_admin:
        if request.form.get("action") == "add":
            name = (request.form.get("name") or "").strip()[:80]
            if name and not LetterTemplate.query.filter_by(name=name).first():
                db.session.add(LetterTemplate(
                    name=name, body=request.form.get("body") or "",
                    updated_by=current_user.id))
                record("letter_template_created", name,
                       entity="letter_template")
                db.session.commit()
                flash("Template created.", "success")
            return redirect(url_for("payroll.letter_templates"))
        tid = int(request.form.get("template_id") or 0)
        tpl = LetterTemplate.query.get_or_404(tid)
        tpl.body = request.form.get("body") or ""
        tpl.is_active = request.form.get("is_active") == "on"
        tpl.updated_by = current_user.id
        record("letter_template_updated", tpl.name,
               entity="letter_template", entity_id=tid)
        db.session.commit()
        flash("Template updated.", "success")
        return redirect(url_for("payroll.letter_templates"))
    rows = LetterTemplate.query.order_by(LetterTemplate.name).all()
    return render_template("hr_letter_templates.html", rows=rows)
