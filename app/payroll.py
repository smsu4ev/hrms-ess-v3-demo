"""Payroll engine for HRMS+ESS v3.

Single pay group: one payroll run for everyone at the same time (period
YYYY-MM). WPS files are generated split by each employee's visa company.

Design notes (from the research blueprint):
- Component-based salary structures with effective-dated assignments.
- OT on BASIC salary; hourly rate = monthly basic / 30 / 8; multipliers
  resolved individual > group(department) > company > system default
  (normal 1.25, night 1.50, rest-day 1.50).
- Payable days = days in month - LOP days (approved unpaid leave in the
  period + manual LOP input); mid-month joiner/exit proration on the
  payable-days/30 basis for fixed components (variable components are full
  unless flagged prorate).
- Loans/advances auto-recover (capped at balance); balance reduces on run
  lock (never on draft).
- SoD: approver may not be the run creator.
"""
import calendar as calmod
import json
from datetime import date

from app import db
from app.models import (User, LeaveRequest, LeaveBalance, SalaryComponent,
                        SalaryAssignment, OTRateRule, Loan, PayrollInput,
                        PayrollOTHours, PayrollRun, PayrollLine, Company,
                        PayrollLoanRecovery)

# System default OT multipliers: normal 1.25, night 1.50, rest-day 1.50
OT_DEFAULTS = {"normal": 1.25, "night": 1.50, "restday": 1.50}
OT_CATEGORIES = ["normal", "night", "restday"]


def parse_period(period):
    """'YYYY-MM' -> (year, month). Raises ValueError on bad input."""
    y, m = period.split("-")
    y, m = int(y), int(m)
    if not (2000 <= y <= 2100 and 1 <= m <= 12):
        raise ValueError("bad period")
    return y, m


def period_start(period):
    y, m = parse_period(period)
    return date(y, m, 1)


def period_end(period):
    y, m = parse_period(period)
    return date(y, m, calmod.monthrange(y, m)[1])


def days_in_period(period):
    y, m = parse_period(period)
    return calmod.monthrange(y, m)[1]


# ---------------- Salary assignments ----------------

def assignments_as_of(user_id, as_of):
    """Latest effective assignment per component for a user.

    Returns {code: (SalaryAssignment, amount)}.
    """
    rows = (SalaryAssignment.query
            .filter(SalaryAssignment.user_id == user_id,
                    SalaryAssignment.effective_from <= as_of)
            .order_by(SalaryAssignment.effective_from.desc(),
                      SalaryAssignment.id.desc()).all())
    out = {}
    for row in rows:
        code = row.component.code if row.component else None
        if code and code not in out:
            out[code] = (row, row.amount)
    return out


def basic_salary(user_id, as_of):
    """Current BASIC salary from component assignments, falling back to the
    legacy User.basic_salary field so old records keep working."""
    a = assignments_as_of(user_id, as_of)
    if "BASIC" in a:
        return a[1][1]
    user = User.query.get(user_id)
    return (user.basic_salary or 0.0) if user else 0.0


# ---------------- OT rate resolution ----------------

def resolve_ot_mult(user_id, category):
    """Resolve the OT multiplier for a user + category.

    Precedence: individual > group(department) > company > system default.
    Returns (multiplier, source_label).
    """
    if category not in OT_CATEGORIES:
        category = "normal"
    user = User.query.get(user_id)
    col = {"normal": "normal_mult", "night": "night_mult",
           "restday": "restday_mult"}[category]

    # 1. individual
    rule = OTRateRule.query.filter_by(level="individual", ref_id=user_id).first()
    if rule:
        return getattr(rule, col), "individual"
    # 2. group = department
    if user and user.department:
        rule = OTRateRule.query.filter_by(level="group",
                                          ref_id=user.department).first()
        if rule:
            return getattr(rule, col), "group"
    # 3. company = visa company code
    if user and getattr(user, "visa_company_id", None):
        company = Company.query.get(user.visa_company_id)
        if company:
            rule = OTRateRule.query.filter_by(level="company",
                                              ref_id=company.code).first()
            if rule:
                return getattr(rule, col), "company"
    # 4. system default
    return OT_DEFAULTS[category], "system default"


# ---------------- Payroll line calculation ----------------

def _approved_unpaid_days(user_id, period):
    """Approved unpaid-leave days inside a payroll period (LOP source)."""
    y, m = parse_period(period)
    total = 0.0
    rows = LeaveRequest.query.filter_by(user_id=user_id, status="approved",
                                        leave_type="unpaid").all()
    for r in rows:
        overlap_start = max(r.start_date, date(y, m, 1))
        overlap_end = min(r.end_date, period_end(period))
        if overlap_end >= overlap_start:
            total += (overlap_end - overlap_start).days + 1
    return total


def _manual_inputs(user_id, period):
    return PayrollInput.query.filter_by(user_id=user_id, period=period).all()


def calculate_payroll_line(user, period):
    """Build the calculated payroll line for one employee + period.

    Returns a dict with earnings/deductions/ot breakdowns plus exceptions.
    """
    dim = days_in_period(period)
    p_end = period_end(period)
    exceptions = []

    assigns = assignments_as_of(user.id, p_end)
    if "BASIC" not in assigns and not (user.basic_salary or 0):
        exceptions.append("missing salary assignment (no BASIC)")

    basic_full = assigns["BASIC"][1] if "BASIC" in assigns \
        else (user.basic_salary or 0.0)

    # ---- LOP / payable days ----
    lop_days = _approved_unpaid_days(user.id, period)
    manual_lop = 0.0
    for inp in _manual_inputs(user.id, period):
        if inp.label.strip().upper() == "LOP" and inp.days:
            manual_lop += inp.days
    lop_days += manual_lop
    payable = dim - lop_days
    # mid-month joiner proration
    if user.joining_date and user.joining_date > date(period_start(period).year,
                                                      period_start(period).month, 1) \
            and user.joining_date <= p_end:
        payable = min(payable, dim - user.joining_date.day + 1)
    # mid-month exit proration (optional exit_date field)
    exit_date = getattr(user, "exit_date", None)
    if exit_date and date(period_start(period).year, period_start(period).month, 1) \
            <= exit_date <= p_end:
        payable = min(payable, exit_date.day)
    payable = max(payable, 0)
    factor = payable / 30.0

    earnings, deductions, ot_lines = [], [], []

    # ---- component earnings / deductions ----
    for code, (_row, amount) in sorted(assigns.items()):
        comp = _row.component
        if not comp or not comp.is_active or code == "BASIC":
            continue
        eff = amount * factor if (comp.prorate) else amount
        entry = {"code": code, "name": comp.name, "amount": round(eff, 2)}
        if comp.kind == "earning":
            earnings.append(entry)
        else:
            deductions.append(entry)

    basic_earned = round(basic_full * factor, 2)

    # ---- overtime (on BASIC) ----
    hourly = basic_full / 30.0 / 8.0 if basic_full else 0.0
    ot_amount_total = 0.0
    for ot in PayrollOTHours.query.join(PayrollRun).filter(
            PayrollRun.period == period,
            PayrollOTHours.user_id == user.id).all():
        mult, src = resolve_ot_mult(user.id, ot.category)
        amt = round(ot.hours * hourly * mult, 2)
        ot_amount_total += amt
        ot_lines.append({"category": ot.category, "hours": ot.hours,
                         "multiplier": mult, "source": src,
                         "amount": amt})
    if ot_amount_total:
        earnings.append({"code": "OVERTIME", "name": "Overtime",
                         "amount": round(ot_amount_total, 2)})

    # ---- manual inputs: additions / deductions / arrears ----
    for inp in _manual_inputs(user.id, period):
        if inp.label.strip().upper() == "LOP":
            continue  # LOP days already reflected in payable days
        label = inp.label
        if inp.is_arrears:
            label += f" (Arrears {inp.original_period or ''})".strip()
        entry = {"code": inp.component.code if inp.component else inp.label,
                 "name": label, "amount": round(inp.amount, 2)}
        if inp.kind == "addition":
            earnings.append(entry)
        else:
            deductions.append(entry)

    # ---- loan / advance recovery (capped at balance) ----
    for loan in Loan.query.filter_by(user_id=user.id, status="active").all():
        if loan.start_period and loan.start_period > period:
            continue
        rec = min(loan.installment, loan.balance)
        if rec > 0:
            deductions.append({
                "code": "LOAN_RECOVERY" if loan.loan_type == "loan"
                else "ADVANCE_RECOVERY",
                "name": f"{'Loan' if loan.loan_type == 'loan' else 'Advance'} "
                        f"recovery (loan #{loan.id}, balance AED {loan.balance:.2f})",
                "amount": round(rec, 2), "loan_id": loan.id})

    gross = round(basic_earned + sum(e["amount"] for e in earnings), 2)
    total_ded = round(sum(d["amount"] for d in deductions), 2)
    net = round(gross - total_ded, 2)

    # ---- exceptions ----
    if not user.iban:
        exceptions.append("missing bank IBAN")
    if not getattr(user, "visa_company_id", None):
        exceptions.append("missing visa company")
    if net < 0:
        exceptions.append("negative net pay")

    return {
        "user_id": user.id, "payable_days": payable, "lop_days": lop_days,
        "basic_earned": basic_earned,
        "earnings": earnings, "deductions": deductions, "ot": ot_lines,
        "gross": gross, "total_deductions": total_ded, "net": net,
        "exceptions": exceptions,
    }


def employees_in_run():
    """Everyone included in a run: active users (single pay group)."""
    return User.query.filter(User.is_active.is_(True)).order_by(User.name).all()


def recalc_run(run):
    """(Re)build all payroll lines for a draft/in_review run."""
    PayrollLine.query.filter_by(run_id=run.id).delete()
    for user in employees_in_run():
        calc = calculate_payroll_line(user, run.period)
        db.session.add(PayrollLine(
            run_id=run.id, user_id=user.id,
            payable_days=calc["payable_days"], lop_days=calc["lop_days"],
            basic_earned=calc["basic_earned"],
            earnings_json=json.dumps(calc["earnings"]),
            deductions_json=json.dumps(calc["deductions"]),
            ot_json=json.dumps(calc["ot"]),
            gross=calc["gross"], total_deductions=calc["total_deductions"],
            net=calc["net"]))
    db.session.flush()


def apply_loan_recovery(run):
    """Reduce loan balances for a run being LOCKED (never on draft).

    Idempotent: one PayrollLoanRecovery row is written per (run, loan) and a
    loan that already has a row for this run is skipped, so a
    lock -> unlock -> lock cycle can never double-recover.
    Uses the recovery amounts already frozen in the payroll lines.
    """
    for line in PayrollLine.query.filter_by(run_id=run.id).all():
        for d in json.loads(line.deductions_json):
            loan_id = d.get("loan_id")
            if not loan_id:
                continue
            existing = PayrollLoanRecovery.query.filter_by(
                run_id=run.id, loan_id=loan_id, reversed=False).first()
            if existing:
                continue
            loan = Loan.query.get(loan_id)
            if not loan or loan.status != "active":
                continue
            amount = d["amount"]
            loan.balance = round(max(loan.balance - amount, 0.0), 2)
            if loan.balance <= 0.01:
                loan.status = "closed"
            db.session.add(PayrollLoanRecovery(run_id=run.id, loan_id=loan_id,
                                               amount=amount))
    db.session.flush()


def reverse_loan_recovery(run):
    """Undo the recoveries applied when a run was LOCKED.

    Restores each loan's balance from this run's recovery rows and marks the
    rows reversed (kept for the audit trail). A re-locked run writes fresh
    recovery rows, so the balance reflects exactly one recovery.
    """
    for rec in PayrollLoanRecovery.query.filter_by(run_id=run.id,
                                                   reversed=False).all():
        loan = Loan.query.get(rec.loan_id)
        if loan:
            loan.balance = round(loan.balance + rec.amount, 2)
            if loan.status == "closed" and loan.balance > 0.01:
                loan.status = "active"
        rec.reversed = True
    db.session.flush()


def run_exceptions(run):
    """Aggregate exception flags for a run: {user_id: [messages]}."""
    out = {}
    for line in PayrollLine.query.filter_by(run_id=run.id).all():
        user = User.query.get(line.user_id)
        msgs = []
        if not user.iban:
            msgs.append("Missing bank IBAN")
        if not getattr(user, "visa_company_id", None):
            msgs.append("Missing visa company")
        assigns = assignments_as_of(user.id, period_end(run.period))
        if "BASIC" not in assigns and not (user.basic_salary or 0):
            msgs.append("Missing salary assignment (no BASIC)")
        if line.net < 0:
            msgs.append("Negative net pay")
        # UAE Labour Law deduction ceiling: flag only, never blocks the run.
        if line.gross > 0 and line.total_deductions > 0.5 * line.gross:
            msgs.append("Deductions exceed 50% of gross pay (review against "
                        "the UAE Labour Law deduction ceiling)")
        if msgs:
            out[line.user_id] = msgs
    return out


def run_variance(run, threshold=0.10):
    """Per-employee net variance vs the previous APPROVED run.

    Flags employees whose net changed more than `threshold` (10%).
    """
    y, m = parse_period(run.period)
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    prev = PayrollRun.query.filter_by(period=f"{y:04d}-{m:02d}",
                                      status="approved").first()
    if not prev:
        return {}
    prev_net = {l.user_id: l.net for l in prev.lines}
    out = {}
    for line in run.lines:
        old = prev_net.get(line.user_id)
        if old is None or old == 0:
            continue
        change = (line.net - old) / abs(old)
        if abs(change) > threshold:
            out[line.user_id] = {"prev": old, "now": line.net,
                                 "change_pct": round(change * 100, 1)}
    return out


def wps_by_company(run):
    """Group run lines by visa company for WPS file export."""
    groups = {}
    for line in run.lines:
        user = User.query.get(line.user_id)
        comp = Company.query.get(user.visa_company_id) \
            if getattr(user, "visa_company_id", None) else None
        key = comp.code if comp else "NO-COMPANY"
        groups.setdefault(key, {"company": comp, "lines": [], "total_net": 0.0,
                                "total_basic": 0.0, "count": 0})
        g = groups[key]
        g["lines"].append((user, line))
        g["total_net"] += line.net
        g["total_basic"] += line.basic_earned
        g["count"] += 1
    return groups


# ---------------- Rule-based insights (honest AI label) ----------------

INSIGHT_LABEL = "Rule-based insights (AI-assisted checks)"


def payroll_insights(run):
    """Rule-based flags for a payroll run. No external calls, no ML claims."""
    flags = []
    # duplicate IBANs
    seen, dupes = {}, set()
    for u in employees_in_run():
        if u.iban:
            if u.iban in seen:
                dupes.add(u.iban)
            seen[u.iban] = u.id
    for iban in sorted(dupes):
        flags.append(("Duplicate IBAN across employees", iban, "high"))
    # negative net / missing data
    for uid, msgs in run_exceptions(run).items():
        for msg in msgs:
            flags.append((msg, uid, "high"))
    # OT hours > 60 in the period
    for ot in (db.session.query(PayrollOTHours.user_id,
                                db.func.sum(PayrollOTHours.hours))
               .join(PayrollRun)
               .filter(PayrollRun.id == run.id)
               .group_by(PayrollOTHours.user_id).all()):
        if ot[1] and ot[1] > 60:
            flags.append((f"OT hours above 60 ({ot[1]:.1f}h)", ot[0], "medium"))
    # net variance > 10%
    for uid, v in run_variance(run).items():
        flags.append((f"Net changed {v['change_pct']}% vs previous run "
                      f"(AED {v['prev']:.2f} -> {v['now']:.2f})", uid, "medium"))
    return flags


def compliance_insights():
    """Rule-based flags for the compliance page."""
    from app.hrms import get_upcoming_expiries
    from app.models import EmployeeDocument
    from datetime import timedelta
    flags = []
    expiries = get_upcoming_expiries(days=60, limit=200)
    for e in expiries:
        flags.append((f"{e['doc'].title()} expiring in {e['days_left']} days",
                      f"{e['user'].id} {e['user'].name}", "medium"
                      if e["days_left"] > 7 else "high"))
    docs = EmployeeDocument.query.filter(
        EmployeeDocument.superseded.is_(False),
        EmployeeDocument.expiry_date.isnot(None)).all()
    today = date.today()
    counts = {}
    for d in docs:
        if d.expiry_date <= today + timedelta(days=60):
            days = (d.expiry_date - today).days
            counts.setdefault(d.category, []).append((d, days))
    for cat, items in sorted(counts.items()):
        flags.append((f"{len(items)} {cat} document(s) expiring within 60 days",
                      ", ".join(sorted({i[0].user_id for i in items})),
                      "high" if any(i[1] <= 7 for i in items) else "medium"))
    return flags
