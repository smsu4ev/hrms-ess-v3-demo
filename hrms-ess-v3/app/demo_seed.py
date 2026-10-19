"""Demo data for FREE SAMPLE deployments only (FAKE/synthetic data).

Active only when the environment variable ESS_SEED_DEMO=1 is set (the
Render free sample sets it in render.yaml). On app startup it populates a
full sample company — 20 users plus sample rows for every module — so that
HRADMIN/MGR001/EMP001 can log in and every screen has realistic demo data.
Idempotent: missing users are created, and module tables are seeded only
when empty.

NEVER enable ESS_SEED_DEMO on a production deployment or with real data.
All names, salaries, IBANs, dates of birth and documents here are invented.
"""
import calendar as calmod
import random
from datetime import date, datetime, timedelta

DEMO_PASSWORD = "Demo@123"

# uid, name, department, designation, role, reporting_manager,
# joined_days_ago, employment_status, nationality,
# basic_salary, monthly_salary, visa_days, passport_days, eid_days,
# bank, leave_used {type: days}
DEMO_USERS = [
    dict(uid="HRADMIN", name="Demo HR Admin", department="Human Resources",
         designation="HR Manager", role="admin", mgr=None,
         joined=1460, status="active", nationality="Demo",
         basic=9000.0, monthly=14000.0, visa=400, passport=500, eid=600,
         bank="Emirates NBD", used={}),
    dict(uid="MGR001", name="Demo Manager", department="Production",
         designation="Production Manager", role="manager", mgr="HRADMIN",
         joined=1095, status="active", nationality="Demo",
         basic=8000.0, monthly=12000.0, visa=400, passport=500, eid=600,
         bank="Mashreq", used={}),
    dict(uid="EMP001", name="Demo Employee One", department="Production",
         designation="Welder", role="employee", mgr="MGR001",
         joined=1095, status="active", nationality="Demo",
         basic=2500.0, monthly=4200.0, visa=400, passport=500, eid=600,
         bank="Emirates NBD", used={"annual": 5.0, "sick": 1.0}),
    dict(uid="EMP002", name="Demo Employee Two", department="Production",
         designation="Metal Grinder", role="employee", mgr="MGR001",
         joined=1095, status="active", nationality="Demo",
         basic=2000.0, monthly=3400.0, visa=10, passport=100, eid=400,
         bank="ADCB", used={"sick": 2.0}),
    dict(uid="EMP003", name="Demo Employee Three", department="Sales",
         designation="Sales Executive", role="employee", mgr="MGR001",
         joined=730, status="active", nationality="Demo",
         basic=3500.0, monthly=6000.0, visa=400, passport=500, eid=600,
         bank="FAB", used={}),
    dict(uid="MGR002", name="Priya Nair", department="Finance",
         designation="Finance Manager", role="manager", mgr="HRADMIN",
         joined=2375, status="active", nationality="India",
         basic=8500.0, monthly=13000.0, visa=500, passport=600, eid=700,
         bank="Emirates NBD", used={"annual": 12.0}),
    dict(uid="MGR003", name="Khalid Mansoor", department="Sales",
         designation="Sales Manager", role="manager", mgr="HRADMIN",
         joined=2240, status="active", nationality="Pakistan",
         basic=8000.0, monthly=12000.0, visa=500, passport=600, eid=700,
         bank="Mashreq", used={"annual": 8.0}),
    dict(uid="EMP004", name="Rajesh Nair", department="Production",
         designation="CNC Operator", role="employee", mgr="MGR001",
         joined=2315, status="active", nationality="India",
         basic=2500.0, monthly=4200.0, visa=400, passport=50, eid=400,
         bank="ADCB", used={"annual": 7.0, "sick": 2.0}),
    dict(uid="EMP005", name="Fatima Noor", department="Human Resources",
         designation="HR Executive", role="employee", mgr="HRADMIN",
         joined=2025, status="active", nationality="Philippines",
         basic=4500.0, monthly=7000.0, visa=400, passport=500, eid=20,
         bank="FAB", used={"annual": 4.0}),
    dict(uid="EMP006", name="Sunil Kumar", department="Finance",
         designation="Accountant", role="employee", mgr="MGR002",
         joined=2175, status="active", nationality="India",
         basic=4000.0, monthly=6500.0, visa=400, passport=500, eid=400,
         bank="Emirates NBD", used={"annual": 3.0}),
    dict(uid="EMP007", name="Anwar Hussain", department="Logistics",
         designation="Driver", role="employee", mgr="HRADMIN",
         joined=1640, status="active", nationality="Pakistan",
         basic=1400.0, monthly=2400.0, visa=55, passport=400, eid=500,
         bank="Mashreq", used={}),
    dict(uid="EMP008", name="Vikram Singh", department="Production",
         designation="Painter", role="employee", mgr="MGR001",
         joined=1155, status="active", nationality="India",
         basic=2000.0, monthly=3400.0, visa=400, passport=500, eid=400,
         bank="ADCB", used={"sick": 1.0}),
    dict(uid="EMP009", name="Ramesh Perera", department="Production",
         designation="Store Keeper", role="employee", mgr="MGR001",
         joined=900, status="active", nationality="Sri Lanka",
         basic=2200.0, monthly=3600.0, visa=25, passport=400, eid=500,
         bank="FAB", used={}),
    dict(uid="EMP010", name="Ayesha Khan", department="Sales",
         designation="Sales Executive", role="employee", mgr="MGR003",
         joined=160, status="probation", nationality="Pakistan",
         basic=3500.0, monthly=6000.0, visa=300, passport=400, eid=45,
         bank="Emirates NBD", used={}),
    dict(uid="EMP011", name="John D'Souza", department="Information Technology",
         designation="IT Support", role="employee", mgr="HRADMIN",
         joined=777, status="active", nationality="India",
         basic=4500.0, monthly=7500.0, visa=400, passport=500, eid=400,
         bank="Mashreq", used={"annual": 2.0}),
    dict(uid="EMP012", name="Prakash Rao", department="Production",
         designation="Supervisor", role="employee", mgr="MGR001",
         joined=2340, status="active", nationality="India",
         basic=3200.0, monthly=5200.0, visa=400, passport=500, eid=400,
         bank="ADCB", used={"annual": 10.0, "sick": 4.0}),
    dict(uid="EMP013", name="Nadia Rahman", department="Administration",
         designation="Procurement Officer", role="employee", mgr="HRADMIN",
         joined=1920, status="active", nationality="Bangladesh",
         basic=4200.0, monthly=6800.0, visa=400, passport=500, eid=400,
         bank="FAB", used={"annual": 6.0}),
    dict(uid="EMP014", name="Carlos Santos", department="Logistics",
         designation="Driver", role="employee", mgr="HRADMIN",
         joined=2370, status="active", nationality="Philippines",
         basic=1400.0, monthly=2400.0, visa=400, passport=500, eid=400,
         bank="Emirates NBD", used={}),
    dict(uid="EMP015", name="Manoj Pillai", department="Production",
         designation="Welder", role="employee", mgr="MGR001",
         joined=2575, status="active", nationality="India",
         basic=2800.0, monthly=4600.0, visa=400, passport=25, eid=500,
         bank="Mashreq", used={"annual": 15.0}),
    dict(uid="EMP016", name="Sara Thomas", department="Production",
         designation="QC Inspector", role="employee", mgr="MGR001",
         joined=30, status="probation", nationality="India",
         basic=3000.0, monthly=5000.0, visa=700, passport=700, eid=700,
         bank="ADCB", used={}),
]


def _iban(idx):
    """Deterministic fake UAE IBAN: AE + 21 digits."""
    return "AE07" + f"{1000000000000000000 + idx:019d}"


# Fake dates of birth (year, month, day); "today" makes a birthday-today demo.
# EMP006's birthday is today so the celebrations page always has content.
DEMO_DOBS = {
    "HRADMIN": (1985, 7, 14), "MGR001": (1988, 2, 3),
    "EMP001": (1992, 11, 25), "EMP002": (1990, 5, 9),
    "EMP003": (1987, 9, 17), "MGR002": (1983, 12, 1),
    "MGR003": (1986, 4, 22), "EMP004": (1991, 8, 30),
    "EMP005": (1994, 6, 5), "EMP006": "today",
    "EMP007": (1989, 1, 19), "EMP008": (1993, 10, 8),
    "EMP009": (1990, 3, 27), "EMP010": (1997, 7, 11),
    "EMP011": (1992, 2, 14), "EMP012": (1988, 9, 3),
    "EMP013": (1991, 12, 29), "EMP014": (1993, 5, 16),
    "EMP015": (1990, 11, 2), "EMP016": (1996, 8, 21),
}


def _demo_dob(uid, today):
    spec = DEMO_DOBS.get(uid)
    if spec == "today":
        return today
    if spec:
        return date(*spec)
    return None


def _demo_joining(uid, today, joined_days):
    # EMP007's joining anniversary is today in the demo (celebrations page).
    if uid == "EMP007":
        try:
            return date(today.year - 3, today.month, today.day)
        except ValueError:  # Feb 29 edge
            return date(today.year - 3, today.month, 28)
    return today - timedelta(days=joined_days)


def ensure_demo_users():
    """Create the 20 demo users (if missing) and seed all module demo data.

    Safe to call on every startup: users are created only when missing and
    module tables are seeded only when empty.
    """
    from app import db
    from app.models import User, LeaveBalance
    from app.demo_seed_data import (_seed_attendance, _seed_leave_requests,
                                    _seed_regularisations, _seed_candidates,
                                    _seed_onboarding, _seed_grievances,
                                    _seed_evaluations,
                                    _seed_v3_extras, _seed_v31_extras,
                                    _seed_v32_extras, _seed_v33_extras,
                                    _seed_v34_extras, _seed_v331_extras,
                                    _seed_v35_extras)
    import config as app_config

    # v3.3: GST "today" so birthday/anniversary demos match the app's
    # Asia/Dubai calendar (the VM itself runs on UTC).
    today = (datetime.utcnow() + timedelta(hours=4)).date()
    created = 0
    for i, spec in enumerate(DEMO_USERS):
        uid = spec["uid"]
        if db.session.get(User, uid) is None:
            u = User(
                id=uid, name=spec["name"], department=spec["department"],
                designation=spec["designation"], role=spec["role"],
                reporting_manager=spec["mgr"],
                joining_date=_demo_joining(uid, today, spec["joined"]),
                employment_status=spec["status"],
                date_of_birth=_demo_dob(uid, today),
                nationality=spec["nationality"],
                visa_expiry_date=today + timedelta(days=spec["visa"]),
                passport_expiry_date=today + timedelta(days=spec["passport"]),
                emirates_id_expiry_date=today + timedelta(days=spec["eid"]),
                email=f"{uid.lower()}@demo.800retail.example",
                mobile=f"+97150{1000000 + i}",
                phone=f"+97150{1000000 + i}",
                iban=_iban(i), bank_name=spec["bank"],
                basic_salary=spec["basic"], monthly_salary=spec["monthly"],
            )
            u.set_password(DEMO_PASSWORD)
            u.must_change_password = False  # demo convenience only
            db.session.add(u)
            created += 1
    db.session.flush()

    # Leave balances for every user / every UAE leave type.
    existing = {(b.user_id, b.leave_type) for b in LeaveBalance.query.all()}
    for spec in DEMO_USERS:
        for lt, cfg in app_config.LEAVE_TYPES.items():
            if (spec["uid"], lt) in existing:
                continue
            db.session.add(LeaveBalance(
                user_id=spec["uid"], leave_type=lt,
                entitled=cfg["entitled"],
                used=float(spec["used"].get(lt, 0.0))))
    db.session.flush()

    _seed_attendance()
    _seed_leave_requests()
    _seed_regularisations()
    _seed_candidates()
    _seed_onboarding()
    _seed_grievances()
    _seed_evaluations()
    _seed_v3_extras(DEMO_USERS, _iban)
    _seed_v31_extras()
    _seed_v32_extras()
    _seed_v33_extras()
    _seed_v34_extras()  # v3.4: HR flags + demo approval chains
    _seed_v331_extras()  # v3.3.1: demo expense claims + letter requests
    _seed_v35_extras()  # v3.5: demo helpdesk tickets + shift swap

    # Backfill DOBs for users that pre-date the v3.3 column.
    for spec in DEMO_USERS:
        u = db.session.get(User, spec["uid"])
        if u is not None and u.date_of_birth is None:
            u.date_of_birth = _demo_dob(spec["uid"], today)
        if u is not None and spec["uid"] == "EMP007":
            u.joining_date = _demo_joining("EMP007", today, spec["joined"])

    db.session.commit()
    return created
