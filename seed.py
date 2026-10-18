"""Seed the ESS database from the company staff master Excel file.

Usage:
    python seed.py [path/to/Employees_List.xlsx]

- Imports ACTIVE employees only.
- Username = Employee ID.
- Initial password = date of birth as DDMMYYYY (bcrypt-hashed, never stored plain).
- must_change_password=True forces a password change on first login.
- Creates leave balances from the standard entitlement table.
- Seeds sample attendance for the previous and current month (replace with
  real imports via Admin > Import Attendance).
"""
import os
import sys
import random
from datetime import date, datetime, timedelta
import calendar as calmod

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app.models import User, LeaveBalance, AttendanceRecord  # noqa: E402
import config as app_config  # noqa: E402

ADMIN_ID = "AA670"  # HR Manager -> ESS administrator


def dob_to_initial(dob_str):
    d = datetime.strptime(dob_str.strip(), "%d-%b-%Y")
    return f"{d.day:02d}{d.month:02d}{d.year}"


def main():
    xlsx = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "user", "files",
        "Employees_List__2.xlsx")
    if not os.path.exists(xlsx):
        print(f"Excel file not found: {xlsx}")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        header = [c for c in rows[0]]
        idx = {name: i for i, name in enumerate(header)}

        def col(row, name):
            v = row[idx[name]]
            return v.strip() if isinstance(v, str) else v

        # Fresh import: wipe user-dependent demo tables.
        for model in (AttendanceRecord, LeaveBalance, User):
            db.session.query(model).delete()

        imported, skipped = 0, 0
        for row in rows[1:]:
            if col(row, "Status") != "Active":
                continue
            emp_id = str(col(row, "Employee ID") or "").strip()
            name = str(col(row, "Name (En)") or "").strip()
            dob = col(row, "DOB")
            if not emp_id or not name or not dob:
                skipped += 1
                continue
            try:
                initial_pw = dob_to_initial(str(dob))
            except ValueError:
                skipped += 1
                continue
            user = User(
                id=emp_id,
                name=name,
                department=str(col(row, "Department") or "").strip(),
                designation=str(col(row, "Designation") or "").strip(),
                email=str(col(row, "Official Email") or "").strip(),
                mobile=str(col(row, "Mobile No.") or "").strip(),
                basic_salary=float(col(row, "Basic Salary") or 0),
                monthly_salary=float(col(row, "Monthly Salary") or 0),
                role="admin" if emp_id == ADMIN_ID else "employee",
            )
            user.set_password(initial_pw)
            user.must_change_password = True
            db.session.add(user)
            for ltype, spec in app_config.LEAVE_TYPES.items():
                db.session.add(LeaveBalance(user_id=emp_id, leave_type=ltype,
                                            entitled=spec["entitled"], used=0))
            imported += 1
        db.session.commit()
        print(f"Imported {imported} active employees, skipped {skipped}.")

        # Sample attendance for previous + current month (demo data).
        random.seed(42)
        today = date.today()
        months = []
        for back in (1, 0):
            m = today.month - back
            y = today.year
            if m < 1:
                m += 12
                y -= 1
            months.append((y, m))
        recs = 0
        users = User.query.all()
        for (y, m) in months:
            last = calmod.monthrange(y, m)[1]
            for day in range(1, last + 1):
                d = date(y, m, day)
                if d > today or d.weekday() == 6:  # Sunday week-off
                    continue
                for u in users:
                    r = random.random()
                    if r < 0.03:
                        status, ci, co = "absent", None, None
                    elif r < 0.08:
                        status = "late"
                        ci = f"08:{random.randint(15,45):02d}"
                        co = f"18:{random.randint(0,30):02d}"
                    else:
                        status = "present"
                        ci = f"07:{random.randint(45,59):02d}"
                        co = f"18:{random.randint(0,30):02d}"
                    db.session.add(AttendanceRecord(user_id=u.id, date=d,
                                                    check_in=ci, check_out=co,
                                                    status=status))
                    recs += 1
        db.session.commit()
        print(f"Seeded {recs} sample attendance records.")
        print(f"Admin account: {ADMIN_ID} (initial password = DOB as DDMMYYYY, must change on first login).")


if __name__ == "__main__":
    main()
