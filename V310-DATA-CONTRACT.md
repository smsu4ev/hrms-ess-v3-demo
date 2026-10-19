# V3.10 Data Contract (verified against app/models.py, 2026-10-19)

## Users / headcount
- User.joining_date (date, may be NULL) ; leaver date = separation_date if set else
  exit_date (both date, may be NULL); statuses in employment_status:
  active|probation|resigned|terminated|retired. ACTIVE = status in ("active","probation").
- Month m attrition: leavers_m = count(users with leaver_date within m).
  headcount_start = users with (joining_date NULL or <= m.start) AND
  (leaver_date NULL or >= m.start); headcount_end similarly at m.end.
  ratio_m = leavers_m / ((headcount_start + headcount_end)/2) * 100.
  Zero denominator -> "—".
- Voluntary/involuntary split: join SeparationRecord on employee_code == user.id;
  SeparationRecord.voluntary_types() = {"Resignation","Retirement"},
  involuntary_types() = {"Termination","Absconding"}. Caption: source = separation register.
- Joiners vs resignations: joiners_m = count(joining_date in m); leavers_m as above.

## Attendance
- AttendanceRecord.status in present|absent|late|leave|holiday|weekoff.
- Absenteeism_m = absent_m / (present_m + absent_m + late_m + leave_m) * 100.
  holiday/weekoff excluded. Caption states exactly this.

## Documents
- EmployeeDocument.category in: Passport, Emirates ID, Residence Visa, Labour Card,
  Education Certificate, Employment Contract, Offer Letter, Medical Certificate,
  Driving Licence, Other. Skip superseded=True rows; need expiry_date not NULL.
- TIERS: T1 Critical = {Residence Visa, Emirates ID, Passport, Labour Card};
  T2 Important = {Driving Licence} (no dedicated certification category exists —
  caption notes certifications are not tracked separately yet);
  T3 Other = {Education Certificate, Employment Contract, Offer Letter,
  Medical Certificate, Other}.
- Windows vs today: Expired (expiry < today), ≤30d, 31–60d, 61–90d.
- Soonest-expiries table: top 10 ordered by expiry_date ascending (includes expired,
  days-left negative, shown in red); columns employee, document, days left; row links
  to the employee's documents page. Also watch user-level fields visa_expiry_date,
  passport_expiry_date, emirates_id_expiry_date — SKIP them (keep the chart to the
  vault only; caption says so) to avoid double-counting the same documents.

## Leave
- LeaveBalance.leave_type == "annual": entitled, used (floats).
- Donut % = sum(used)/sum(entitled)*100; per-department bars: used vs (entitled-used).

## Payroll
- PayrollOTHours: run_id -> PayrollRun; category normal|night|restday; hours float.
  Use runs with status in ("locked","approved"); order by period; take last 6.
  Chart shows HOURS ONLY — never AED. Caption: "Hours from approved OT entries on
  locked payroll runs (Payroll Overtime)."
- PayrollLine.net: sum per locked run (last 6 by period) -> bars with Δ% vs previous
  run. Biggest movers: employees present in BOTH of the last two locked runs, top 8
  by |net change|: prev net, latest net, Δ AED, Δ%. <2 locked runs -> empty state.

## Departments
- User.department free-text string; skip NULL/blank.

## Manager scoping
- current_user.department; all charts filtered to that department. Manpower: show own
  dept bar only + company total as a caption note. Salary section absent unless
  current_user.can_view_salary (never for plain managers).
