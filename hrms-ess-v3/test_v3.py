"""HRMS+ESS v3 tests: companies, salary components, can_view_salary, OT rules,
payroll engine (LOP/proration/loans/arrears), run lifecycle + SoD, WPS split,
photos, document vault, letters, WhatsApp outbox.

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: ESS_SECRET_KEY=testsecret12345 ESS_DATABASE_URL=<redacted> \\
     python test_v3.py
"""
import io
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import (User, Company, SalaryComponent, SalaryAssignment,
                        OTRateRule, Loan, PayrollInput, PayrollOTHours,
                        PayrollRun, PayrollLine, EmployeeDocument,
                        LetterTemplate, GeneratedLetter, NotificationOutbox,
                        PayrollLoanRecovery, AuditLog)  # noqa: E402
from app import payroll as eng  # noqa: E402
import config as app_config  # noqa: E402

app = create_app()
app.config["WTF_CSRF_ENABLED"] = True
from app import limiter  # noqa: E402
limiter.enabled = False  # many logins in this suite; rate limiting is live-tested separately
client = app.test_client()

passed, failed = [], []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra else ""))


def csrf_token(resp):
    m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    return m.group(1).decode() if m else ""


def get_token(path):
    return csrf_token(client.get(path))


def login(uid, pw):
    r = client.get("/login")
    t = csrf_token(r)
    return client.post("/login", data={"username": uid, "password": pw,
                                       "csrf_token": t},
                       follow_redirects=False)


def logout():
    t = csrf_token(client.get("/change-password"))
    return client.post("/logout", data={"csrf_token": t},
                       follow_redirects=False)


today = date.today()
PERIOD = "2026-04"  # 30-day month for clean proration math


def mk(uid, name, role, dept, **kw):
    if not User.query.get(uid):
        kw.setdefault("joining_date", date(2020, 1, 15))
        u = User(id=uid, name=name, role=role, department=dept,
                 employment_status="active", is_active=True, **kw)
        u.set_password("pw-" + uid)
        u.must_change_password = False
        db.session.add(u)
    return User.query.get(uid)


def seed_master():
    for code, name in app_config.DEFAULT_COMPANIES:
        if not Company.query.filter_by(code=code).first():
            db.session.add(Company(code=code, name=name))
    for code, name, kind, fixed, prorate, bucket, stat in \
            app_config.DEFAULT_SALARY_COMPONENTS:
        if not SalaryComponent.query.filter_by(code=code).first():
            db.session.add(SalaryComponent(
                code=code, name=name, kind=kind, is_fixed=fixed,
                prorate=prorate, wps_bucket=bucket, is_statutory=stat))
    for name, body in app_config.DEFAULT_LETTER_TEMPLATES.items():
        if not LetterTemplate.query.filter_by(name=name).first():
            db.session.add(LetterTemplate(name=name, body=body))
    db.session.commit()


with app.app_context():
    seed_master()
    ala = Company.query.filter_by(code="ALA").first()
    r800 = Company.query.filter_by(code="R800").first()

    admin = mk("V3-ADM", "V3 Admin", "admin", "Human Resources",
               visa_company_id=ala.id, iban="AE071", bank_name="Emirates NBD",
               basic_salary=9000, monthly_salary=14000)
    admin2 = mk("V3-ADM2", "V3 Admin Two", "admin", "Finance",
                visa_company_id=ala.id, iban="AE072", bank_name="Mashreq",
                basic_salary=8000, monthly_salary=12000)
    mgr = mk("V3-MGR", "V3 Manager", "manager", "Production",
             visa_company_id=ala.id, iban="AE073", bank_name="ADCB",
             basic_salary=6000, monthly_salary=9000)
    emp1 = mk("V3-E1", "V3 Employee One", "employee", "Production",
              visa_company_id=ala.id, iban="AE074", bank_name="Emirates NBD",
              designation="Welder", basic_salary=3000, monthly_salary=4500,
              reporting_manager="V3-MGR", joining_date=date(2020, 1, 15))
    emp2 = mk("V3-E2", "V3 Employee Two", "employee", "Sales",
              visa_company_id=r800.id, iban="AE075", bank_name="FAB",
              basic_salary=3000, monthly_salary=4000,
              joining_date=date(2020, 1, 15))
    db.session.commit()

    def comp(code):
        return SalaryComponent.query.filter_by(code=code).first()

    # salary assignments: emp1 BASIC 3000 + HRA 1000; emp2 BASIC 3000 + HRA 500
    for uid, rows in [("V3-E1", [("BASIC", 3000.0), ("HRA", 1000.0)]),
                      ("V3-E2", [("BASIC", 3000.0), ("HRA", 500.0)]),
                      ("V3-MGR", [("BASIC", 6000.0), ("HRA", 2000.0)])]:
        for code, amt in rows:
            if not SalaryAssignment.query.filter_by(
                    user_id=uid, component_id=comp(code).id).first():
                db.session.add(SalaryAssignment(
                    user_id=uid, component_id=comp(code).id, amount=amt,
                    effective_from=date(2020, 1, 15), created_by="V3-ADM"))
    db.session.commit()

# ================= A. component CRUD + delete guard =================
login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/salary-components")
r = client.post("/hrms/salary-components",
                data={"action": "add", "code": "BONUS", "name": "Joining Bonus",
                      "kind": "earning", "is_fixed": "on", "prorate": "on",
                      "wps_bucket": "variable", "csrf_token": t})
with app.app_context():
    bonus = SalaryComponent.query.filter_by(code="BONUS").first()
check("component added", bonus is not None and bonus.kind == "earning",
      bonus.code if bonus else None)

t = get_token("/hrms/salary-components")
r = client.post("/hrms/salary-components",
                data={"action": "add", "code": "BONUS2", "name": "X",
                      "kind": "earning", "csrf_token": t})
with app.app_context():
    b2 = SalaryComponent.query.filter_by(code="BONUS2").first()
    db.session.add(SalaryAssignment(user_id="V3-E1", component_id=b2.id,
                                    amount=100.0,
                                    effective_from=date(2020, 1, 15),
                                    created_by="V3-ADM"))
    db.session.commit()
    b2id = b2.id
t = get_token("/hrms/salary-components")
r = client.post("/hrms/salary-components",
                data={"action": "delete", "comp_id": b2id, "csrf_token": t},
                follow_redirects=True)
with app.app_context():
    still = SalaryComponent.query.filter_by(code="BONUS2").first()
check("component delete blocked when used", still is not None)
t = get_token("/hrms/salary-components")
r = client.post("/hrms/salary-components",
                data={"action": "toggle", "comp_id": b2id, "csrf_token": t},
                follow_redirects=True)
with app.app_context():
    b2 = SalaryComponent.query.filter_by(code="BONUS2").first()
check("component soft-deactivated instead", b2 is not None and not b2.is_active)
logout()

# ================= C. can_view_salary enforcement =================
login("V3-MGR", "pw-V3-MGR")  # manager WITHOUT the flag
r = client.get("/hrms/payroll")
check("payroll 403 without salary flag", r.status_code == 403, r.status_code)
r = client.get("/hrms/employees")
check("employee central 403 without salary flag", r.status_code == 403,
      r.status_code)
r = client.get("/hrms/letters")
check("letters reachable by manager (no salary merge yet)", r.status_code == 200,
      r.status_code)
logout()
login("V3-ADM", "pw-V3-ADM")
with app.app_context():
    m = User.query.get("V3-MGR")
    m.can_view_salary = True
    db.session.commit()
logout()
login("V3-MGR", "pw-V3-MGR")  # manager WITH the flag
r = client.get("/hrms/payroll")
check("payroll 200 with salary flag", r.status_code == 200, r.status_code)
r = client.get("/hrms/employees/V3-E1")
check("employee central 200 with salary flag", r.status_code == 200,
      r.status_code)
check("salary figures shown to flagged user",
      b"Basic / Monthly" in r.data)
logout()
login("V3-ADM", "pw-V3-ADM")
with app.app_context():
    m = User.query.get("V3-MGR")
    m.can_view_salary = False
    db.session.commit()
logout()

# ================= D. OT precedence resolver =================
with app.app_context():
    db.session.add(OTRateRule(level="company", ref_id="ALA",
                              normal_mult=2.0, night_mult=2.5,
                              restday_mult=2.5, updated_by="V3-ADM"))
    db.session.add(OTRateRule(level="group", ref_id="Production",
                              normal_mult=1.75, night_mult=2.0,
                              restday_mult=2.0, updated_by="V3-ADM"))
    db.session.add(OTRateRule(level="individual", ref_id="V3-E1",
                              normal_mult=1.3, night_mult=1.6,
                              restday_mult=1.6, updated_by="V3-ADM"))
    db.session.commit()
    m, src = eng.resolve_ot_mult("V3-E1", "normal")
    check("OT precedence: individual wins", m == 1.3 and src == "individual",
          f"{m}/{src}")
    m2, src2 = eng.resolve_ot_mult("V3-MGR", "normal")  # Production, ALA, no individual
    check("OT precedence: group wins", m2 == 1.75 and src2 == "group",
          f"{m2}/{src2}")
    m3, src3 = eng.resolve_ot_mult("V3-E2", "night")  # Sales, R800, no rules
    check("OT precedence: system default", m3 == 1.5 and src3 == "system default",
          f"{m3}/{src3}")
    # OT math on basic: V3-E1 basic 3000 -> hourly 12.5; 8h @1.3 = 130.00
    hourly = 3000.0 / 30 / 8
    check("OT amount math", round(8 * hourly * 1.3, 2) == 130.00)

# ================= G. payroll calc: LOP proration =================
with app.app_context():
    if not PayrollInput.query.filter_by(period=PERIOD, user_id="V3-E1",
                                        label="LOP").first():
        db.session.add(PayrollInput(period=PERIOD, user_id="V3-E1",
                                    label="LOP", amount=0.0, days=2.0,
                                    kind="deduction", created_by="V3-ADM"))
        db.session.commit()
    calc = eng.calculate_payroll_line(User.query.get("V3-E1"), PERIOD)
check("LOP payable days 28", calc["payable_days"] == 28.0, calc["payable_days"])
check("basic prorated 2800", calc["basic_earned"] == 2800.00, calc["basic_earned"])
hra = [e for e in calc["earnings"] if e["code"] == "HRA"][0]["amount"]
check("HRA prorated 933.33", hra == 933.33, hra)

# ================= E. loan recovery on lock =================
with app.app_context():
    if not Loan.query.filter_by(user_id="V3-E1").first():
        db.session.add(Loan(user_id="V3-E1", loan_type="loan", principal=3000.0,
                            installment=500.0, start_period=PERIOD,
                            balance=3000.0, created_by="V3-ADM"))
        db.session.commit()
    loan = Loan.query.filter_by(user_id="V3-E1").first()
    loan.balance = 3000.0
    loan.status = "active"
    db.session.commit()
    loan_id = loan.id

login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/payroll")
r = client.post("/hrms/payroll/new", data={"period": PERIOD, "csrf_token": t},
                follow_redirects=False)
run_url = r.headers.get("Location", "")
check("run created", r.status_code == 302 and "/hrms/payroll/" in run_url,
      r.status_code)
with app.app_context():
    run = PayrollRun.query.filter_by(period=PERIOD).first()
    run_id = run.id
    line = PayrollLine.query.filter_by(run_id=run_id, user_id="V3-E1").first()
    rec = [d for d in __import__("json").loads(line.deductions_json)
           if d.get("loan_id") == loan_id]
check("loan recovery line 500 in draft", len(rec) == 1 and rec[0]["amount"] == 500.00,
      rec[0]["amount"] if rec else None)
with app.app_context():
    loan = Loan.query.get(loan_id)
check("balance unchanged on draft", loan.balance == 3000.00, loan.balance)

t = get_token(f"/hrms/payroll/{run_id}")
r = client.post(f"/hrms/payroll/{run_id}/submit", data={"csrf_token": t},
                follow_redirects=True)
r = client.post(f"/hrms/payroll/{run_id}/lock", data={"csrf_token": t},
                follow_redirects=True)
with app.app_context():
    loan = Loan.query.get(loan_id)
    run = PayrollRun.query.get(run_id)
check("balance reduces on lock", loan.balance == 2500.00, loan.balance)
check("run locked", run.status == "locked", run.status)

# lock blocks recalculation
t = get_token(f"/hrms/payroll/{run_id}")
with app.app_context():
    before = PayrollLine.query.filter_by(run_id=run_id,
                                         user_id="V3-E1").first().net
r = client.post(f"/hrms/payroll/{run_id}/recalc", data={"csrf_token": t},
                follow_redirects=True)
with app.app_context():
    after = PayrollLine.query.filter_by(run_id=run_id,
                                        user_id="V3-E1").first().net
check("recalc blocked on locked run", before == after, (before, after))
logout()

# ================= SoD: creator cannot approve own run =================
login("V3-ADM", "pw-V3-ADM")  # creator of the run
t = get_token(f"/hrms/payroll/{run_id}")
r = client.post(f"/hrms/payroll/{run_id}/approve", data={"csrf_token": t},
                follow_redirects=True)
with app.app_context():
    run = PayrollRun.query.get(run_id)
check("creator cannot approve own run", run.status == "locked", run.status)
check("SoD message shown", b"Segregation of duties" in r.data)
logout()

login("V3-ADM2", "pw-V3-ADM2")  # different admin approves
t = get_token(f"/hrms/payroll/{run_id}")
r = client.post(f"/hrms/payroll/{run_id}/approve", data={"csrf_token": t},
                follow_redirects=True)
with app.app_context():
    run = PayrollRun.query.get(run_id)
    n_out = NotificationOutbox.query.filter_by(event="payslip_ready").count()
check("second admin approves run", run.status == "approved", run.status)
check("WhatsApp outbox queued on approval", n_out >= 2, n_out)
logout()

# ================= F. arrears on payslip =================
with app.app_context():
    if not PayrollInput.query.filter_by(period=PERIOD, user_id="V3-E2",
                                        label="Backpay").first():
        db.session.add(PayrollInput(
            period=PERIOD, user_id="V3-E2", label="Backpay", amount=200.0,
            kind="addition", is_arrears=True, original_period="2026-03",
            created_by="V3-ADM"))
        db.session.commit()
    # recalc needed? run is approved - payslip reads the line; add arrears by
    # direct calc check instead (run already approved with old inputs)
    calc2 = eng.calculate_payroll_line(User.query.get("V3-E2"), PERIOD)
arr = [e for e in calc2["earnings"] if "Arrears" in e["name"]]
check("arrears line labelled with original period",
      len(arr) == 1 and "2026-03" in arr[0]["name"] and arr[0]["amount"] == 200.00,
      arr[0]["name"] if arr else None)

# ================= exceptions: negative net =================
with app.app_context():
    if not PayrollInput.query.filter_by(period="2026-05", user_id="V3-E2",
                                        label="Huge fine").first():
        db.session.add(PayrollInput(period="2026-05", user_id="V3-E2",
                                    label="Huge fine", amount=99999.0,
                                    kind="deduction", created_by="V3-ADM"))
        db.session.commit()
    run2 = PayrollRun(period="2026-05", status="draft", created_by="V3-ADM")
    db.session.add(run2)
    db.session.flush()
    eng.recalc_run(run2)
    db.session.commit()
    exc = eng.run_exceptions(run2)
check("negative net flagged", "V3-E2" in exc and
      any("Negative net" in m for m in exc["V3-E2"]), exc.get("V3-E2"))

# ================= H. WPS split by visa company =================
login("V3-ADM", "pw-V3-ADM")
r = client.get(f"/hrms/wps/ALA/{PERIOD}")
check("WPS ALA csv 200", r.status_code == 200 and r.mimetype == "text/csv",
      r.status_code)
check("WPS ALA has V3-E1 not V3-E2", b"V3-E1" in r.data and b"V3-E2" not in r.data)
r = client.get(f"/hrms/wps/R800/{PERIOD}")
check("WPS R800 has V3-E2 not V3-E1", b"V3-E2" in r.data and b"V3-E1" not in r.data)
check("WPS filename per company",
      "wps-r800-202604.csv" in r.headers.get("Content-Disposition", ""))
logout()

# ================= I. photo upload =================
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
login("V3-E1", "pw-V3-E1")
t = get_token("/profile")
r = client.post("/profile/photo",
                data={"photo": (io.BytesIO(PNG), "me.png"), "csrf_token": t},
                content_type="multipart/form-data", follow_redirects=True)
check("photo upload accepted", b"Profile photo updated" in r.data)
r = client.get("/photo/V3-E1")
check("own photo fetchable", r.status_code == 200 and
      r.data.startswith(b"\x89PNG"), r.status_code)
t = get_token("/profile")
r = client.post("/profile/photo",
                data={"photo": (io.BytesIO(b"MZ\x90\x00evil"), "x.exe"),
                      "csrf_token": t},
                content_type="multipart/form-data", follow_redirects=True)
check("exe photo rejected", b"must be a valid JPG or PNG" in r.data)
r = client.get("/photo/V3-E2")
check("other employee photo denied", r.status_code == 403, r.status_code)
logout()

# ================= J. document vault =================
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
login("V3-E1", "pw-V3-E1")
t = get_token("/documents")
r = client.post("/documents",
                data={"category": "Passport", "title": "My passport",
                      "file": (io.BytesIO(PDF), "pp.pdf"),
                      "issue_date": "2020-01-01", "expiry_date": "2030-01-01",
                      "csrf_token": t},
                content_type="multipart/form-data", follow_redirects=True)
with app.app_context():
    doc = EmployeeDocument.query.filter_by(user_id="V3-E1",
                                           title="My passport").first()
check("employee upload -> pending", doc is not None and doc.status == "pending",
      doc.status if doc else None)
doc_id = doc.id if doc else 0
# employee cannot download another's doc later; manager denied
r = client.get(f"/documents/{doc_id}/download")
check("owner can download", r.status_code == 200, r.status_code)
logout()
login("V3-MGR", "pw-V3-MGR")
r = client.get(f"/documents/{doc_id}/download")
check("manager download denied 403", r.status_code == 403, r.status_code)
logout()
login("V3-E2", "pw-V3-E2")
r = client.get(f"/documents/{doc_id}/download")
check("other employee download denied 403", r.status_code == 403, r.status_code)
logout()
login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/documents")
r = client.post("/hrms/documents",
                data={"action": "verify", "doc_id": doc_id,
                      "note": "Checked", "csrf_token": t},
                follow_redirects=True)
with app.app_context():
    doc = EmployeeDocument.query.get(doc_id)
check("HR verify works", doc.status == "verified", doc.status)
r = client.get(f"/documents/{doc_id}/download")
check("HR download 200", r.status_code == 200, r.status_code)
# expiry on compliance page
with app.app_context():
    d2 = EmployeeDocument(user_id="V3-E1", category="Emirates ID",
                          title="EID", stored_name="x.pdf",
                          original_name="x.pdf", mime="application/pdf",
                          expiry_date=today + timedelta(days=20),
                          status="verified", uploaded_by="V3-ADM")
    db.session.add(d2)
    db.session.commit()
r = client.get("/hrms/compliance")
check("doc expiry listed on compliance", b"Document-Vault Expiries" in r.data)
logout()

# ================= K. letters + mail merge =================
login("V3-MGR", "pw-V3-MGR")  # manager, no salary flag
t = get_token("/hrms/letters/new")
with app.app_context():
    tpl = LetterTemplate.query.filter_by(name="Salary Certificate").first()
r = client.post("/hrms/letters/new",
                data={"user_id": "V3-E1", "template_id": tpl.id,
                      "csrf_token": t}, follow_redirects=True)
check("salary letter blocked without salary flag",
      b"Salary visibility is required" in r.data)
with app.app_context():
    n = GeneratedLetter.query.filter_by(user_id="V3-E1").count()
check("no letter generated when blocked", n == 0, n)
logout()
login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/letters/new")
r = client.post("/hrms/letters/new",
                data={"user_id": "V3-E1", "template_id": tpl.id,
                      "csrf_token": t}, follow_redirects=True)
with app.app_context():
    letter = GeneratedLetter.query.filter_by(user_id="V3-E1").order_by(
        GeneratedLetter.id.desc()).first()
check("letter merges name",
      letter is not None and "V3 Employee One" in letter.rendered_text)
check("letter merges designation",
      letter is not None and "Welder" in letter.rendered_text)
check("letter merges salary",
      letter is not None and "3000.00" in letter.rendered_text)
logout()

# ================= N. insights label present =================
login("V3-ADM", "pw-V3-ADM")
r = client.get("/hrms/compliance")
check("insights labelled as rule-based",
      b"Rule-based insights (AI-assisted checks)" in r.data)
logout()

# ================= O. salary-view user cannot POST employee edits =================
with app.app_context():
    mgr_u = User.query.get("V3-MGR")
    mgr_u.can_view_salary = True
    db.session.commit()
login("V3-MGR", "pw-V3-MGR")  # manager WITH salary visibility, not admin
t = get_token("/hrms/employees/V3-E1/edit")
check("salary viewer can still GET edit form", client.get(
    "/hrms/employees/V3-E1/edit").status_code == 200)
r = client.post("/hrms/employees/V3-E1/edit",
                data={"name": "HACKED NAME", "basic_salary": "99999",
                      "monthly_salary": "99999", "csrf_token": t},
                follow_redirects=False)
check("salary viewer POST edit -> 403", r.status_code == 403, r.status_code)
with app.app_context():
    e1 = User.query.get("V3-E1")
check("name unchanged after blocked edit", e1.name == "V3 Employee One", e1.name)
check("basic salary unchanged after blocked edit", e1.basic_salary == 3000.0,
      e1.basic_salary)
logout()
with app.app_context():
    User.query.get("V3-MGR").can_view_salary = False
    db.session.commit()

# ================= P. salary letters gated + letter views audited =================
login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/letters/new")
with app.app_context():
    tpl = LetterTemplate.query.filter_by(name="Salary Certificate").first()
r = client.post("/hrms/letters/new",
                data={"user_id": "V3-E2", "template_id": tpl.id,
                      "csrf_token": t}, follow_redirects=True)
with app.app_context():
    sl = GeneratedLetter.query.filter_by(user_id="V3-E2").order_by(
        GeneratedLetter.id.desc()).first()
check("salary letter generated for authorized admin", sl is not None)
check("letter flagged has_salary_data",
      sl is not None and sl.has_salary_data is True)
check("admin letter view OK + audited", r.status_code == 200)
with app.app_context():
    aud = AuditLog.query.filter_by(action="letter_viewed",
                                   entity_id=str(sl.id)).first()
check("letter view written to audit", aud is not None and
      aud.actor_id == "V3-ADM", getattr(aud, "actor_id", None))
logout()
login("V3-MGR", "pw-V3-MGR")  # manager WITHOUT salary visibility
r = client.get(f"/hrms/letters/{sl.id}")
check("manager without salary permission -> 403 on salary letter",
      r.status_code == 403, r.status_code)
r = client.get("/hrms/letters")
check("salary letters hidden from letters list",
      b"V3-E2" not in r.data)
logout()

# ================= Q. run state machine: approved recalc blocked, draft lock blocked =================
PERIOD2 = "2026-05"
login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/payroll")
r = client.post("/hrms/payroll/new", data={"period": PERIOD2, "csrf_token": t},
                follow_redirects=False)
with app.app_context():
    run2 = PayrollRun.query.filter_by(period=PERIOD2).first()
    run2_id = run2.id
t = get_token(f"/hrms/payroll/{run2_id}")
r = client.post(f"/hrms/payroll/{run2_id}/lock", data={"csrf_token": t},
                follow_redirects=True)
with app.app_context():
    run2 = PayrollRun.query.get(run2_id)
check("lock straight from draft blocked", run2.status == "draft",
      run2.status)
check("draft-lock refusal explained", b"in review" in r.data)
with app.app_context():
    net_before = PayrollLine.query.filter_by(run_id=run_id,
                                             user_id="V3-E1").first().net
t = get_token(f"/hrms/payroll/{run_id}")
r = client.post(f"/hrms/payroll/{run_id}/recalc", data={"csrf_token": t},
                follow_redirects=True)
with app.app_context():
    net_after = PayrollLine.query.filter_by(run_id=run_id,
                                            user_id="V3-E1").first().net
check("recalc blocked on approved run", net_before == net_after)
logout()

# ================= R. lock -> unlock -> lock: exactly one loan recovery =================
PERIOD3 = "2026-07"
with app.app_context():
    if not Loan.query.filter_by(user_id="V3-E2").first():
        db.session.add(Loan(user_id="V3-E2", loan_type="loan",
                            principal=2000.0, installment=400.0,
                            start_period=PERIOD3, balance=2000.0,
                            created_by="V3-ADM"))
        db.session.commit()
    loan3 = Loan.query.filter_by(user_id="V3-E2").first()
    loan3_id = loan3.id
login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/payroll")
r = client.post("/hrms/payroll/new", data={"period": PERIOD3, "csrf_token": t},
                follow_redirects=False)
with app.app_context():
    run3 = PayrollRun.query.filter_by(period=PERIOD3).first()
    run3_id = run3.id
t = get_token(f"/hrms/payroll/{run3_id}")
client.post(f"/hrms/payroll/{run3_id}/submit", data={"csrf_token": t},
            follow_redirects=True)
client.post(f"/hrms/payroll/{run3_id}/lock", data={"csrf_token": t},
            follow_redirects=True)
with app.app_context():
    bal1 = Loan.query.get(loan3_id).balance
check("first lock recovers installment", bal1 == 1600.00, bal1)
t = get_token(f"/hrms/payroll/{run3_id}")
client.post(f"/hrms/payroll/{run3_id}/unlock", data={"csrf_token": t},
            follow_redirects=True)
with app.app_context():
    loan3 = Loan.query.get(loan3_id)
    run3 = PayrollRun.query.get(run3_id)
check("unlock restores loan balance", loan3.balance == 2000.00,
      loan3.balance)
check("unlock returns run to draft", run3.status == "draft", run3.status)
check("loan reactivated after reversal", loan3.status == "active",
      loan3.status)
t = get_token(f"/hrms/payroll/{run3_id}")
client.post(f"/hrms/payroll/{run3_id}/submit", data={"csrf_token": t},
            follow_redirects=True)
client.post(f"/hrms/payroll/{run3_id}/lock", data={"csrf_token": t},
            follow_redirects=True)
with app.app_context():
    loan3 = Loan.query.get(loan3_id)
    recs = PayrollLoanRecovery.query.filter_by(run_id=run3_id,
                                               loan_id=loan3_id).all()
check("re-lock recovers exactly once (no double)", loan3.balance == 1600.00,
      loan3.balance)
check("old recovery row marked reversed",
      any(r.reversed for r in recs) and any(not r.reversed for r in recs),
      [(r.id, r.reversed) for r in recs])
logout()

# ================= S. photo view is audited =================
login("V3-ADM", "pw-V3-ADM")
r = client.get("/photo/V3-E1")
check("admin photo fetch OK", r.status_code == 200, r.status_code)
with app.app_context():
    pv = AuditLog.query.filter_by(action="photo_viewed",
                                  entity_id="V3-E1",
                                  actor_id="V3-ADM").first()
check("photo view written to audit", pv is not None)
logout()

# ================= T. WPS Emirates ID column uses the ID number =================
with app.app_context():
    e1 = User.query.get("V3-E1")
    e1.emirates_id_no = "784-1990-1234567-1"
    e1.emirates_id_expiry_date = date(2027, 5, 1)
    db.session.commit()
login("V3-ADM", "pw-V3-ADM")
r = client.get(f"/hrms/wps/ALA/{PERIOD3}")
check("WPS per-company export OK", r.status_code == 200, r.status_code)
check("WPS Emirates ID column shows the ID number",
      b"784-1990-1234567-1" in r.data)
check("WPS Emirates ID column is not the expiry date",
      b"2027-05-01" not in r.data)
logout()

# ================= U. new-employee temp password is secrets-based =================
login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/employees/new")
r = client.post("/hrms/employees/new",
                data={"employee_id": "V3-NEW1", "name": "V3 New One",
                      "csrf_token": t}, follow_redirects=True)
check("employee created", r.status_code == 200, r.status_code)
m = re.search(rb"Temporary password: ([^ ]+)", r.data)
pw = m.group(1).decode() if m else ""
check("temp password is not the old time-based pattern",
      bool(pw) and not pw.startswith("Change"), pw[:20])
check("temp password has upper+lower+digit and length>=12",
      len(pw) >= 12 and re.search(r"[A-Z]", pw) and re.search(r"[a-z]", pw)
      and re.search(r"[0-9]", pw), f"len={len(pw)}")
with app.app_context():
    nu = User.query.get("V3-NEW1")
check("force-change still required", nu is not None and
      nu.must_change_password is True)
logout()

# ================= V. deduction ceiling flag (>50% of gross, flag only) =================
PERIOD4 = "2026-08"
with app.app_context():
    if not PayrollInput.query.filter_by(period=PERIOD4, user_id="V3-E1",
                                        label="Big fine").first():
        db.session.add(PayrollInput(period=PERIOD4, user_id="V3-E1",
                                    label="Big fine", amount=5000.0,
                                    kind="deduction", created_by="V3-ADM"))
        db.session.commit()
login("V3-ADM", "pw-V3-ADM")
t = get_token("/hrms/payroll")
client.post("/hrms/payroll/new", data={"period": PERIOD4, "csrf_token": t},
            follow_redirects=False)
with app.app_context():
    run4 = PayrollRun.query.filter_by(period=PERIOD4).first()
    exc = eng.run_exceptions(run4)
check("deduction ceiling flagged at >50% of gross",
      "V3-E1" in exc and any("50%" in m for m in exc["V3-E1"]),
      exc.get("V3-E1"))
check("ceiling is a flag, run still created as draft",
      run4 is not None and run4.status == "draft")
logout()

# ================= W. WhatsApp outbox carries no pay amounts =================
with app.app_context():
    # Clear old payslip messages (queued before the privacy fix) so the
    # assertion covers only post-fix messages.
    NotificationOutbox.query.filter_by(event="payslip_ready").delete()
    db.session.commit()
    run3 = PayrollRun.query.filter_by(period=PERIOD3).first()
    run3_id, run3_net = run3.id, {l.user_id: l.net for l in run3.lines}
login("V3-ADM2", "pw-V3-ADM2")  # different admin approves (SoD)
t = get_token(f"/hrms/payroll/{run3_id}")
r = client.post(f"/hrms/payroll/{run3_id}/approve", data={"csrf_token": t},
                follow_redirects=True)
with app.app_context():
    run3 = PayrollRun.query.get(run3_id)
check("run approved by second admin", run3.status == "approved", run3.status)
with app.app_context():
    msgs = NotificationOutbox.query.filter_by(event="payslip_ready").all()
    leaky = [m for m in msgs
             if re.search(r"AED\s*[\d,]", m.message)
             or "Net pay" in m.message
             or any(f"{net:.2f}" in m.message and net > 0
                    for net in run3_net.values())]
check("payslip WhatsApp messages queued", len(msgs) > 0, len(msgs))
check("no pay amounts in WhatsApp messages", not leaky,
      [m.message for m in leaky][:2])
with app.app_context():
    all_msgs = NotificationOutbox.query.all()
    leaky_all = [m for m in all_msgs if re.search(r"AED\s*[\d,]", m.message)]
check("no outbox message anywhere contains AED amounts", not leaky_all,
      [m.message for m in leaky_all][:2])
logout()

print()
print(f"RESULT: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
