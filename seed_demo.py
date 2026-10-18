"""Seed DEMO data for free sample testing — FAKE employees only.

Usage (on the sample host, after installing requirements):
    python seed_demo.py

Populates the full sample company via app/demo_seed.py: 20 demo users
(HRADMIN / MGR001..MGR003 / EMP001..EMP016, all with password Demo@123)
plus sample attendance, leave requests, regularisation requests,
candidates, onboarding cases, grievances and evaluations for every module.

WARNING: Demo seed only. Never run this on a production database and
never use these passwords with real employee data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.demo_seed import ensure_demo_users, DEMO_PASSWORD  # noqa: E402


def main():
    app = create_app()
    with app.app_context():
        created = ensure_demo_users()
        print(f"Demo seed done: {created} new demo user(s); full module data seeded.")
        print(f"Login at / with HRADMIN / {DEMO_PASSWORD} (admin), "
              f"MGR001 / {DEMO_PASSWORD} (manager), "
              f"EMP001 / {DEMO_PASSWORD} (employee).")


if __name__ == "__main__":
    main()
