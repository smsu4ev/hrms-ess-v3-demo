#!/usr/bin/env python3
"""HRMS+ESS v3 — Production data migration: load REAL staff data from the HR master xlsx.

Usage:
    ESS_SECRET_KEY=<production-key> \\
        python tools/migrate_real_data.py --xlsx /path/to/staff.xlsx \\
            --db /var/lib/hrms/ess.db --cutover 2026-10-01 [--force]

SAFETY RULES (non-negotiable):
- The loader REFUSES to write to a database file that already contains users,
  unless --force is given. With --force it further REFUSES if the target DB
  resolves inside this app/repo tree (production DB must live outside the code).
- ONLY rows with Status == "Active" become User records. Everything else is
  reported as skipped-by-status.
- The generated REPORT contains counts and Employee-ID lists for gaps only.
  It never contains names, salaries, IBANs or other personal data.
- This script is meant to run ONCE against the production VM. It is fixture-tested
  only; never run it against the real file from this workstation.

Mapping (HR master headers -> app model, app/models.py):
    User.id             = Employee ID (login username is the Employee ID)
    User.name           = Name (En)
    initial password    = DOB formatted DDMMYYYY via the app's own set_password()
                          (werkzeug scrypt); must_change_password=True always
    User.role           = "employee" (AA670 is bootstrapped to admin, see below)
    User.employment_status = "active", is_active=True
    User.department     = Department, User.designation = Designation
    User.joining_date   = Date of join
    User.phone / User.mobile = Mobile No.
    User.email          = Official Email, falling back to Personal Email
    User.bank_name      = Bank Name, User.iban = IBAN No.
    User.date_of_birth  = DOB
    User.nationality    = Nationality
    User.emirates_id_no = Emirates ID
    User.passport_no    = Passport No.
    User.basic_salary / User.monthly_salary = Basic Salary / Monthly Salary
    reporting_manager   = (no source column) left blank — HR assigns after cutover
    Company (visa companies master): one row per distinct "Visa Company" value;
        code is derived deterministically from the name: strip punctuation and
        corporate suffixes (LLC/LTD/FZE/EST/CO/INC...), then take the first letter
        of the first three remaining words, upper-cased
        (e.g. "Al-Ameen Industries LLC" -> "AAI"); a numeric suffix is appended on
        collision (AAI2). name = the full value from the file.
    SalaryAssignment (effective --cutover): component BASIC = Basic Salary; if
        Monthly Salary > Basic Salary, a second line OTHER_ALLOW = Monthly-Basic
        is created (the "allowance split" the company pays above basic).
        Components are created if missing. Rows with missing/zero basic are
        skipped (IDs in the report).
    Admin bootstrap: if Employee ID "AA670" is among the loaded actives, that
    user becomes role="admin", is_hr_manager=True (must_change_password stays
    True so even the admin must set their own password on first login).
    Leave balances: deliberately NOT invented — left unset so system defaults
    apply. Loans/advances: none created; HR must enter opening balances if any.
    Columns with no model field (Name (Arb), Username, Attendance ID, Employment
    Type, Working Company, Workflow Group, Marital Status, Gender, Blood Group,
    Week Offs, Work Hours, Has Commission, Has Pension, Has Overtime,
    Commence From, Require Attendance, Payment Mode, File No., UID No.,
    Personal ID No., Labour Card No.) are skipped and listed in the report.
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime

# ---- resolve the app tree (this file lives in <root>/tools/) ----------------
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

REQUIRED_HEADERS = [
    "Employee ID", "Name (En)", "Name (Arb)", "Username", "Attendance ID",
    "Employment Type", "Working Company", "Visa Company", "Department",
    "Designation", "Workflow Group", "Nationality", "Gender", "DOB",
    "Blood Group", "Marital Status", "Official Email", "Personal Email",
    "Mobile No.", "Date of join", "Payment Mode", "Bank Name", "IBAN No.",
    "File No.", "UID No.", "Passport No.", "Personal ID No.",
    "Labour Card No.", "Emirates ID", "Week Offs", "Work Hours",
    "Has Commission", "Has Pension", "Has Overtime", "Commence From",
    "Require Attendance", "Basic Salary", "Monthly Salary", "Status",
]

SKIPPED_COLUMNS = [
    "Name (Arb)", "Username", "Attendance ID", "Employment Type",
    "Working Company", "Workflow Group", "Marital Status", "Gender",
    "Blood Group", "Week Offs", "Work Hours", "Has Commission",
    "Has Pension", "Has Overtime", "Commence From", "Require Attendance",
    "Payment Mode", "File No.", "UID No.", "Personal ID No.",
    "Labour Card No.",
]

SKIP_WORDS = {"LLC", "LTD", "LTDA", "FZE", "FZ", "EST", "INC",
              "LLP", "LLC.", "LTD."}
# "Co"/"Company" is deliberately NOT skipped: in a name like "Visa Co" it is a
# real word, and dropping it would silently shorten initials (TV vs TVC).


def fail(msg):
    print(f"MIGRATION REFUSED: {msg}", file=sys.stderr)
    sys.exit(2)


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def clean_str(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("", "none", "nan", "n/a", "-") else s


def parse_date(v):
    """Accept datetime/date cells and common string forms; return date or None."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d/%m/%y",
                "%Y-%m-%d", "%d.%m.%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(v):
    try:
        f = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0
    return f if f > 0 else 0.0


def company_code(name, taken):
    """Deterministic short code from a company name (see module docstring)."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    words = [w for w in words if w.upper() not in SKIP_WORDS]
    base = "".join(w[0].upper() for w in words[:3]) or "CO"
    code, i = base, 2
    while code in taken:
        code = f"{base}{i}"
        i += 1
    taken.add(code)
    return code


def main():
    ap = argparse.ArgumentParser(description="Load real staff data into a production DB.")
    ap.add_argument("--xlsx", required=True, help="Path to the staff master xlsx")
    ap.add_argument("--db", required=True, help="Target SQLite DB file path")
    ap.add_argument("--cutover", required=True, help="Salary effective date YYYY-MM-DD")
    ap.add_argument("--force", action="store_true",
                    help="Allow writing to a DB that already has users "
                         "(still refused inside the repo tree)")
    ap.add_argument("--report", default=None, help="Report JSON path")
    args = ap.parse_args()

    if not os.path.isfile(args.xlsx):
        fail(f"xlsx not found: {args.xlsx}")
    try:
        cutover = datetime.strptime(args.cutover, "%Y-%m-%d").date()
    except ValueError:
        fail(f"--cutover must be YYYY-MM-DD, got {args.cutover!r}")

    db_path = os.path.abspath(os.path.expanduser(args.db))
    repo_root = os.path.realpath(APP_ROOT)

    # --- safety: existing data ----------------------------------------------
    if args.force and os.path.realpath(db_path).startswith(repo_root + os.sep):
        fail("with --force, the target DB must live OUTSIDE the repo tree "
             f"(got {db_path}); production data must never sit inside the code tree")

    if os.environ.get("ESS_SECRET_KEY", "") in ("", "dev-only-change-me-immediately"):
        warn("ESS_SECRET_KEY is unset or the dev default — production must use a "
             "generated key (see deploy/setup-production.sh)")

    # --- boot the app against the TARGET database -----------------------------
    os.environ["ESS_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    from app import create_app, db  # noqa: E402
    app = create_app()

    with app.app_context():
        db.create_all()
        from app.models import (  # noqa: E402
            User, Company, SalaryComponent, SalaryAssignment)

        existing = User.query.count()
        if existing and not args.force:
            fail(f"target DB already has {existing} user(s) — refusing to write "
                 "without --force (re-run with --force only if you mean to "
                 "append to an existing production DB)")

        # --- read the workbook -------------------------------------------------
        from openpyxl import load_workbook  # noqa: E402
        wb = load_workbook(args.xlsx, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            fail("workbook has no rows")
        header = [clean_str(c) for c in rows[0]]
        missing = [h for h in REQUIRED_HEADERS if h not in header]
        if missing:
            fail(f"workbook header missing columns: {missing}")
        idx = {h: header.index(h) for h in REQUIRED_HEADERS}

        data_rows = [r for r in rows[1:] if any(r)]
        report = {
            "xlsx": os.path.basename(args.xlsx),
            "db": db_path,
            "cutover": args.cutover,
            "rows_read": len(data_rows),
            "actives_loaded": 0,
            "skipped_by_status": 0,
            "visa_companies_created": [],
            "salary_assignments": 0,
            "other_allow_splits": 0,
            "missing_dob_skipped_ids": [],
            "missing_iban_active_ids": [],
            "missing_basic_ids": [],
            "duplicate_employee_ids": [],
            "aa670_found": False,
            "fields_skipped": SKIPPED_COLUMNS,
            "leave_balances_note": "Not invented — system defaults apply. HR to "
                                   "enter opening balances if needed.",
            "loans_note": "No opening loan/advance balances imported. HR must "
                          "enter them if any exist.",
        }

        # --- ensure salary components ----------------------------------------
        def ensure_component(code, name, kind="earning"):
            c = SalaryComponent.query.filter_by(code=code).first()
            if not c:
                c = SalaryComponent(code=code, name=name, kind=kind,
                                    is_fixed=True, prorate=True,
                                    wps_bucket="basic" if code == "BASIC"
                                    else "variable")
                db.session.add(c)
                db.session.flush()
            return c

        comp_basic = ensure_component("BASIC", "Basic Salary")
        comp_other = ensure_component("OTHER_ALLOW", "Other Allowance")

        # --- visa company master ---------------------------------------------
        companies = {}  # name -> Company row
        taken_codes = {c.code for c in Company.query.all()}
        for c in Company.query.all():
            companies.setdefault(c.name, c)

        seen_ids = set()

        def get_company(name):
            name = clean_str(name)
            if not name:
                return None
            if name in companies:
                return companies[name]
            code = company_code(name, taken_codes)
            c = Company(code=code, name=name, is_active=True)
            db.session.add(c)
            db.session.flush()
            companies[name] = c
            report["visa_companies_created"].append({"code": code, "name": name})
            return c

        # --- load rows ----------------------------------------------------------
        admin_id = None
        for r in data_rows:
            def cell(h):
                return r[idx[h]]

            emp_id = clean_str(cell("Employee ID"))
            status = clean_str(cell("Status")).lower()
            if not emp_id:
                continue
            if status != "active":
                report["skipped_by_status"] += 1
                continue
            if emp_id in seen_ids or User.query.get(emp_id):
                report["duplicate_employee_ids"].append(emp_id)
                continue

            dob = parse_date(cell("DOB"))
            if not dob:
                report["missing_dob_skipped_ids"].append(emp_id)
                continue  # no DOB => no initial password by design

            seen_ids.add(emp_id)
            comp = get_company(cell("Visa Company"))
            email = clean_str(cell("Official Email")) or clean_str(cell("Personal Email"))
            mobile = clean_str(cell("Mobile No."))
            iban = clean_str(cell("IBAN No."))
            if not iban:
                report["missing_iban_active_ids"].append(emp_id)

            basic = parse_money(cell("Basic Salary"))
            monthly = parse_money(cell("Monthly Salary"))
            if not basic:
                report["missing_basic_ids"].append(emp_id)

            u = User(
                id=emp_id,
                name=clean_str(cell("Name (En)")) or emp_id,
                department=clean_str(cell("Department")) or None,
                designation=clean_str(cell("Designation")) or None,
                joining_date=parse_date(cell("Date of join")),
                employment_status="active",
                nationality=clean_str(cell("Nationality")) or None,
                emirates_id_no=clean_str(cell("Emirates ID")) or None,
                passport_no=clean_str(cell("Passport No.")) or None,
                date_of_birth=dob,
                email=email or None,
                mobile=mobile or None,
                phone=mobile or None,
                bank_name=clean_str(cell("Bank Name")) or None,
                iban=iban or None,
                visa_company_id=comp.id if comp else None,
                basic_salary=basic,
                monthly_salary=monthly,
                role="employee",
                is_active=True,
                must_change_password=True,
            )
            u.set_password(f"{dob.day:02d}{dob.month:02d}{dob.year}")
            db.session.add(u)
            report["actives_loaded"] += 1

            if basic:
                db.session.add(SalaryAssignment(
                    user_id=emp_id, component_id=comp_basic.id,
                    amount=basic, effective_from=cutover,
                    note=f"Migration cutover {args.cutover}"))
                report["salary_assignments"] += 1
                if monthly > basic:
                    db.session.add(SalaryAssignment(
                        user_id=emp_id, component_id=comp_other.id,
                        amount=round(monthly - basic, 2),
                        effective_from=cutover,
                        note=f"Migration cutover {args.cutover} (monthly-basic split)"))
                    report["salary_assignments"] += 1
                    report["other_allow_splits"] += 1

        db.session.flush()

        # --- admin bootstrap ----------------------------------------------------
        aa670 = User.query.get("AA670")
        if aa670:
            aa670.role = "admin"
            aa670.is_hr_manager = True
            admin_id = "AA670"
            report["aa670_found"] = True

        # backfill created_by on the salary assignments
        if admin_id:
            for sa in SalaryAssignment.query.filter(
                    SalaryAssignment.note.like("Migration cutover%")).all():
                sa.created_by = admin_id

        db.session.commit()

        # --- report -------------------------------------------------------------
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = args.report or os.path.join(
            os.path.dirname(db_path), f"migration_report_{ts}.json")
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        print("\n=== Migration complete ===")
        print(f"rows read:            {report['rows_read']}")
        print(f"actives loaded:       {report['actives_loaded']}")
        print(f"skipped (non-active): {report['skipped_by_status']}")
        print(f"visa companies:       {len(report['visa_companies_created'])} "
              f"({', '.join(c['code'] for c in report['visa_companies_created'])})")
        print(f"salary assignments:   {report['salary_assignments']} "
              f"(OTHER_ALLOW splits: {report['other_allow_splits']})")
        print(f"missing DOB (skipped):{len(report['missing_dob_skipped_ids'])}")
        print(f"missing IBAN actives: {len(report['missing_iban_active_ids'])}")
        print(f"missing basic:        {len(report['missing_basic_ids'])}")
        print(f"duplicates:           {len(report['duplicate_employee_ids'])}")
        print(f"AA670 admin found:    {report['aa670_found']}")
        print(f"report JSON:          {report_path}")


if __name__ == "__main__":
    main()
