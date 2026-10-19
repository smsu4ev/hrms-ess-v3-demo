"""Fixture test for tools/migrate_real_data.py.

Builds a SYNTHETIC 4-row xlsx (same headers as the real staff master), runs the
loader against a throwaway SQLite DB and asserts the full migration contract.
Uses no real employee data.

Run: rm -f /tmp/test_migrate.db && ESS_SECRET_KEY=testsecret12345 \\
ESS_DATABASE_URL=sqlite:////tmp/test_migrate.db \\
    ~/workspace/ess-app/venv/bin/python tests/test_migrate.py
(or from the repo root: python test_migrate.py)
"""
import json
import os
import re
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from openpyxl import Workbook  # noqa: E402

HEADERS = [
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
H = {h: i for i, h in enumerate(HEADERS)}


def row(**kw):
    r = [""] * len(HEADERS)
    for k, v in kw.items():
        r[H[k]] = v
    return r


DB = "/tmp/test_migrate.db"
XLSX = "/tmp/test_migrate_fixture.xlsx"
REPORT = "/tmp/test_migrate_report.json"

# synthetic fixture rows — fake people only
wb = Workbook()
ws = wb.active
ws.append(HEADERS)
ws.append(row(**{
    "Employee ID": "AA670", "Name (En)": "Test Admin", "Visa Company": "Test Visa Co LLC",
    "Department": "Human Resources", "Designation": "HR Manager",
    "Nationality": "Indian", "DOB": date(1990, 5, 15),
    "Official Email": "admin@example.com", "Mobile No.": "0501111111",
    "Date of join": date(2020, 1, 10), "Bank Name": "Test Bank",
    "IBAN No.": "AE070000000000000000001",
    "Passport No.": "P000001", "Emirates ID": "784-1990-0000001-1",
    "Basic Salary": 3000, "Monthly Salary": 5000, "Status": "Active",
}))
ws.append(row(**{
    "Employee ID": "AA999", "Name (En)": "Test Employee", "Visa Company": "Test Visa Co LLC",
    "Department": "Production", "Designation": "Welder",
    "Nationality": "Pakistani", "DOB": date(1985, 1, 2),
    "Official Email": "", "Personal Email": "personal@example.com",
    "Mobile No.": "0502222222", "Date of join": date(2021, 6, 1),
    "Bank Name": "Test Bank", "IBAN No.": "",  # missing IBAN
    "Basic Salary": 2500, "Monthly Salary": 2500, "Status": "Active",
}))
ws.append(row(**{
    "Employee ID": "AA888", "Name (En)": "Test Ex Staff", "Visa Company": "Test Visa Co LLC",
    "DOB": date(1980, 3, 3), "Basic Salary": 2000, "Monthly Salary": 2000,
    "Status": "Resigned",  # must be skipped
}))
ws.append(row(**{
    "Employee ID": "AA1000", "Name (En)": "Test No Salary",
    "Visa Company": "Other Sponsoring LLC", "Department": "Sales",
    "Designation": "Sales Executive", "DOB": date(1992, 11, 30),
    "Official Email": "sales@example.com", "Mobile No.": "0503333333",
    "Date of join": date(2023, 2, 1), "Basic Salary": 0, "Monthly Salary": 0,
    "IBAN No.": "AE070000000000000000002", "Status": "Active",
}))
wb.save(XLSX)

for f in (DB, REPORT):
    if os.path.exists(f):
        os.remove(f)

passed, failed = [], []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra else ""))


env = dict(os.environ)
env["ESS_SECRET_KEY"] = "testsecret12345"
env["ESS_DATABASE_URL"] = f"sqlite:///{DB}"

# 1) loader refuses to run against a DB path inside the repo tree with --force
r = subprocess.run(
    [sys.executable, os.path.join(ROOT, "tools", "migrate_real_data.py"),
     "--xlsx", XLSX, "--db", os.path.join(ROOT, "ess.db"),
     "--cutover", "2026-10-01", "--force"],
    env=env, capture_output=True, text=True)
check("refuses --force inside repo tree", r.returncode == 2, r.stderr.strip()[:80])

# 2) main load
r = subprocess.run(
    [sys.executable, os.path.join(ROOT, "tools", "migrate_real_data.py"),
     "--xlsx", XLSX, "--db", DB, "--cutover", "2026-10-01",
     "--report", REPORT],
    env=env, capture_output=True, text=True)
check("loader exits 0", r.returncode == 0, r.stderr.strip()[:200])

rep = json.load(open(REPORT))
check("report: rows_read=4", rep["rows_read"] == 4)
check("report: actives_loaded=3", rep["actives_loaded"] == 3)
check("report: skipped_by_status=1", rep["skipped_by_status"] == 1)
check("report: salary_assignments=3", rep["salary_assignments"] == 3)
check("report: other_allow_splits=1", rep["other_allow_splits"] == 1)
check("report: missing_iban=[AA999]", rep["missing_iban_active_ids"] == ["AA999"])
check("report: missing_basic=[AA1000]", rep["missing_basic_ids"] == ["AA1000"])
check("report: aa670_found", rep["aa670_found"] is True)
check("report: one visa company created from Test rows + one from Other",
      len(rep["visa_companies_created"]) == 2)
check("report: fields_skipped lists Labour Card No",
      "Labour Card No." in rep["fields_skipped"])
blob = json.dumps(rep)
check("report has no personal data (no names/IBANs)",
      "Test Admin" not in blob and "AE070000000000000000001" not in blob
      and "personal@example.com" not in blob)

# 3) second run without --force is refused (DB already has users)
r = subprocess.run(
    [sys.executable, os.path.join(ROOT, "tools", "migrate_real_data.py"),
     "--xlsx", XLSX, "--db", DB, "--cutover", "2026-10-01"],
    env=env, capture_output=True, text=True)
check("refuses re-run on populated DB without --force", r.returncode == 2)

# 4) DB-level assertions via the app
from app import create_app, db  # noqa: E402
from app.models import User, Company, SalaryComponent, SalaryAssignment  # noqa: E402

app = create_app()
with app.app_context():
    check("3 users loaded", User.query.count() == 3)
    check("resigned AA888 skipped", User.query.get("AA888") is None)

    adm = User.query.get("AA670")
    check("AA670 is admin", adm.role == "admin")
    check("AA670 is_hr_manager", adm.is_hr_manager is True)
    check("AA670 must_change_password", adm.must_change_password is True)
    check("AA670 DOB password set", adm.check_password("15051990"))
    check("AA670 wrong password fails", not adm.check_password("15051991"))
    check("AA670 emirates ID mapped", adm.emirates_id_no == "784-1990-0000001-1")
    check("AA670 passport mapped", adm.passport_no == "P000001")
    check("AA670 joined mapped", adm.joining_date == date(2020, 1, 10))

    emp = User.query.get("AA999")
    check("AA999 email fallback to personal",
          emp.email == "personal@example.com")
    check("AA999 phone mapped", emp.phone == "0502222222")
    check("AA999 IBAN empty", not emp.iban)

    co = Company.query.filter(Company.name == "Test Visa Co LLC").first()
    check("visa company created", co is not None)
    check("visa company code deterministic initials",
          co.code == "TVC", co.code)
    check("users linked to visa company",
          adm.visa_company_id == co.id and emp.visa_company_id == co.id)

    def amt(uid, code):
        comp = SalaryComponent.query.filter_by(code=code).one()
        sa = SalaryAssignment.query.filter_by(user_id=uid,
                                             component_id=comp.id).one_or_none()
        return sa.amount if sa else None

    check("AA670 BASIC=3000", amt("AA670", "BASIC") == 3000)
    check("AA670 OTHER_ALLOW=2000", amt("AA670", "OTHER_ALLOW") == 2000)
    check("AA999 BASIC=2500", amt("AA999", "BASIC") == 2500)
    check("AA999 no OTHER_ALLOW split", amt("AA999", "OTHER_ALLOW") is None)
    check("AA1000 no salary at all", amt("AA1000", "BASIC") is None)

    sas = SalaryAssignment.query.all()
    check("assignment cutover dates", all(s.effective_from == date(2026, 10, 1)
                                          for s in sas))
    check("assignments created_by AA670",
          all(s.created_by == "AA670" for s in sas))

    # 5) login via the test client with the DOB password -> forced change
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    r0 = client.get("/login")
    m = re.search(rb'name="csrf_token" value="([^"]+)"', r0.data)
    t = m.group(1).decode()
    r1 = client.post("/login",
                     data={"username": "AA670", "password": "15051990",
                           "csrf_token": t}, follow_redirects=False)
    check("DOB login redirects to change-password",
          r1.status_code == 302 and "/change-password" in r1.headers["Location"])

print()
print(f"V-MIGRATE: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
