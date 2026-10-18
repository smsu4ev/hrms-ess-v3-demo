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
