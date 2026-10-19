"""Sample rows for every module (seeded only when the tables are empty)."""
from datetime import date, timedelta
import json


def _seed_attendance():
    """Current-month attendance for all staff, skipping Sundays."""
    from app import db
    from app.models import AttendanceRecord, User
    import random as _random

    if AttendanceRecord.query.count() > 0:
        return
    _random.seed(42)
    today = date.today()
    year, month = today.year, today.month
    users = [u.id for u in User.query.filter_by(is_active=True).all()]
    for day in range(1, today.day + 1):
        d = date(year, month, day)
        if d.weekday() == 6:  # Sunday week-off
            continue
        for uid in users:
            r = _random.random()
            if r < 0.03:
                status, ci, co = "absent", None, None
            elif r < 0.08:
                status = "late"
                ci = f"08:{_random.randint(15, 45):02d}"
                co = f"18:{_random.randint(0, 30):02d}"
            else:
                status = "present"
                ci = f"07:{_random.randint(45, 59):02d}"
                co = f"18:{_random.randint(0, 30):02d}"
            db.session.add(AttendanceRecord(user_id=uid, date=d, check_in=ci,
                                            check_out=co, status=status))


def _seed_leave_requests():
    from app import db
    from app.models import LeaveRequest

    if LeaveRequest.query.count() > 0:
        return
    today = date.today()
    rows = [
        # 3 pending from different employees / departments / managers
        ("EMP010", "maternity", today + timedelta(days=14), today + timedelta(days=43),
         30.0, "Delivery expected end of next month.", "pending", None, None, None),
        ("EMP004", "annual", today + timedelta(days=23), today + timedelta(days=32),
         10.0, "Family visit to Kerala.", "pending", None, None, None),
        ("EMP006", "annual", today + timedelta(days=18), today + timedelta(days=25),
         8.0, "Personal work back home.", "pending", None, None, None),
        # 2 approved
        ("EMP001", "annual", today - timedelta(days=47), today - timedelta(days=43),
         5.0, "Attending cousin's wedding.", "approved", "MGR001",
         "Approved.", today - timedelta(days=40)),
        ("EMP012", "sick", today - timedelta(days=69), today - timedelta(days=67),
         3.0, "Fever, clinic certificate attached.", "approved", "MGR001",
         "Get well soon.", today - timedelta(days=65)),
        # 1 rejected
        ("EMP008", "annual", today - timedelta(days=13), today + timedelta(days=2),
         16.0, "Too long during peak production month.", "rejected", "MGR001",
         "Rejected: production schedule conflict.", today - timedelta(days=10)),
    ]
    for uid, ltype, start, end, days, reason, status, approver, comment, decided in rows:
        db.session.add(LeaveRequest(user_id=uid, leave_type=ltype,
                                    start_date=start, end_date=end, days=days,
                                    reason=reason, status=status,
                                    approver_id=approver,
                                    approver_comment=comment,
                                    decided_at=datetime_(decided) if decided else None))


def datetime_(d):
    from datetime import datetime
    return datetime(d.year, d.month, d.day, 10, 0, 0)


def _seed_regularisations():
    from app import db
    from app.models import RegularisationRequest

    if RegularisationRequest.query.count() > 0:
        return
    today = date.today()
    rows = [
        ("EMP001", today - timedelta(days=3), "08:05", "18:10",
         "Gate reader missed my check-in punch.", "pending", None, None),
        ("EMP012", today - timedelta(days=2), "07:55", "18:05",
         "Was on the factory floor before 8, forgot to punch.", "pending", None, None),
        ("EMP004", today - timedelta(days=5), "08:10", "17:55",
         "Late due to road diversion near Industrial Area 10.", "approved", "MGR001",
         today - timedelta(days=4)),
    ]
    for uid, d, ci, co, reason, status, approver, decided in rows:
        db.session.add(RegularisationRequest(
            user_id=uid, date=d, corrected_in=ci, corrected_out=co,
            reason=reason, status=status, approver_id=approver,
            decided_at=datetime_(decided) if decided else None))


def _seed_candidates():
    from app import db
    from app.models import Candidate

    if Candidate.query.count() > 0:
        return
    rows = [
        ("Ahmed Saleem", "Welder", "Applied", "Indeed", "+971501234567"),
        ("Deepak Yadav", "Metal Grinder", "Screening", "Referral", "+971502345678"),
        ("Jose Mathew", "Accountant", "Interview", "LinkedIn", "+971503456789"),
        ("Shabeer Ali", "HR Assistant", "Offer", "Indeed", "+971504567890"),
        ("Babu Lal", "Driver", "Joined", "Walk-in", "+971505678901"),
        ("Ibrahim Kutty", "Sales Executive", "Rejected", "Indeed", "+971506789012"),
    ]
    for name, position, stage, source, phone in rows:
        db.session.add(Candidate(name=name, position=position, stage=stage,
                                 source=source, phone=phone,
                                 email=f"{name.split()[0].lower()}@example.net",
                                 notes="Demo candidate.", created_by="HRADMIN"))


def _seed_onboarding():
    from app import db
    from app.models import OnboardingCase, OnboardingTask, ONBOARDING_CHECKLIST_ITEMS

    if OnboardingCase.query.count() > 0:
        return
    from datetime import datetime
    today = date.today()
    # Case 1: the recent joiner (~1 month ago), ~half the tasks done.
    case1 = OnboardingCase(name="Sara Thomas",
                           joining_date=today - timedelta(days=30),
                           employee_id="EMP016",
                           notes="New QC Inspector, started this month.",
                           created_by="HRADMIN")
    db.session.add(case1)
    db.session.flush()
    for i, label in enumerate(ONBOARDING_CHECKLIST_ITEMS):
        done = i < 4
        db.session.add(OnboardingTask(
            case_id=case1.id, label=label, is_done=done,
            done_at=datetime.utcnow() if done else None))
    # Case 2: fresh joiner, 1 of 8 tasks done.
    case2 = OnboardingCase(name="Anwar Sulaiman",
                           joining_date=today + timedelta(days=21),
                           notes="Joining as Helper next month.",
                           created_by="HRADMIN")
    db.session.add(case2)
    db.session.flush()
    for i, label in enumerate(ONBOARDING_CHECKLIST_ITEMS):
        done = i == 0
        db.session.add(OnboardingTask(
            case_id=case2.id, label=label, is_done=done,
            done_at=datetime.utcnow() if done else None))


def _seed_grievances():
    from app import db
    from app.models import Grievance

    if Grievance.query.count() > 0:
        return
    db.session.add(Grievance(
        user_id="EMP008", category="Salary & Benefits",
        subject="Overtime payment for last month not reflected",
        description="I worked overtime on three days last month but the extra "
                    "hours are not shown on my payslip.",
        is_anonymous=False, status="under_review"))
    db.session.add(Grievance(
        user_id="EMP003", category="Workplace Conduct",
        subject="Noise levels in the packing area",
        description="The packing area is very noisy during night shifts; "
                    "ear protection was requested.",
        is_anonymous=False, status="resolved",
        resolution="Ear protection issued to all packing staff on 2026-09-20. "
                   "Closed."))


def _seed_evaluations():
    from app import db
    from app.models import Evaluation
    import config as app_config

    if Evaluation.query.count() > 0:
        return

    def scores(ratings):
        return json.dumps([
            {"criterion": c, "rating": r, "comment": "Meets Expectations", "note": ""}
            for c, r in zip(app_config.EVAL_CRITERIA, ratings)])

    n = len(app_config.EVAL_CRITERIA)
    # EMP001: completed and acknowledged.
    r1 = [4] * n
    db.session.add(Evaluation(
        user_id="EMP001", period="2026 H2", evaluator_id="MGR001",
        scores_json=scores(r1), overall=round(sum(r1) / n, 2),
        remarks="Consistent welding quality; meets production targets.",
        acknowledged=True))
    # EMP004: completed, awaiting employee acknowledgment.
    r2 = [5, 4, 4, 4, 4, 4, 3, 4]
    db.session.add(Evaluation(
        user_id="EMP004", period="2026 H2", evaluator_id="MGR001",
        scores_json=scores(r2), overall=round(sum(r2) / n, 2),
        remarks="Strong CNC setup skills; working on initiative.",
        acknowledged=False))


# ===================== v3 extras (idempotent) =====================

def _seed_v3_extras(demo_users, iban_fn):
    """Companies, salary components + assignments, loan, docs, letter templates."""
    from datetime import date, timedelta
    from app import db
    from app.models import (User, Company, SalaryComponent, SalaryAssignment,
                            Loan, EmployeeDocument, LetterTemplate)
    from app.files import store_file
    import config as app_config

    today = date.today()

    # --- visa companies ---
    if Company.query.count() == 0:
        comps = {}
        for code, name in app_config.DEFAULT_COMPANIES:
            c = Company(code=code, name=name)
            db.session.add(c)
            comps[code] = c
        db.session.flush()
        codes = list(comps.keys())
        for i, spec in enumerate(demo_users):
            u = User.query.get(spec["uid"])
            if u and not getattr(u, "visa_company_id", None):
                u.visa_company_id = comps[codes[i % len(codes)]].id
        db.session.flush()

    # --- salary components ---
    if SalaryComponent.query.count() == 0:
        compmap = {}
        for code, name, kind, fixed, prorate, bucket, stat in \
                app_config.DEFAULT_SALARY_COMPONENTS:
            c = SalaryComponent(code=code, name=name, kind=kind,
                                is_fixed=fixed, prorate=prorate,
                                wps_bucket=bucket, is_statutory=stat)
            db.session.add(c)
            compmap[code] = c
        db.session.flush()
        # assignments for all 20: BASIC + HRA + TRANSPORT split of monthly-basic
        for spec in demo_users:
            basic = spec["basic"]
            rest = max(spec["monthly"] - basic, 0.0)
            hra = round(rest * 0.6, 2)
            transport = round(rest - hra, 2)
            eff = today - timedelta(days=spec["joined"])
            for code, amt in (("BASIC", basic), ("HRA", hra),
                              ("TRANSPORT", transport)):
                db.session.add(SalaryAssignment(
                    user_id=spec["uid"], component_id=compmap[code].id,
                    amount=amt, effective_from=eff,
                    note="Demo seed", created_by="HRADMIN"))
        db.session.flush()

    # --- one active loan (EMP004) ---
    if Loan.query.count() == 0:
        period = today.strftime("%Y-%m")
        db.session.add(Loan(user_id="EMP004", loan_type="loan",
                            principal=3000.0, installment=500.0,
                            start_period=period, balance=3000.0,
                            note="Demo seed loan", created_by="HRADMIN"))
        db.session.flush()

    # --- letter templates ---
    if LetterTemplate.query.count() == 0:
        for name, body in app_config.DEFAULT_LETTER_TEMPLATES.items():
            db.session.add(LetterTemplate(name=name, body=body,
                                          updated_by="HRADMIN"))
        db.session.flush()

    # --- demo documents: 3 verified for EMP001, 1 pending for EMP002 ---
    if EmployeeDocument.query.count() == 0:
        pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
               b"trailer<</Root 1 0 R>>\n%%EOF")
        rows = [
            ("EMP001", "Passport", "Passport copy - Demo Employee One",
             today - timedelta(days=400), today + timedelta(days=200),
             "verified", "HRADMIN"),
            ("EMP001", "Emirates ID", "Emirates ID copy - Demo Employee One",
             today - timedelta(days=300), today + timedelta(days=100),
             "verified", "HRADMIN"),
            ("EMP001", "Education Certificate", "ITI certificate",
             today - timedelta(days=900), None, "verified", "HRADMIN"),
            ("EMP002", "Passport", "Passport copy - Demo Employee Two",
             None, today + timedelta(days=45), "pending", "EMP002"),
        ]
        for uid, cat, title, issue, expiry, status, by in rows:
            doc = EmployeeDocument(
                user_id=uid, category=cat, title=title,
                stored_name=store_file(pdf, "docs", "pdf"),
                original_name=f"{cat.lower().replace(' ', '_')}.pdf",
                mime="application/pdf",
                issue_date=issue, expiry_date=expiry, status=status,
                uploaded_by=by,
                verified_by="HRADMIN" if status == "verified" else None)
            db.session.add(doc)
        db.session.flush()


# ---------------- v3.1: retail registers + separation records ----------------
def _seed_v31_extras():
    """Sample data for the v3.1 retail-fixture registers and separations.

    Idempotent: seeds only when the module tables are empty, and never
    changes the 20 demo users or their statuses.
    """
    from app.models import (Certification, Project, Deployment,
                            FacilityAssignment, PPEIssue, HSEIncident,
                            ContractorWorker, SeparationRecord, AssetCustody)
    from app import db
    if Certification.query.first():
        return
    today = date.today()

    certs = [
        # one certification expiring within 60 days (Forklift, +30 days)
        dict(user_id="EMP001", name="Welding Trade", issuer="Demo Technical Institute",
             issue_date=today - timedelta(days=400), expiry_date=today + timedelta(days=400)),
        dict(user_id="EMP004", name="Forklift Operation", issuer="Demo Safety Academy",
             issue_date=today - timedelta(days=700), expiry_date=today + timedelta(days=30)),
        dict(user_id="EMP007", name="Heavy Vehicle Driving Licence",
             issuer="RTA Dubai", issue_date=today - timedelta(days=500),
             expiry_date=today + timedelta(days=500)),
        dict(user_id="EMP008", name="Fire Safety", issuer="Demo Safety Academy",
             issue_date=today - timedelta(days=800), expiry_date=today + timedelta(days=100)),
    ]
    for c in certs:
        db.session.add(Certification(created_by="HRADMIN", **c))

    p1 = Project(name="Store Fit-Out — Dubai Mall Client", client="Demo Retail LLC",
                 location="Dubai Mall, Dubai", status="active", created_by="HRADMIN")
    p2 = Project(name="Warehouse Racking — UAQ", client="Demo Logistics Co",
                 location="Umm Al Quwain", status="active", created_by="HRADMIN")
    db.session.add_all([p1, p2])
    db.session.flush()
    db.session.add_all([
        Deployment(user_id="EMP001", project_id=p1.id,
                   from_date=today - timedelta(days=60), to_date=None,
                   site_allowance=300.0, created_by="HRADMIN"),
        Deployment(user_id="EMP004", project_id=p1.id,
                   from_date=today - timedelta(days=60), to_date=None,
                   site_allowance=300.0, created_by="HRADMIN"),
        Deployment(user_id="EMP015", project_id=p2.id,
                   from_date=today - timedelta(days=30), to_date=None,
                   site_allowance=200.0, created_by="HRADMIN"),
    ])

    db.session.add_all([
        FacilityAssignment(user_id="EMP001", kind="accommodation",
                           location_or_route="Labour Camp A — Room 12",
                           monthly_cost=250.0,
                           from_date=today - timedelta(days=300), to_date=None,
                           created_by="HRADMIN"),
        FacilityAssignment(user_id="EMP004", kind="transport",
                           location_or_route="Bus Route 3 — Sharjah to UAQ",
                           monthly_cost=150.0,
                           from_date=today - timedelta(days=300), to_date=None,
                           created_by="HRADMIN"),
        FacilityAssignment(user_id="EMP007", kind="accommodation",
                           location_or_route="Labour Camp B — Room 04",
                           monthly_cost=250.0,
                           from_date=today - timedelta(days=200), to_date=None,
                           created_by="HRADMIN"),
    ])

    db.session.add_all([
        PPEIssue(user_id="EMP001", item="Safety Helmet", size="M", qty=1,
                 issued_date=today - timedelta(days=90), created_by="HRADMIN"),
        PPEIssue(user_id="EMP001", item="Safety Shoes", size="42", qty=1,
                 issued_date=today - timedelta(days=90), created_by="HRADMIN"),
        PPEIssue(user_id="EMP004", item="Welding Gloves", size="L", qty=2,
                 issued_date=today - timedelta(days=60), created_by="HRADMIN"),
        PPEIssue(user_id="EMP015", item="Hi-Vis Vest", size="XL", qty=1,
                 issued_date=today - timedelta(days=45), created_by="HRADMIN"),
    ])

    db.session.add_all([
        AssetCustody(user_id="EMP003", item_name="DeWalt Cordless Drill",
                     category="Tool", serial_no="DCD-2201", replacement_value=850.0,
                     issued_date=today - timedelta(days=120), status="Issued",
                     created_by="HRADMIN"),
        AssetCustody(user_id="EMP008", item_name="Laptop — Lenovo ThinkPad",
                     category="IT Asset", serial_no="LT-5540",
                     replacement_value=3200.0,
                     issued_date=today - timedelta(days=300),
                     returned_date=today - timedelta(days=10), status="Returned",
                     created_by="HRADMIN"),
        AssetCustody(user_id="EMP004", item_name="Welding Machine Inverter",
                     category="Tool", serial_no="WM-8871", replacement_value=2400.0,
                     issued_date=today - timedelta(days=200), status="Issued",
                     created_by="HRADMIN"),
    ])

    db.session.add(HSEIncident(
        date=today - timedelta(days=20), location="Factory Floor — Welding Bay",
        employee_id=None, type="Near Miss", severity="Low",
        description="Welding spark landed near gas cylinder storage area.",
        action_taken="Toolbox talk conducted; cylinders relocated to the "
                     "designated cage.",
        status="Closed", created_by="HRADMIN"))

    db.session.add_all([
        ContractorWorker(name="Suresh Menon", company="Demo Manpower Supply",
                         trade="Welder", site="UAQ Factory",
                         start_date=today - timedelta(days=60), end_date=None,
                         monthly_rate=1800.0, status="active",
                         created_by="HRADMIN"),
        ContractorWorker(name="Anil Das", company="Demo Manpower Supply",
                         trade="Grinder", site="UAQ Factory",
                         start_date=today - timedelta(days=30), end_date=None,
                         monthly_rate=1500.0, status="active",
                         created_by="HRADMIN"),
    ])

    year = today.year
    db.session.add_all([
        SeparationRecord(employee_code="AA901", employee_name="Ravi Shankar",
                         department="Production", designation="Welder",
                         joining_date=date(year - 5, 3, 1),
                         separation_date=date(year, 2, 15),
                         separation_type="Resignation",
                         reason="Better opportunity",
                         notice_days=30, final_settlement_status="Settled",
                         settlement_amount=8500.0, created_by="HRADMIN"),
        SeparationRecord(employee_code="AA902", employee_name="Joseph DSouza",
                         department="Sales", designation="Sales Executive",
                         joining_date=date(year - 4, 6, 10),
                         separation_date=date(year, 5, 20),
                         separation_type="Termination",
                         reason="Performance",
                         notice_days=30, final_settlement_status="In Process",
                         settlement_amount=0.0, created_by="HRADMIN"),
        SeparationRecord(employee_code="AA903", employee_name="Lakshmi Nair",
                         department="Admin", designation="Receptionist",
                         joining_date=date(year - 3, 1, 15),
                         separation_date=date(year, 8, 1),
                         separation_type="Resignation",
                         reason="Relocation",
                         notice_days=30, final_settlement_status="Pending",
                         settlement_amount=0.0, created_by="HRADMIN"),
    ])


def _seed_v32_extras():
    """Shift roster + final settlement demo data (v3.2).

    Idempotent: seeds only when the module tables are empty, and never
    changes the 20 demo users or their statuses. Ramadan dates are left
    empty on purpose (never fake religious dates).
    """
    from app.models import (ShiftPattern, RosterSettings, RosterAssignment,
                            FinalSettlement, User)
    from app import db
    if not ShiftPattern.query.first():
        for code, name, st, et in (
                ("GEN", "General Shift", "08:00", "18:00"),
                ("MORN", "Morning Shift", "06:00", "14:00"),
                ("EVE", "Evening Shift", "14:00", "22:00"),
                ("NIGHT", "Night Shift", "22:00", "06:00")):
            db.session.add(ShiftPattern(
                code=code, name=name, start_time=st, end_time=et,
                break_minutes=60, grace_minutes=10))
        db.session.flush()
    if db.session.get(RosterSettings, 1) is None:
        db.session.add(RosterSettings(id=1))  # Ramadan dates left empty
    if not RosterAssignment.query.first():
        pats = {p.code: p for p in ShiftPattern.query.all()}
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        for uid, pcode in (
                ("EMP001", "GEN"), ("EMP002", "GEN"), ("EMP003", "GEN"),
                ("EMP004", "GEN"), ("EMP005", "GEN"), ("EMP008", "MORN"),
                ("EMP015", "MORN"), ("EMP011", "EVE"), ("EMP007", "NIGHT"),
                ("EMP014", "NIGHT")):
            if db.session.get(User, uid) is None:
                continue
            for d in range(7):
                db.session.add(RosterAssignment(
                    user_id=uid, date=monday + timedelta(days=d),
                    shift_id=pats[pcode].id, created_by="HRADMIN"))
    if not FinalSettlement.query.first():
        today = date.today()
        lwd = date(today.year, today.month, 1) - timedelta(days=1)
        db.session.add(FinalSettlement(
            user_id="EMP013", last_working_day=lwd,
            separation_type="Resignation", reason="Demo draft settlement",
            status="draft", notice_days=0, notice_direction="none",
            created_by="HRADMIN"))
    db.session.flush()


# ===================== HRMS+ESS V3.3 seed extras =====================

def _demo_signature_png():
    """Tiny synthetic signature image (demo only)."""
    import io as _io
    from PIL import Image as _Image, ImageDraw as _Draw
    img = _Image.new("RGB", (400, 160), "white")
    d = _Draw.Draw(img)
    d.line([(40, 105), (110, 60), (170, 108), (235, 66), (300, 96), (350, 80)],
           fill="black", width=3)
    d.line([(50, 125), (330, 130)], fill="black", width=2)
    buf = _io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


_WELDER_JD = """JOB PURPOSE
Perform MIG/TIG welding on mild steel and stainless steel components for retail fixture manufacturing, meeting drawing tolerances and quality standards.

EDUCATION / QUALIFICATION
- ITI / diploma in welding technology or equivalent trade certificate.
- Minimum 2 years of experience in structural or sheet-metal welding.

ACCOUNTABILITIES
1. Read and interpret fabrication drawings and welding symbols.
2. Set up welding machines, jigs and fixtures for production jobs.
3. Perform MIG/TIG welding on mild steel and SS as per WPS.
4. Grind and finish welds to the required surface quality.
5. Conduct first-piece inspection and report deviations to the supervisor.
6. Follow all HSE rules: PPE, hot-work permits, fume extraction.
7. Maintain daily production records and consumable usage.

JOB NATURE
Full-time, factory-based (Umm Al Quwain). Shift work as rostered.
"""

_HR_EXEC_JD = """JOB PURPOSE
Support the HR Manager in recruitment, onboarding, attendance administration and employee records for factory and office staff.

EDUCATION / QUALIFICATION
- Bachelor's degree in Human Resources, Business Administration or related field.
- 2+ years of HR generalist experience, preferably in manufacturing.

ACCOUNTABILITIES
1. Maintain accurate employee records in the HRMS (joining, exits, documents).
2. Administer attendance, leave requests and regularisations.
3. Coordinate recruitment: screening, interview scheduling, offer documentation.
4. Support onboarding: induction plans, document collection, ID creation.
5. Prepare HR letters, certificates and compliance reports.
6. Handle employee queries on leave, salary and company policies.
7. Uphold strict confidentiality of employee and salary data.

JOB NATURE
Full-time, office-based (Sharjah). Reports to the HR Manager.
"""


def _seed_v33_extras():
    """Work locations, training LMS seed, a notice, and 2 demo JDs."""
    from app import db
    from app.models import (User, WorkLocation, TrainingCourse, QuizQuestion,
                            TrainingAssignment, TrainingAttempt, Notice,
                            Notification, JobDescription, JDAcknowledgement,
                            Certification)
    from app.files import store_file
    from datetime import datetime

    today = date.today()

    # --- work location (demo coordinates, clearly editable) ---
    if WorkLocation.query.count() == 0:
        db.session.add(WorkLocation(
            name="Head Office — Sharjah (Demo)",
            latitude=25.3054, longitude=55.4248, radius_m=300,
            is_active=True, created_by="HRADMIN"))

    # --- training courses ---
    if TrainingCourse.query.count() == 0:
        pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
               b"trailer<</Root 1 0 R>>\n%%EOF")
        c1 = TrainingCourse(
            title="Safety Induction", category="Induction",
            description=("Mandatory safety induction for all staff: PPE, "
                         "emergency exits, fire safety, hot-work permits."),
            material_kind="file",
            material_path=store_file(pdf, "training", "pdf"),
            pass_percent=70, issue_cert_name="HSE Safety Induction",
            cert_valid_months=12, created_by="HRADMIN")
        db.session.add(c1)
        db.session.flush()
        for i, (q, opts, ci) in enumerate([
            ("What must you wear before entering the welding bay?",
             ["Safety helmet and goggles", "Sandals", "Shorts", "Nothing"],
             0),
            ("In case of fire, you should:",
             ["Run immediately", "Raise the alarm and use the nearest exit",
              "Hide", "Continue working"], 1),
            ("A hot-work permit is required for:",
             ["Desk work", "Welding and grinding", "Meetings", "Lunch"], 1),
        ]):
            db.session.add(QuizQuestion(course_id=c1.id, question=q,
                                        options_json=json.dumps(opts),
                                        correct_index=ci, sort=i))
        c2 = TrainingCourse(
            title="Company Orientation", category="Orientation",
            description="Company history, values, policies and ESS usage.",
            material_kind="link",
            material_url="https://example.com/orientation-demo",
            pass_percent=70, created_by="HRADMIN")
        db.session.add(c2)
        db.session.flush()
        for u in User.query.filter_by(is_active=True).all():
            db.session.add(TrainingAssignment(course_id=c1.id, user_id=u.id,
                                              assigned_by="HRADMIN"))
        db.session.add(TrainingAttempt(
            course_id=c1.id, user_id="EMP001", score_percent=100.0,
            passed=True, answers_json="[0, 1, 1]"))
        if not Certification.query.filter_by(
                user_id="EMP001", name="HSE Safety Induction").first():
            db.session.add(Certification(
                user_id="EMP001", name="HSE Safety Induction",
                issuer="Internal Training", issue_date=today,
                expiry_date=today + timedelta(days=365),
                created_by="HRADMIN"))

    # --- a demo notice (mandatory read) ---
    if Notice.query.count() == 0:
        n = Notice(
            title="Welcome to the new Employee Self-Service",
            body=("This portal is now your home for leave requests, "
                  "attendance, payslips, training, celebrations and more. "
                  "Please read this notice and acknowledge it."),
            audience="all", requires_ack=True, created_by="HRADMIN")
        db.session.add(n)
        db.session.flush()
        for u in User.query.filter_by(is_active=True).all():
            db.session.add(Notification(
                user_id=u.id, kind="notice", title=n.title, body=n.body,
                link=f"/notices/{n.id}", ref_id=n.id))

    # --- demo job descriptions ---
    if JobDescription.query.count() == 0:
        jd1 = JobDescription(title="Welder", department="Production",
                             reports_to="Production Manager", body=_WELDER_JD,
                             created_by="HRADMIN")
        jd2 = JobDescription(title="HR Executive", department="Human Resources",
                             reports_to="HR Manager", body=_HR_EXEC_JD,
                             created_by="HRADMIN")
        db.session.add_all([jd1, jd2])
        db.session.flush()
        for u in User.query.filter(User.designation == "Welder",
                                   User.is_active.is_(True)).all():
            db.session.add(JDAcknowledgement(
                jd_id=jd1.id, user_id=u.id, jd_version=jd1.version,
                assigned_by="HRADMIN"))
            db.session.add(Notification(
                user_id=u.id, kind="jd",
                title=f"Your Job Description is ready: {jd1.title}",
                body="Please read and acknowledge with your signature.",
                link="/my-jd"))
        # pre-acknowledge one welder so the HR dashboard shows both states
        first = JDAcknowledgement.query.filter_by(jd_id=jd1.id).first()
        if first:
            sig = _demo_signature_png()
            first.signature_path = store_file(sig, "signatures", "jpg")
            first.typed_name = "Demo Employee"
            first.signer_ip = "127.0.0.1"
            first.status = "acknowledged"
            first.jd_version = jd1.version
            first.acknowledged_at = datetime.utcnow()

    db.session.flush()


def _seed_v34_extras():
    """v3.4 approval matrix: HR team flags + demo approval chains.

    Idempotent: flags are backfilled for existing users, and the demo
    chains are re-applied (only in demo mode).
    """
    from app import db
    from app.models import User
    from app.approvals import set_demo_matrices
    hradmin = db.session.get(User, "HRADMIN")
    if hradmin is not None and not hradmin.is_hr_manager:
        hradmin.is_hr_manager = True
    fatima = db.session.get(User, "EMP005")
    if fatima is not None and not fatima.is_hr_staff:
        fatima.is_hr_staff = True
    db.session.flush()
    set_demo_matrices()


def _seed_v331_extras():
    """v3.3.1 ESS extras: demo expense claims, letter requests.

    Idempotent: rows are seeded only when the tables are empty.
    """
    from app import db
    from app.models import (User, ExpenseClaim, LetterRequest,
                            LetterTemplate, GeneratedLetter, PayrollInput)
    from app.approvals import start_instance
    from app.engagement import notify_user
    from app.audit import record as audit_record
    from app.files import store_file
    today = date.today()
    pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
           b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
           b"trailer<</Root 1 0 R>>\n%%EOF")

    if ExpenseClaim.query.count() == 0:
        # EMP003: pending claim, sitting at step 1 of the demo expense chain
        # (reporting_manager = MGR001, then hr_staff).
        receipt = store_file(pdf, "expenses", "pdf")
        c1 = ExpenseClaim(user_id="EMP003", claim_date=today,
                          category="Fuel", amount=245.50,
                          description="Site visits fuel — demo",
                          receipt_path=receipt, status="pending")
        db.session.add(c1)
        db.session.flush()
        start_instance("expense", c1.id, "EMP003", "EMP003")
        # EMP004: approved claim with its payroll input already created
        # (the engine path is exercised by the test suite).
        c2 = ExpenseClaim(user_id="EMP004", claim_date=today,
                          category="Materials", amount=480.00,
                          description="Grinding discs — demo",
                          status="approved", decided_by="HRADMIN",
                          decision_comment="Demo seed")
        db.session.add(c2)
        db.session.flush()
        period = today.strftime("%Y-%m")
        inp = PayrollInput(period=period, user_id="EMP004",
                           kind="addition",
                           label=f"Expense reimbursement (claim #{c2.id})",
                           amount=480.00, created_by="HRADMIN",
                           note="Demo seed expense reimbursement")
        db.session.add(inp)
        db.session.flush()
        c2.payroll_input_id = inp.id
        notify_user("EMP004", "expense", "Expense claim approved",
                    body=f"Claim #{c2.id} (Materials, AED 480.00) approved — "
                         f"added to payroll inputs for {period}.",
                    link="/my-expenses")
        audit_record("expense_approved",
                     f"claim #{c2.id} for EMP004: AED 480.00 -> payroll "
                     f"input {inp.id} ({period}) [demo seed]",
                     entity="expense", entity_id=c2.id,
                     field_name="status", old_value="pending",
                     new_value="approved")
        db.session.commit()

    if LetterRequest.query.count() == 0:
        # EMP002: salary certificate request, still submitted.
        db.session.add(LetterRequest(user_id="EMP002",
                                    letter_type="salary_certificate",
                                    purpose="Bank account opening — demo",
                                    status="submitted"))
        # EMP001: employment certificate request, fulfilled via mail merge.
        lr = LetterRequest(user_id="EMP001",
                           letter_type="employment_certificate",
                           purpose="Visa file — demo", status="ready",
                           processed_by="HRADMIN")
        db.session.add(lr)
        db.session.flush()
        tpl = LetterTemplate.query.filter_by(
            name="Employment Certificate", is_active=True).first()
        if tpl is not None:
            from app.payroll_views import merge_placeholders
            user = db.session.get(User, "EMP001")
            letter = GeneratedLetter(
                user_id="EMP001", template_id=tpl.id,
                rendered_text=merge_placeholders(tpl.body, user),
                has_salary_data=False, generated_by="HRADMIN")
            db.session.add(letter)
            db.session.flush()
            lr.generated_letter_id = letter.id
        notify_user("EMP001", "letter", "Your letter is ready",
                    body="Your Employment Certificate (request "
                         f"#{lr.id}) is ready to download.",
                    link="/my-letters")
        db.session.commit()


# ===================== HRMS+ESS V3.5 seed extras =====================

def _seed_v35_extras():
    """v3.5 demo seed: helpdesk tickets + a pending shift swap.

    Idempotent: rows are seeded only when the tables are empty.
    Demo content: 2 helpdesk tickets (EMP007 open IT ticket; EMP002 resolved
    HR ticket with a public comment) and 1 shift swap in pending_counterpart
    between EMP007 and EMP008.
    """
    from datetime import datetime, timedelta
    from app import db
    from app.models import (HelpdeskTicket, TicketComment, ShiftSwapRequest,
                            RosterAssignment, ShiftPattern)
    from app.engagement import notify_user
    from app.audit import record as audit_record

    gst_today = (datetime.utcnow() + timedelta(hours=4)).date()

    if HelpdeskTicket.query.count() == 0:
        t1 = HelpdeskTicket(
            requester_id="EMP007", category="IT",
            subject="VPN not connecting",
            description="The site office VPN drops every 20 minutes. "
                        "Please check the router - demo ticket.",
            priority="Normal", status="open")
        db.session.add(t1)
        t2 = HelpdeskTicket(
            requester_id="EMP002", category="HR",
            subject="Payslip copy request",
            description="Please share a signed copy of my last payslip - "
                        "demo ticket.",
            priority="Low", status="resolved",
            resolved_at=datetime.utcnow())
        db.session.add(t2)
        db.session.flush()
        db.session.add(TicketComment(
            ticket_id=t2.id, author_id="HRADMIN",
            body="Signed copy attached to your document vault - "
                 "demo resolution.", is_internal=False))
        notify_user("EMP002", "ticket", f"Ticket #{t2.id} resolved",
                    f"Your ticket #{t2.id} ({t2.subject}) has been "
                    f"resolved.", link=f"/my-tickets/{t2.id}")
    db.session.flush()

    if ShiftSwapRequest.query.count() == 0:
        pats = {p.code: p for p in ShiftPattern.query.all()}

        def future_assign(uid):
            return (RosterAssignment.query
                    .filter(RosterAssignment.user_id == uid,
                            RosterAssignment.date >= gst_today)
                    .order_by(RosterAssignment.date).first())

        a = future_assign("EMP007")
        b = future_assign("EMP008")
        if a is None or b is None or a.shift_id == b.shift_id:
            # Deterministic fallback: next Monday, explicit patterns.
            nm = gst_today + timedelta(days=(7 - gst_today.weekday()))
            if nm <= gst_today:
                nm += timedelta(days=7)
            a = RosterAssignment.query.filter_by(
                user_id="EMP007", date=nm).first()
            if a is None:
                a = RosterAssignment(user_id="EMP007", date=nm,
                                    shift_id=pats["NIGHT"].id,
                                    created_by="HRADMIN")
                db.session.add(a)
            b = RosterAssignment.query.filter_by(
                user_id="EMP008", date=nm).first()
            if b is None:
                b = RosterAssignment(user_id="EMP008", date=nm,
                                    shift_id=pats["MORN"].id,
                                    created_by="HRADMIN")
                db.session.add(b)
            db.session.flush()
        if a.shift_id != b.shift_id:
            swap = ShiftSwapRequest(
                requester_id="EMP007", my_assignment_id=a.id,
                counterpart_user_id="EMP008", their_assignment_id=b.id,
                my_pattern_id=a.shift_id, their_pattern_id=b.shift_id,
                status="pending_counterpart")
            db.session.add(swap)
            db.session.flush()
            audit_record("shift_swap_created",
                         f"swap #{swap.id} (demo seed): EMP007 <-> EMP008",
                         entity="shift_swap", entity_id=swap.id)
            notify_user(
                "EMP008", "shift_swap", "Shift swap request",
                "Demo Employee Seven wants to swap shifts with you. "
                "Open My Shift Swaps to respond.", link="/my-swaps",
                category="important")
    db.session.flush()
