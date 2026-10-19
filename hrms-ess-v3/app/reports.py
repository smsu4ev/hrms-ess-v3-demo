"""Reports & Excel export hub (v3.1).

Every report has an on-screen HTML view plus an XLSX download (openpyxl).
Export governance:
- /hrms/reports/* routes are admin-only, except the payroll-money reports
  (additions & deductions, OT summary) which additionally accept users the
  HR Manager granted salary visibility to (admin OR can_view_salary).
- Every export writes an audit record (actor, report name, filters).
"""
import io
import json
import calendar
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Blueprint, request, render_template, abort, Response,
                   redirect, url_for, flash)
from flask_login import login_required, current_user
from sqlalchemy import or_

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from app import db
from app.models import (User, Company, SalaryComponent, PayrollRun, PayrollLine,
                        PayrollInput, Loan, LeaveBalance, LeaveRequest,
                        AttendanceRecord, SeparationRecord, ReportTemplate,
                        AuditLog, AssetCustody)
from app.audit import record
from app.files import salary_visible
from app.hrms import admin_required
from app import payroll as eng
from app.uae import gratuity as gratuity_calc
import config as app_config

reports_bp = Blueprint("reports", __name__, url_prefix="/hrms")


def _salary_required(fn):
    """Admin or a user granted salary visibility by the HR Manager."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if not salary_visible(current_user):
            abort(403)
        return fn(*a, **kw)
    return wrapper


# ---------------- Excel helpers ----------------

HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill("solid", fgColor="172B4D")
TITLE_FONT = Font(bold=True, size=13, color="172B4D")
TOTAL_FONT = Font(bold=True, size=11)
MONEY_FMT = '#,##0.00'
THIN_WRAP = Alignment(wrap_text=True, vertical="top")


def xlsx_cell(value):
    """Guard against spreadsheet formula injection in Excel exports.

    Spreadsheet apps treat cells starting with =, +, - or @ (and tab/CR
    control chars) as formulas, so user-controlled text (names, IBANs,
    leave reasons, notes) could execute on open. Prefix such cells with a
    single quote; the data itself is unchanged. Mirrors app.audit.csv_cell.
    """
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def make_workbook(title, subtitle_lines, columns, rows, money_cols=(),
                  total_row=None, total_label="Total"):
    wb = Workbook()
    ws = wb.active
    ws.title = (title[:28] or "Report")
    ws.append([title])
    ws["A1"].font = TITLE_FONT
    for line in subtitle_lines:
        ws.append([line])
    ws.append([])  # blank row
    header_row = len(subtitle_lines) + 3
    ws.append(columns)
    for c in range(1, len(columns) + 1):
        cell = ws.cell(row=header_row, column=c)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = THIN_WRAP
    for r in rows:
        ws.append([xlsx_cell(v) if isinstance(v, str) else v for v in r])
    if total_row is not None:
        ws.append([total_label] + [xlsx_cell(v) if isinstance(v, str) else v
                                   for v in total_row])
        for c in range(1, len(columns) + 1):
            ws.cell(row=ws.max_row, column=c).font = TOTAL_FONT
    # number formats + widths
    for idx in money_cols:
        col = get_column_letter(idx + 1)
        for row in ws.iter_rows(min_row=header_row + 1, min_col=idx + 1,
                                max_col=idx + 1):
            for cell in row:
                if isinstance(cell.value, (int, float)):
                    cell.number_format = MONEY_FMT
    for i, col in enumerate(columns, 1):
        width = max([len(str(col))] +
                    [len(str(ws.cell(row=r, column=i).value or ""))
                     for r in range(header_row, ws.max_row + 1)] + [10])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 42)
    ws.freeze_panes = "A%d" % (header_row + 1)
    return wb


def xlsx_response(wb, filename, report_name=None, filters=""):
    """Serialize a workbook to a download response.

    Adds a forensic watermark: workbook properties (creator = exporting user,
    title = report name, created = UTC now) plus a hidden "Export Info" sheet
    recording who exported what and when. Exports are streamed from memory
    with no-store caching.
    """
    try:
        who = f"{current_user.id} ({current_user.name})" \
            if getattr(current_user, "is_authenticated", False) else "system"
    except Exception:
        who = "system"
    now = datetime.utcnow()
    wb.properties.creator = who
    if report_name:
        wb.properties.title = report_name
    wb.properties.created = now
    ws_info = wb.create_sheet("Export Info")
    ws_info["A1"] = "Report"
    ws_info["B1"] = report_name or ""
    ws_info["A2"] = "Exported by"
    ws_info["B2"] = who
    ws_info["A3"] = "Exported at (UTC)"
    ws_info["B3"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    ws_info["A4"] = "Filters used"
    ws_info["B4"] = filters or ""
    for cell in ("A1", "A2", "A3", "A4"):
        ws_info[cell].font = Font(bold=True)
    ws_info.sheet_state = "veryHidden"
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return Response(
        out.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}",
                 "Cache-Control": "no-store"})


def export_audit(report_key, label, filters):
    record("report_export", f"{label} | filters: {filters}",
           entity="report", entity_id=report_key)
    db.session.commit()


def _saved_templates(report_key):
    return ReportTemplate.query.filter_by(
        user_id=current_user.id, report_key=report_key)\
        .order_by(ReportTemplate.created_at.desc()).all()


def _report_ctx(report_key, title, description, columns, rows, filters,
                export_args, money_cols=(), row_classes=None, total_row=None,
                total_label="Total", formula_note=None, footnotes=None):
    export_url = url_for("reports." + _ROUTE_NAMES[report_key],
                         **{k: v for k, v in export_args.items()
                            if v not in ("", None)}, format="xlsx")
    params = {k: v for k, v in export_args.items() if v not in ("", None)}
    saved = []
    for t in _saved_templates(report_key):
        try:
            t.apply_url = url_for("reports." + _ROUTE_NAMES[report_key],
                                  **json.loads(t.params_json or "{}"))
        except (ValueError, TypeError):
            t.apply_url = url_for("reports." + _ROUTE_NAMES[report_key])
        saved.append(t)
    return dict(title=title, description=description, report_key=report_key,
                columns=columns, rows=rows, filters=filters,
                money_cols=list(money_cols), row_classes=row_classes or [],
                total_row=total_row, total_label=total_label,
                formula_note=formula_note, footnotes=footnotes or [],
                export_url=export_url, saved=saved,
                params_json=json.dumps(params))


def _filter_depts(value):
    rows = User.query.with_entities(User.department).distinct().all()
    depts = sorted({r[0] for r in rows if r[0]})
    return {"name": "department", "label": "Department", "type": "select",
            "options": [("", "All departments")] + [(d, d) for d in depts],
            "value": value or ""}


def _filter_status(value):
    opts = [("", "All statuses"), ("active", "Active"), ("probation", "Probation"),
            ("resigned", "Resigned"), ("terminated", "Terminated"),
            ("retired", "Retired")]
    return {"name": "status", "label": "Status", "type": "select",
            "options": opts, "value": value or ""}


def _filter_company(value):
    comps = Company.query.order_by(Company.code).all()
    return {"name": "company", "label": "Visa Company", "type": "select",
            "options": [("", "All companies")] +
                       [(str(c.id), f"{c.code} - {c.name}") for c in comps],
            "value": value or ""}


def _user_map():
    return {u.id: u for u in User.query.all()}


# ---------------- Hub ----------------

REPORT_CARDS = [
    ("master", "Master Data Export",
     "Employee master list with filters; salary/bank columns only for viewers with salary permission."),
    ("addded", "Additions & Deductions Report",
     "Payroll register per period: component columns, additions, arrears, overtime, gross, deductions, net; department subtotals."),
    ("absence", "Absence Report",
     "Working days, present, absent, late, absence rate %, optional Bradford factor."),
    ("leave", "Leave Report",
     "Leave ledger (entitled / used / pending / remaining) and leave-request transactions."),
    ("separations", "Separation Report",
     "Ex-employee register with tenure, type, reason, settlement status."),
    ("turnover", "Turnover Report",
     "Separations vs headcount with voluntary/involuntary split and per-department breakdown."),
    ("variance", "Payroll Variance Report",
     "Current vs previous period: per-employee change, joiner/leaver buckets, exceptions above 10%."),
    ("liability", "Leave & EOSB Liability",
     "Accrued gratuity and annual-leave value per employee with department subtotals."),
    ("wpspreflight", "WPS Pre-Flight Check",
     "Per-run data-quality flags before bank transfer: Emirates ID, IBAN, bank, company, pay limits."),
    ("otsummary", "OT Summary",
     "Overtime hours and amounts by tier (normal / night / rest-day) per payroll period."),
    ("loans", "Loan Balances Report",
     "Loans and advances: principal, recovered, balance, installment, status."),
]

_ROUTE_NAMES = {k: {
    "master": "master", "addded": "addded", "absence": "absence",
    "leave": "leave", "separations": "sep_report", "turnover": "turnover",
    "variance": "variance", "liability": "liability",
    "wpspreflight": "wps_preflight",
    "otsummary": "ot_summary", "loans": "loans"}[k] for k in
    ["master", "addded", "absence", "leave", "separations", "turnover",
     "variance", "liability", "wpspreflight", "otsummary", "loans"]}


@reports_bp.route("/reports")
@login_required
@admin_required
def hub():
    return render_template("hr_reports_hub.html", cards=REPORT_CARDS)


# ---------------- (a) Master Data Export ----------------

def _master_rows(dept, status, company):
    q = User.query
    if dept:
        q = q.filter(User.department == dept)
    if status:
        q = q.filter(User.employment_status == status)
    if company:
        q = q.filter(User.visa_company_id == int(company))
    users = q.order_by(User.name).all()
    comp_names = {c.id: f"{c.code}" for c in Company.query.all()}
    umap = _user_map()
    show_salary = salary_visible(current_user)
    rows = []
    for u in users:
        mgr = umap.get(u.reporting_manager)
        row = [
            u.id, u.name,
            "Active" if u.is_active else "Inactive",
            u.department or "", u.designation or "",
            comp_names.get(getattr(u, "visa_company_id", None), ""),
            mgr.name if mgr else "",
            u.joining_date.isoformat() if u.joining_date else "",
            u.nationality or "", u.phone or u.mobile or "",
            u.passport_no or "",
            u.passport_expiry_date.isoformat() if u.passport_expiry_date else "",
            u.emirates_id_no or "",
            u.emirates_id_expiry_date.isoformat() if u.emirates_id_expiry_date else "",
            u.visa_expiry_date.isoformat() if u.visa_expiry_date else "",
            (u.employment_status or "").title(),
        ]
        if show_salary:
            row += [u.basic_salary or 0.0, u.monthly_salary or 0.0,
                    u.bank_name or "", u.iban or ""]
        rows.append(row)
    columns = ["Employee ID", "Name", "Status", "Department", "Designation",
               "Visa Company", "Reporting Manager", "Joining Date", "Nationality",
               "Phone", "Passport No", "Passport Expiry", "Emirates ID No",
               "Emirates ID Expiry", "Visa Expiry", "Employment Status"]
    if show_salary:
        columns += ["Basic (AED)", "Monthly (AED)", "Bank", "IBAN"]
    return columns, rows


@reports_bp.route("/reports/master")
@login_required
@admin_required
def master():
    dept = request.args.get("department", "")
    status = request.args.get("status", "")
    company = request.args.get("company", "")
    columns, rows = _master_rows(dept, status, company)
    filt = f"department={dept or 'all'}, status={status or 'all'}, company={company or 'all'}"
    if request.args.get("format") == "xlsx":
        money = [16, 17] if salary_visible(current_user) else []
        wb = make_workbook("Employee Master Data",
                           [f"Generated {date.today().isoformat()}", f"Filters: {filt}",
                            "Salary/bank columns shown only to viewers with salary permission."],
                           columns, rows, money_cols=money)
        export_audit("master", "Master Data Export", filt)
        return xlsx_response(wb, f"master-data-{date.today().isoformat()}.xlsx",
                           "Master Data Export", filt)
    ctx = _report_ctx("master", "Master Data Export",
                      "Employee master list. Salary and bank columns appear only for "
                      "viewers with salary permission.", columns, rows,
                      [_filter_depts(dept), _filter_status(status),
                       _filter_company(company)],
                      {"department": dept, "status": status, "company": company},
                      money_cols=[16, 17] if salary_visible(current_user) else [])
    return render_template("hr_report.html", **ctx)


# ---------------- (b) Additions & Deductions Report ----------------

def _addded_data(run):
    """Build register rows for a payroll run.

    Columns: ID, Name, Dept, Basic | earning-code groups | Overtime |
    of-which: Inputs | of-which: Arrears | Gross | deduction-code groups |
    Total Deductions | Net.
    """
    lines = (PayrollLine.query.filter_by(run_id=run.id)
             .order_by(PayrollLine.user_id).all())
    earn_codes, ded_codes = [], []  # (code, name) in first-seen order
    def seen(lst, code):
        return any(c == code for c, _ in lst)
    parsed = []
    for line in lines:
        earnings = json.loads(line.earnings_json or "[]")
        deductions = json.loads(line.deductions_json or "[]")
        ot = json.loads(line.ot_json or "[]")
        emap, dmap = {}, {}
        for e in earnings:
            code = e.get("code", "")
            if code == "OVERTIME":
                continue
            if not seen(earn_codes, code):
                earn_codes.append((code, e.get("name", code)))
            emap[code] = emap.get(code, 0.0) + float(e.get("amount", 0) or 0)
        for d in deductions:
            code = d.get("code", "")
            if not seen(ded_codes, code):
                ded_codes.append((code, d.get("name", code)))
            dmap[code] = dmap.get(code, 0.0) + float(d.get("amount", 0) or 0)
        ot_amt = round(sum(float(o.get("amount", 0) or 0) for o in ot), 2)
        parsed.append((line, emap, dmap, ot_amt))
    # informational breakouts from PayrollInput (subsets of the above)
    inputs_add, inputs_arr = {}, {}
    for inp in PayrollInput.query.filter_by(period=run.period).all():
        if inp.is_arrears:
            inputs_arr[inp.user_id] = inputs_arr.get(inp.user_id, 0.0) + float(inp.amount or 0)
        elif inp.kind == "addition":
            inputs_add[inp.user_id] = inputs_add.get(inp.user_id, 0.0) + float(inp.amount or 0)
    columns = (["Employee ID", "Name", "Department", "Basic (AED)"] +
               [f"{name}" for code, name in earn_codes] +
               ["Overtime (AED)", "of which: Inputs (AED)", "of which: Arrears (AED)",
                "Gross (AED)"] +
               [f"{name}" for code, name in ded_codes] +
               ["Total Deductions (AED)", "Net (AED)"])
    money_cols = list(range(3, len(columns)))
    umap = _user_map()
    rows, row_classes = [], []
    dept_totals = {}
    for line, emap, dmap, ot_amt in parsed:
        u = umap.get(line.user_id)
        dept = (u.department or "—") if u else "—"
        row = [line.user_id, u.name if u else line.user_id, dept,
               line.basic_earned]
        row += [round(emap.get(code, 0.0), 2) for code, _ in earn_codes]
        row += [ot_amt,
                round(inputs_add.get(line.user_id, 0.0), 2),
                round(inputs_arr.get(line.user_id, 0.0), 2),
                line.gross]
        row += [round(dmap.get(code, 0.0), 2) for code, _ in ded_codes]
        row += [line.total_deductions, line.net]
        rows.append(row)
        row_classes.append("")
        key = dept
        t = dept_totals.setdefault(key, [0.0] * (len(columns) - 3))
        for i in range(3, len(columns)):
            t[i - 3] += float(row[i] or 0)
    # department subtotal rows + grand total
    grand = [0.0] * (len(columns) - 3)
    for dept in sorted(dept_totals):
        t = dept_totals[dept]
        rows.append([f"Subtotal — {dept}", "", ""] +
                    [round(v, 2) for v in t])
        row_classes.append("subtotal")
        for i, v in enumerate(t):
            grand[i] += v
    # full-length total row (label in the first column)
    total_row = ["GRAND TOTAL", "", ""] + [round(v, 2) for v in grand]
    return columns, rows, row_classes, money_cols, total_row


@reports_bp.route("/reports/additions-deductions")
@login_required
@_salary_required
def addded():
    runs = PayrollRun.query.order_by(PayrollRun.period.desc()).all()
    period = request.args.get("period") or (runs[0].period if runs else "")
    run = PayrollRun.query.filter_by(period=period).first() if period else None
    if not run:
        flash("No payroll run found for that period.", "error")
        return redirect(url_for("reports.hub"))
    columns, rows, row_classes, money_cols, total_row = _addded_data(run)
    note = (f"Run {run.period} is '{run.status}' - figures may change until locked."
            if run.status not in ("locked", "approved") else
            f"Run {run.period} is '{run.status}'.")
    filt = f"period={period}"
    if request.args.get("format") == "xlsx":
        wb = make_workbook("Additions & Deductions Report",
                           [f"Period: {period}", note,
                            "'of which' columns are informational breakouts from Payroll Inputs; "
                            "component columns already include those amounts."],
                           columns, rows, money_cols=money_cols,
                           total_row=total_row, total_label="GRAND TOTAL")
        export_audit("addded", "Additions & Deductions Report", filt)
        return xlsx_response(wb, f"additions-deductions-{period}.xlsx",
                           "Additions & Deductions Report", filt)
    ctx = _report_ctx(
        "addded", "Additions & Deductions Report",
        "Payroll register for one pay period. " + note, columns, rows,
        [{"name": "period", "label": "Payroll Period", "type": "select",
          "options": [(r.period, f"{r.period} ({r.status})") for r in runs],
          "value": period}],
        {"period": period}, money_cols=money_cols, row_classes=row_classes,
        total_row=total_row, total_label="GRAND TOTAL",
        footnotes=["'of which' columns are informational breakouts from Payroll Inputs; "
                   "component columns already include those amounts."])
    return render_template("hr_report.html", **ctx)


# ---------------- (c) Absence Report ----------------

def _bradford(absent_dates):
    """Bradford factor = spells^2 x absent days. A spell = consecutive
    calendar dates with an absence. Shown as an optional indicator only."""
    if not absent_dates:
        return 0, 0
    ds = sorted(set(absent_dates))
    spells, run = 1, 1
    for prev, cur in zip(ds, ds[1:]):
        if (cur - prev).days == 1:
            run += 1
        else:
            spells += 1
            run = 1
    return spells, spells * spells * len(ds)


@reports_bp.route("/reports/absence")
@login_required
@admin_required
def absence():
    month = request.args.get("month") or date.today().strftime("%Y-%m")
    try:
        y, m = int(month[:4]), int(month[5:7])
        start = date(y, m, 1)
    except (ValueError, IndexError):
        abort(400)
    end = date(y, m, calendar.monthrange(y, m)[1])
    recs = AttendanceRecord.query.filter(
        AttendanceRecord.date >= start, AttendanceRecord.date <= end).all()
    by_user = {}
    for r in recs:
        by_user.setdefault(r.user_id, []).append(r)
    users = User.query.filter(User.is_active.is_(True)).order_by(User.name).all()
    rows = []
    for u in users:
        recs_u = by_user.get(u.id, [])
        present = sum(1 for r in recs_u if r.status == "present")
        late = sum(1 for r in recs_u if r.status == "late")
        absent = sum(1 for r in recs_u if r.status == "absent")
        working = len(recs_u)
        rate = round(absent / (present + absent) * 100, 1) if (present + absent) else 0.0
        spells, bradford = _bradford([r.date for r in recs_u if r.status == "absent"])
        rows.append([u.id, u.name, u.department or "", working, present, absent,
                     late, f"{rate:.1f}%", spells, bradford])
    columns = ["Employee ID", "Name", "Department", "Working Days",
               "Present", "Absent", "Late", "Absence Rate %",
               "Absence Spells", "Bradford Factor"]
    filt = f"month={month}"
    if request.args.get("format") == "xlsx":
        wb = make_workbook("Absence Report",
                           [f"Month: {month}",
                            "Absence rate = absent / (present + absent) x 100. "
                            "Bradford factor = spells^2 x absent days (optional indicator)."],
                           columns, rows)
        export_audit("absence", "Absence Report", filt)
        return xlsx_response(wb, f"absence-{month}.xlsx", "Absence Report", filt)
    ctx = _report_ctx(
        "absence", "Absence Report",
        f"Attendance summary for {month}.", columns, rows,
        [{"name": "month", "label": "Month", "type": "month", "value": month}],
        {"month": month},
        formula_note="Absence rate = absent days / (present + absent days) x 100. "
                     "A 'spell' is consecutive calendar days of absence.",
        footnotes=["Bradford factor (spells squared x absent days) is an optional "
                   "indicator of absence patterns; it is not a Gulf/UAE standard "
                   "and should not be used alone for disciplinary decisions."])
    return render_template("hr_report.html", **ctx)


# ---------------- (d) Leave Report ----------------

@reports_bp.route("/reports/leave")
@login_required
@admin_required
def leave():
    view = request.args.get("view", "ledger")
    if view == "requests":
        return _leave_requests_view()
    users = User.query.filter(User.is_active.is_(True)).order_by(User.name).all()
    pending = {}
    for r in LeaveRequest.query.filter_by(status="pending").all():
        pending[(r.user_id, r.leave_type)] = \
            pending.get((r.user_id, r.leave_type), 0.0) + (r.days or 0)
    bals = {(b.user_id, b.leave_type): b for b in LeaveBalance.query.all()}
    types = sorted({b.leave_type for b in bals.values()},
                   key=lambda t: list(app_config.LEAVE_TYPES).index(t)
                   if t in app_config.LEAVE_TYPES else 99)
    rows = []
    for u in users:
        for lt in types:
            b = bals.get((u.id, lt))
            if not b:
                continue
            pend = pending.get((u.id, lt), 0.0)
            rows.append([u.id, u.name, u.department or "", lt.title(),
                         b.entitled, b.used, pend,
                         round(b.entitled - b.used - pend, 2)])
    columns = ["Employee ID", "Name", "Department", "Leave Type", "Entitled",
               "Used (Approved)", "Pending", "Remaining"]
    if request.args.get("format") == "xlsx":
        wb = make_workbook("Leave Ledger",
                           [f"Generated {date.today().isoformat()}",
                            "Remaining = Entitled - Used (approved) - Pending."],
                           columns, rows,
                           money_cols=[4, 5, 6, 7])
        export_audit("leave", "Leave Ledger Report", "view=ledger")
        return xlsx_response(wb, f"leave-ledger-{date.today().isoformat()}.xlsx",
                           "Leave Ledger Report", "view=ledger")
    ctx = _report_ctx(
        "leave", "Leave Report — Ledger",
        "Per-employee leave balances.", columns, rows,
        [{"name": "view", "label": "View", "type": "select",
          "options": [("ledger", "Ledger"), ("requests", "Leave Requests")],
          "value": view}],
        {"view": view}, money_cols=[4, 5, 6, 7],
        formula_note="Remaining = Entitled - Used (approved) - Pending approval.")
    return render_template("hr_report.html", **ctx)


def _leave_requests_view():
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    status = request.args.get("status", "")
    q = LeaveRequest.query
    if start:
        q = q.filter(LeaveRequest.start_date >= date.fromisoformat(start))
    if end:
        q = q.filter(LeaveRequest.end_date <= date.fromisoformat(end))
    if status:
        q = q.filter(LeaveRequest.status == status)
    items = q.order_by(LeaveRequest.start_date.desc()).all()
    umap = _user_map()
    rows = []
    for r in items:
        u = umap.get(r.user_id)
        ap = umap.get(r.approver_id)
        rows.append([r.user_id, u.name if u else r.user_id,
                     u.department if u else "", r.leave_type.title(),
                     r.start_date.isoformat(), r.end_date.isoformat(),
                     r.days, r.status.title(), ap.name if ap else ""])
    columns = ["Employee ID", "Name", "Department", "Leave Type", "From", "To",
               "Days", "Status", "Approver"]
    filt = f"view=requests, start={start or '-'}, end={end or '-'}, status={status or 'all'}"
    if request.args.get("format") == "xlsx":
        wb = make_workbook("Leave Requests",
                           [f"Generated {date.today().isoformat()}", f"Filters: {filt}"],
                           columns, rows, money_cols=[6])
        export_audit("leave", "Leave Requests Report", filt)
        return xlsx_response(wb, f"leave-requests-{date.today().isoformat()}.xlsx",
                           "Leave Requests Report", filt)
    ctx = _report_ctx(
        "leave", "Leave Report — Requests",
        "Leave applications in the selected range.", columns, rows,
        [{"name": "view", "label": "View", "type": "select",
          "options": [("ledger", "Ledger"), ("requests", "Leave Requests")],
          "value": "requests"},
         {"name": "start", "label": "From", "type": "date", "value": start},
         {"name": "end", "label": "To", "type": "date", "value": end},
         {"name": "status", "label": "Status", "type": "select",
          "options": [("", "All"), ("pending", "Pending"),
                      ("approved", "Approved"), ("rejected", "Rejected"),
                      ("cancelled", "Cancelled")], "value": status}],
        {"view": "requests", "start": start, "end": end, "status": status},
        money_cols=[6])
    return render_template("hr_report.html", **ctx)


# ---------------- (e) Separation Report ----------------

def _separation_rows(year, stype, dept):
    recs = SeparationRecord.query
    if year:
        recs = recs.filter(SeparationRecord.separation_date >= date(int(year), 1, 1),
                            SeparationRecord.separation_date <= date(int(year), 12, 31))
    if stype:
        recs = recs.filter(SeparationRecord.separation_type == stype)
    if dept:
        recs = recs.filter(SeparationRecord.department == dept)
    recs = recs.order_by(SeparationRecord.separation_date.desc()).all()
    rec_codes = {r.employee_code for r in recs} if not (year or stype or dept) else \
        {r.employee_code for r in SeparationRecord.query.all()}
    rows = []
    for r in recs:
        pending = AssetCustody.query.filter_by(user_id=r.employee_code,
                                               status="Issued",
                                               returned_date=None).count()
        rows.append([r.employee_code, r.employee_name, r.department or "",
                     r.designation or "",
                     r.joining_date.isoformat() if r.joining_date else "",
                     r.separation_date.isoformat() if r.separation_date else "",
                     r.tenure_years if r.tenure_years is not None else "",
                     r.separation_type, (r.reason or "")[:80],
                     r.notice_days if r.notice_days is not None else "",
                     f"{pending} unreturned" if pending else "",
                     r.final_settlement_status, r.settlement_amount or 0.0])
    # merge separated users not already in the register
    uq = User.query.filter(User.separation_date.isnot(None))
    if year:
        uq = uq.filter(User.separation_date >= date(int(year), 1, 1),
                       User.separation_date <= date(int(year), 12, 31))
    if stype:
        uq = uq.filter(User.separation_type == stype)
    if dept:
        uq = uq.filter(User.department == dept)
    for u in uq.order_by(User.separation_date.desc()).all():
        if u.id in rec_codes:
            continue
        tenure = (round((u.separation_date - u.joining_date).days / 365.25, 1)
                  if u.joining_date and u.separation_date else "")
        pending = AssetCustody.query.filter_by(user_id=u.id, status="Issued",
                                               returned_date=None).count()
        rows.append([u.id, u.name, u.department or "", u.designation or "",
                     u.joining_date.isoformat() if u.joining_date else "",
                     u.separation_date.isoformat() if u.separation_date else "",
                     tenure, u.separation_type or "",
                     "(from employee record)", "",
                     f"{pending} unreturned" if pending else "",
                     "", 0.0])
    columns = ["Employee ID", "Name", "Department", "Designation",
               "Joining Date", "Separation Date", "Tenure (yrs)",
               "Separation Type", "Reason", "Notice Days", "Assets Pending",
               "Settlement Status", "Settlement (AED)"]
    return columns, rows


@reports_bp.route("/reports/separations")
@login_required
@admin_required
def sep_report():
    year = request.args.get("year", "")
    stype = request.args.get("stype", "")
    dept = request.args.get("department", "")
    columns, rows = _separation_rows(year, stype, dept)
    filt = f"year={year or 'all'}, type={stype or 'all'}, department={dept or 'all'}"
    if request.args.get("format") == "xlsx":
        wb = make_workbook("Separation Report",
                           [f"Generated {date.today().isoformat()}", f"Filters: {filt}",
                            "Includes the separation register plus separated users "
                            "not yet recorded in the register."],
                           columns, rows, money_cols=[12])
        export_audit("separations", "Separation Report", filt)
        return xlsx_response(wb, f"separations-{date.today().isoformat()}.xlsx",
                           "Separation Report", filt)
    types = ["Resignation", "Termination", "Contract End", "Retirement",
             "Absconding", "Other"]
    ctx = _report_ctx(
        "separations", "Separation Report",
        "Ex-employee register merged with separated employee records.",
        columns, rows,
        [{"name": "year", "label": "Year", "type": "text", "value": year,
          "placeholder": "e.g. 2026"},
         {"name": "stype", "label": "Type", "type": "select",
          "options": [("", "All types")] + [(t, t) for t in types],
          "value": stype},
         _filter_depts(dept)],
        {"year": year, "stype": stype, "department": dept},
        money_cols=[12])
    return render_template("hr_report.html", **ctx)


# ---------------- (f) Turnover Report ----------------

def _turnover_stats(start, end):
    """Separations, joiners, headcount approximation, turnover %.

    Headcount is derived from current records (approximation for past
    periods): end = users joined by period end with no separation (or
    separation after period end).
    """
    recs = SeparationRecord.query.filter(
        SeparationRecord.separation_date >= start,
        SeparationRecord.separation_date <= end).all()
    rec_codes = {r.employee_code for r in recs}
    users_sep = User.query.filter(
        User.separation_date.isnot(None),
        User.separation_date >= start,
        User.separation_date <= end).all()
    users_sep = [u for u in users_sep if u.id not in rec_codes]
    separations = len(recs) + len(users_sep)
    joiners = User.query.filter(User.joining_date >= start,
                                User.joining_date <= end).count()
    end_hc = User.query.filter(
        User.joining_date <= end,
        or_(User.separation_date.is_(None),
            User.separation_date > end)).count()
    start_hc = end_hc - joiners + separations
    avg = (start_hc + end_hc) / 2
    rate = round(separations / avg * 100, 1) if avg else 0.0

    def cat(sep_type):
        if sep_type in SeparationRecord.voluntary_types():
            return "voluntary"
        if sep_type in SeparationRecord.involuntary_types():
            return "involuntary"
        return "other"
    cats = {"voluntary": 0, "involuntary": 0, "other": 0}
    dept_sep = {}
    for r in recs:
        cats[cat(r.separation_type)] += 1
        dept_sep[r.department or "—"] = dept_sep.get(r.department or "—", 0) + 1
    for u in users_sep:
        cats[cat(u.separation_type or "")] += 1
        dept_sep[u.department or "—"] = dept_sep.get(u.department or "—", 0) + 1

    dept_rows = []
    for dept in sorted(dept_sep):
        d_join = User.query.filter(User.department == dept,
                                   User.joining_date >= start,
                                   User.joining_date <= end).count()
        d_end = User.query.filter(
            User.department == dept, User.joining_date <= end,
            or_(User.separation_date.is_(None),
                User.separation_date > end)).count()
        d_start = d_end - d_join + dept_sep[dept]
        d_avg = (d_start + d_end) / 2
        d_rate = round(dept_sep[dept] / d_avg * 100, 1) if d_avg else 0.0
        dept_rows.append([dept, dept_sep[dept], round(d_avg, 1), f"{d_rate:.1f}%"])
    return {
        "start": start, "end": end, "separations": separations,
        "joiners": joiners, "start_hc": start_hc, "end_hc": end_hc,
        "avg_hc": round(avg, 1), "rate": rate, "cats": cats,
        "dept_rows": dept_rows,
    }


@reports_bp.route("/reports/turnover")
@login_required
@admin_required
def turnover():
    mode = request.args.get("mode", "year")
    year = request.args.get("year", "") or str(date.today().year)
    if mode == "last12":
        end = date.today()
        start = end - timedelta(days=365)
        label = "Last 12 months"
    else:
        start, end = date(int(year), 1, 1), date(int(year), 12, 31)
        label = f"Year {year}"
    stats = _turnover_stats(start, end)
    columns = ["Metric", "Value"]
    rows = [
        ["Period", label],
        ["Separations in period", stats["separations"]],
        ["Joiners in period", stats["joiners"]],
        ["Headcount at start (approx.)", stats["start_hc"]],
        ["Headcount at end (approx.)", stats["end_hc"]],
        ["Average headcount", stats["avg_hc"]],
        ["Turnover %", f"{stats['rate']:.1f}%"],
        ["Voluntary separations", stats["cats"]["voluntary"]],
        ["Involuntary separations", stats["cats"]["involuntary"]],
        ["Other separations", stats["cats"]["other"]],
    ]
    dept_columns = ["Department", "Separations", "Avg Headcount", "Turnover %"]
    filt = f"mode={mode}, year={year}"
    if request.args.get("format") == "xlsx":
        wb = make_workbook("Turnover Report",
                           [f"Period: {label}", _TURNOVER_FORMULA,
                            "Headcount for past periods is approximated from current records."],
                           columns, rows)
        ws2 = wb.create_sheet("By Department")
        ws2.append(dept_columns)
        for r in stats["dept_rows"]:
            ws2.append(r)
        export_audit("turnover", "Turnover Report", filt)
        return xlsx_response(wb, f"turnover-{label.replace(' ', '-')}.xlsx".lower(),
                           "Turnover Report", filt)
    ctx = _report_ctx(
        "turnover", "Turnover Report",
        f"Workforce movement for {label}.", columns, rows,
        [{"name": "mode", "label": "Period", "type": "select",
          "options": [("year", "Calendar year"), ("last12", "Last 12 months")],
          "value": mode},
         {"name": "year", "label": "Year", "type": "text", "value": year,
          "placeholder": "e.g. 2026"}],
        {"mode": mode, "year": year},
        formula_note=_TURNOVER_FORMULA,
        footnotes=["Headcount at start/end for past periods is approximated from "
                   "current employee records."])
    ctx["dept_columns"] = dept_columns
    ctx["dept_rows"] = stats["dept_rows"]
    return render_template("hr_report_turnover.html", **ctx)


_TURNOVER_FORMULA = (
    "Turnover % = (Separations in period / Average headcount) x 100; "
    "Average headcount = (Headcount at start + Headcount at end) / 2. "
    "Voluntary = Resignation + Retirement; Involuntary = Termination + "
    "Absconding; Other = Contract End + Other. Turnover definitions vary "
    "by vendor - this report uses the definition stated here.")


# ---------------- (g) OT Summary ----------------

@reports_bp.route("/reports/ot-summary")
@login_required
@_salary_required
def ot_summary():
    runs = PayrollRun.query.order_by(PayrollRun.period.desc()).all()
    period = request.args.get("period") or (runs[0].period if runs else "")
    run = PayrollRun.query.filter_by(period=period).first() if period else None
    if not run:
        flash("No payroll run found for that period.", "error")
        return redirect(url_for("reports.hub"))
    umap = _user_map()
    rows = []
    tot = [0.0] * 8
    for line in (PayrollLine.query.filter_by(run_id=run.id)
                 .order_by(PayrollLine.user_id).all()):
        ot = json.loads(line.ot_json or "[]")
        by_cat = {}
        for o in ot:
            c = by_cat.setdefault(o.get("category", "normal"),
                                  {"hours": 0.0, "amount": 0.0})
            c["hours"] += float(o.get("hours", 0) or 0)
            c["amount"] += float(o.get("amount", 0) or 0)
        n, ni, r = (by_cat.get("normal", {"hours": 0.0, "amount": 0.0}),
                    by_cat.get("night", {"hours": 0.0, "amount": 0.0}),
                    by_cat.get("restday", {"hours": 0.0, "amount": 0.0}))
        u = umap.get(line.user_id)
        vals = [round(n["hours"], 1), round(n["amount"], 2),
                round(ni["hours"], 1), round(ni["amount"], 2),
                round(r["hours"], 1), round(r["amount"], 2),
                round(n["hours"] + ni["hours"] + r["hours"], 1),
                round(n["amount"] + ni["amount"] + r["amount"], 2)]
        rows.append([line.user_id, u.name if u else line.user_id,
                     u.department if u else ""] + vals)
        for i, v in enumerate(vals):
            tot[i] += v
    columns = ["Employee ID", "Name", "Department",
               "Normal OT Hrs", "Normal OT (AED)",
               "Night OT Hrs", "Night OT (AED)",
               "Rest-day OT Hrs", "Rest-day OT (AED)",
               "Total OT Hrs", "Total OT (AED)"]
    filt = f"period={period}"
    total_vals = [round(v, 2) for v in tot]
    if request.args.get("format") == "xlsx":
        wb = make_workbook("OT Summary",
                           [f"Period: {period} (run status: {run.status})"],
                           columns, rows, money_cols=[4, 6, 8, 10],
                           total_row=total_vals,
                           total_label="TOTAL")
        export_audit("otsummary", "OT Summary Report", filt)
        return xlsx_response(wb, f"ot-summary-{period}.xlsx", "OT Summary Report", filt)
    ctx = _report_ctx(
        "otsummary", "OT Summary",
        f"Overtime by tier for payroll period {period} (run: {run.status}).",
        columns, rows,
        [{"name": "period", "label": "Payroll Period", "type": "select",
          "options": [(r.period, f"{r.period} ({r.status})") for r in runs],
          "value": period}],
        {"period": period}, money_cols=[4, 6, 8, 10],
        total_row=["TOTAL", "", ""] + total_vals)
    return render_template("hr_report.html", **ctx)


# ---------------- (h) Loan Balances ----------------

@reports_bp.route("/reports/loans")
@login_required
@admin_required
def loans():
    umap = _user_map()
    rows = []
    for ln in Loan.query.order_by(Loan.user_id).all():
        u = umap.get(ln.user_id)
        recovered = round((ln.principal or 0) - (ln.balance or 0), 2)
        rows.append([ln.user_id, u.name if u else ln.user_id,
                     u.department if u else "",
                     ln.loan_type.title(), ln.principal, recovered,
                     ln.balance, ln.installment, ln.status.title(),
                     ln.start_period or ""])
    columns = ["Employee ID", "Name", "Department", "Type", "Principal (AED)",
               "Recovered (AED)", "Balance (AED)", "Installment (AED)",
               "Status", "Start Period"]
    if request.args.get("format") == "xlsx":
        wb = make_workbook("Loan Balances Report",
                           [f"Generated {date.today().isoformat()}",
                            "Recovered = Principal - Balance."],
                           columns, rows, money_cols=[4, 5, 6, 7])
        export_audit("loans", "Loan Balances Report", "all")
        return xlsx_response(wb, f"loan-balances-{date.today().isoformat()}.xlsx",
                           "Loan Balances Report", "all")
    ctx = _report_ctx(
        "loans", "Loan Balances Report",
        "Loans and salary advances with recovery progress.", columns, rows,
        [], {}, money_cols=[4, 5, 6, 7])
    return render_template("hr_report.html", **ctx)


# ---------------- (j) Payroll Variance Report ----------------

def _prev_period(period):
    try:
        y, m = int(period[:4]), int(period[5:7])
    except (ValueError, IndexError):
        return None
    m -= 1
    if m < 1:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


@reports_bp.route("/reports/variance")
@login_required
@_salary_required
def variance():
    runs = PayrollRun.query.order_by(PayrollRun.period.desc()).all()
    period = request.args.get("period") or (runs[0].period if runs else "")
    run = PayrollRun.query.filter_by(period=period).first() if period else None
    if not run:
        flash("No payroll run found for that period.", "error")
        return redirect(url_for("reports.hub"))
    prev_period = _prev_period(period)
    prev_run = (PayrollRun.query.filter_by(period=prev_period).first()
                if prev_period else None)
    if not prev_run:
        if request.args.get("format") == "xlsx":
            abort(404)
        return render_template(
            "hr_report_simple.html",
            title="Payroll Variance Report",
            body=(f"No previous payroll run found before {period}, so there is "
                  f"nothing to compare. Create a run for {prev_period or 'an earlier period'} "
                  "first, then return here."))
    cur = {l.user_id: l for l in
           PayrollLine.query.filter_by(run_id=run.id).all()}
    prev = {l.user_id: l for l in
            PayrollLine.query.filter_by(run_id=prev_run.id).all()}
    umap = _user_map()
    rows, row_classes, exc = [], [], []
    prev_total = cur_total = 0.0
    for uid in sorted(set(cur) | set(prev)):
        u = umap.get(uid)
        c, p = cur.get(uid), prev.get(uid)
        cnet = round(c.net or 0, 2) if c else 0.0
        pnet = round(p.net or 0, 2) if p else 0.0
        if c and not p:
            bucket = "New Joiner"
        elif p and not c:
            bucket = "Leaver"
        elif cnet == pnet:
            bucket = "Unchanged"
        else:
            bucket = "Changed"
        chg = round(cnet - pnet, 2)
        pct = round(chg / pnet * 100, 1) if (c and p and pnet) else ""
        rows.append([uid, u.name if u else uid, u.department if u else "",
                     pnet if p else "", cnet if c else "", chg,
                     f"{pct:.1f}%" if pct != "" else "", bucket])
        row_classes.append("")
        if bucket == "Changed" and p and pnet and abs(float(pct)) > 10:
            exc.append(rows[-1])
        prev_total += pnet if p else 0
        cur_total += cnet if c else 0
    bridge = [round(prev_total, 2), round(cur_total, 2),
              round(cur_total - prev_total, 2)]
    columns = ["Employee ID", "Name", "Department",
               f"Net {prev_run.period} (AED)", f"Net {run.period} (AED)",
               "Change (AED)", "Change %", "Bucket"]
    filt = f"period={period}, vs={prev_period}"
    if request.args.get("format") == "xlsx":
        wb = make_workbook(
            "Payroll Variance Report",
            [f"Current: {period} ({run.status}) vs Previous: {prev_period} "
             f"({prev_run.status})",
             "Exceptions: pay change above 10% (joiners/leavers excluded)."],
            columns, rows, money_cols=[3, 4, 5],
            total_row=["BRIDGE", "", ""] + bridge, total_label="BRIDGE")
        ws2 = wb.create_sheet("Exceptions")
        ws2.append(columns)
        for r in exc:
            ws2.append([xlsx_cell(v) if isinstance(v, str) else v for v in r])
        export_audit("variance", "Payroll Variance Report", filt)
        return xlsx_response(wb, f"payroll-variance-{period}.xlsx",
                             "Payroll Variance Report", filt)
    ctx = _report_ctx(
        "variance", "Payroll Variance Report",
        f"Net pay: {period} vs {prev_period}.",
        columns, rows,
        [{"name": "period", "label": "Payroll Period", "type": "select",
          "options": [(r.period, f"{r.period} ({r.status})") for r in runs],
          "value": period}],
        {"period": period}, money_cols=[3, 4, 5],
        row_classes=row_classes,
        total_row=["BRIDGE", "", ""] + bridge, total_label="BRIDGE",
        formula_note="Change % = (Current net - Previous net) / Previous net x 100. "
                     "Exceptions are employees whose change exceeds 10% "
                     "(joiners and leavers excluded).",
        footnotes=["Joiner/Leaver buckets compare who was on each run only; "
                   "they do not distinguish contract type."])
    ctx["exc_columns"] = columns
    ctx["exc_rows"] = exc
    return render_template("hr_report_variance.html", **ctx)


# ---------------- (k) Leave & EOSB Liability Report ----------------

@reports_bp.route("/reports/liability")
@login_required
@_salary_required
def liability():
    today = date.today()
    users = User.query.filter(User.is_active.is_(True)).order_by(User.name).all()
    bals = {(b.user_id, b.leave_type): b for b in LeaveBalance.query.all()}
    rows = []
    dept_totals = {}
    for u in users:
        basic = eng.basic_salary(u.id, today)
        years = ((today - u.joining_date).days / 365.25) if u.joining_date else 0.0
        grat = gratuity_calc(u.joining_date, today, basic, "employer")[
            "amount"] if u.joining_date else 0.0
        b = bals.get((u.id, "annual"))
        rem = round((b.entitled - b.used) if b else 0.0, 2)
        leave_val = round(max(rem, 0.0) * (basic / 30.0), 2)
        dept = u.department or "—"
        row = [u.id, u.name, dept,
               u.joining_date.isoformat() if u.joining_date else "",
               round(years, 1), round(basic, 2), grat, rem, leave_val,
               round(grat + leave_val, 2)]
        rows.append(row)
        t = dept_totals.setdefault(dept, [0.0] * 5)
        for i, v in enumerate([grat, leave_val, grat + leave_val]):
            t[i] += v
    columns = ["Employee ID", "Name", "Department", "Joining Date",
               "Years of Service", "Basic (AED)", "Gratuity Accrued (AED)",
               "Annual Leave Remaining (days)", "Leave Value (AED)",
               "Total Liability (AED)"]
    grand = [0.0] * 3
    for dept in sorted(dept_totals):
        t = dept_totals[dept]
        rows.append([f"Subtotal — {dept}", "", "", "", "", "",
                     round(t[0], 2), "", round(t[1], 2), round(t[2], 2)])
        for i in range(3):
            grand[i] += t[i]
    total_row = ["GRAND TOTAL", "", "", "", "", "",
                 round(grand[0], 2), "", round(grand[1], 2),
                 round(grand[2], 2)]
    if request.args.get("format") == "xlsx":
        wb = make_workbook(
            "Leave & EOSB Liability",
            [f"As of {today.isoformat()}",
             "Gratuity accrued per Federal Decree-Law 33/2021 (estimate only; "
             "final settlement is calculated by HR at separation). "
             "Leave value = remaining annual-leave days x (basic / 30)."],
            columns, rows, money_cols=[5, 6, 7, 8, 9],
            total_row=total_row, total_label="GRAND TOTAL")
        export_audit("liability", "Leave & EOSB Liability Report", "all")
        return xlsx_response(wb, f"liability-{today.isoformat()}.xlsx",
                             "Leave & EOSB Liability Report", "all")
    ctx = _report_ctx(
        "liability", "Leave & EOSB Liability",
        f"Accrued obligations as of {today.isoformat()}.",
        columns, rows, [], {}, money_cols=[5, 6, 7, 8, 9],
        total_row=total_row, total_label="GRAND TOTAL",
        formula_note="Gratuity accrued per Federal Decree-Law 33/2021 using "
                     "last-drawn basic and service to date (estimate only). "
                     "Leave value = remaining annual-leave days x (basic / 30).",
        footnotes=["Final settlement figures are calculated by HR at "
                   "separation; this report is a management estimate."])
    return render_template("hr_report.html", **ctx)


# ---------------- (l) WPS Pre-Flight Check ----------------

@reports_bp.route("/reports/wps-preflight")
@login_required
@_salary_required
def wps_preflight():
    runs = PayrollRun.query.order_by(PayrollRun.period.desc()).all()
    period = request.args.get("period") or (runs[0].period if runs else "")
    run = PayrollRun.query.filter_by(period=period).first() if period else None
    if not run:
        flash("No payroll run found for that period.", "error")
        return redirect(url_for("reports.hub"))
    umap = _user_map()
    comp_names = {c.id: c.code for c in Company.query.all()}
    lines = PayrollLine.query.filter_by(run_id=run.id).all()
    flagged, ok = [], 0
    for line in lines:
        u = umap.get(line.user_id)
        flags = []
        name = u.name if u else line.user_id
        if u:
            if not (u.emirates_id_no or "").strip():
                flags.append("Missing Emirates ID number")
            iban = (u.iban or "").replace(" ", "").upper()
            if not (len(iban) == 23 and iban.startswith("AE")):
                flags.append("Invalid IBAN (must be AE followed by 21 digits)")
            if not (u.bank_name or "").strip():
                flags.append("Missing bank name")
            if not getattr(u, "visa_company_id", None):
                flags.append("Missing visa company")
        else:
            flags.append("Employee record not found")
        gross = line.gross or 0
        if gross > 0:
            if (line.net or 0) < 0.8 * gross:
                flags.append("Net pay below 80% of gross")
            if (line.total_deductions or 0) > 0.5 * gross:
                flags.append("Total deductions above 50% of gross")
        if flags:
            flagged.append([line.user_id, name,
                            u.department if u else "",
                            comp_names.get(getattr(u, "visa_company_id", None),
                                           "") if u else "",
                            round(line.net or 0, 2), "; ".join(flags)])
        else:
            ok += 1
    columns = ["Employee ID", "Name", "Department", "Visa Company",
               "Net (AED)", "Flags"]
    filt = f"period={period}"
    summary = (f"Run {period}: {len(lines)} employees checked — "
               f"{ok} OK, {len(flagged)} flagged.")
    if request.args.get("format") == "xlsx":
        wb = make_workbook("WPS Pre-Flight Check", [summary],
                           columns, flagged, money_cols=[4])
        export_audit("wpspreflight", "WPS Pre-Flight Check", filt)
        return xlsx_response(wb, f"wps-preflight-{period}.xlsx",
                             "WPS Pre-Flight Check", filt)
    ctx = _report_ctx(
        "wpspreflight", "WPS Pre-Flight Check",
        summary + " Fix flagged items before generating the WPS file.",
        columns, flagged,
        [{"name": "period", "label": "Payroll Period", "type": "select",
          "options": [(r.period, f"{r.period} ({r.status})") for r in runs],
          "value": period}],
        {"period": period}, money_cols=[4])
    ctx["summary_ok"] = ok
    return render_template("hr_report.html", **ctx)


# ---------------- (i) Saved report templates ----------------

@reports_bp.route("/reports/templates/save", methods=["POST"])
@login_required
@admin_required
def template_save():
    report_key = (request.form.get("report_key") or "").strip()
    name = (request.form.get("name") or "").strip()[:120]
    params = (request.form.get("params") or "{}").strip()
    valid = {c[0] for c in REPORT_CARDS}
    if report_key not in valid or not name:
        flash("Give the template a name.", "error")
        return redirect(url_for("reports.hub"))
    try:
        json.loads(params)
    except ValueError:
        params = "{}"
    db.session.add(ReportTemplate(user_id=current_user.id,
                                  report_key=report_key, name=name,
                                  params_json=params))
    record("report_template_saved", f"{report_key}: {name}",
           entity="report", entity_id=report_key)
    db.session.commit()
    flash(f"Template '{name}' saved.", "success")
    return redirect(url_for("reports." + _ROUTE_NAMES[report_key]))
