# V3.10 — HR Analytics: interactive charts & widgets (user request, 2026-10-19)

New page **HR Analytics** at `/hrms/analytics` — interactive charts/widgets from REAL
app data, hand-rolled SVG (NO external chart CDN — must work offline), fully theme-aware
(colours from the V3.9 CSS variables so all 4 themes work). Every chart has: hover
tooltips (value + label), a caption stating its formula/source, and click-through to
the underlying list where one exists. Honest empty states ("No data yet") — never
fabricated numbers.

## Access & scope
- Admin + HR Manager + HR Staff: company-wide.
- Manager: same page scoped to own department (manpower shows all departments but own
  highlighted? NO — scope = own department only for absenteeism/leave/OT/movement;
  manpower chart shows own dept bar only + company total in caption).
- Employee: 403.
- The SALARY section renders ONLY for users with can_view_salary (server-side; absent
  from HTML otherwise — totals are salary data too).
- Filters (GET params, PJAX-friendly): period = 6m | 12m (default 12m) for trend
  charts; department = all | <dept> for the department-comparable charts.

## KPI widget row (top)
Headcount (active) · Attrition YTD % · Absenteeism (this month) % · OT hours (latest
locked run) · Vital docs expiring ≤30 days (count, red if >0) · Joiners vs Leavers
(this month, two small numbers). Each widget links to its chart/list.

## Charts (in importance order on the page)
1. **Attrition ratio** — line/area chart, monthly, period window. Formula (caption):
   leavers in month ÷ average headcount ((headcount at month start + month end)/2) ×100.
   Headcount/leavers derived from User.date_joined / date_left (inspect models for the
   actual separation fields — settlement/separation records if richer). KPI overlay:
   YTD attrition % + voluntary/involuntary split if separation reason exists, else omit.
2. **Department manpower** — horizontal bars, active headcount per department (desc),
   value labels; click a bar → Employees list filtered by that department.
3. **Vital documents expiry** — importance tiers: TIER 1 Critical = residence visa,
   Emirates ID, passport, labour card/work permit; TIER 2 Important = driving licence,
   safety/professional certifications; TIER 3 Other = education certificates + rest
   (map from the vault's actual document-type values — inspect code). Stacked bars per
   tier × window (Expired / ≤30d / 31–60d / 61–90d) + a "Soonest expiries" table
   (top 10, employee, doc, days left, link to the document). Expired counted in red.
4. **Absenteeism** — monthly trend bars (period window): absent ÷ (present + absent +
   on-leave) attendance records ×100 (inspect AttendanceRecord status values and adapt;
   caption states the exact definition used) + by-department bars for the latest month.
5. **Annual vacation** — utilisation: annual leave used vs entitled (LeaveBalance for
   the annual type): company donut (% utilised) + per-department bars used vs remaining;
   total remaining days (liability days only — no money figure; money stays in the
   gated liability report).
6. **Overtime hours** — monthly bars from payroll data (OT quantity on payslip lines of
   LOCKED runs; if OT qty isn't stored, sum approved OT payroll inputs — inspect and use
   what exists; caption says which) for the last 6 runs + latest-run by-department bars.
   Hours only — no AED amounts (amounts would make it salary data).
7. **Monthly salary differences** (can_view_salary ONLY) — total NET pay per locked run
   (last 6 runs) as bars with Δ% vs previous run labelled; below: "Biggest movers"
   table (top 8 employees by |net change| between the last two locked runs: name, prev
   net, latest net, Δ AED, Δ%) linking to their payslips. If <2 locked runs: empty state.
8. **Joiners vs resignations** — grouped bars per month (period window): joiners
   (date_joined) vs leavers (date_left); net movement line optional if clean. Legend +
   totals in caption.

## Interactivity
- Period toggle (6M/12M pills) + department select — GET params, page reload/PJAX.
- Hover tooltips on every bar/point/segment (CSS/JS, values formatted).
- Click-throughs: manpower bar → /hrms/employees?department=…; doc table rows →
  employee documents; movers → payslip; KPI widgets anchor-scroll to their chart.
- Chart colours via CSS vars (--blue, --navy, tier/status semantic vars) — verify in
  nixtio + glass + night (screenshots).
- Nav: drawer group + All Services tile "HR Analytics" (HR roles/managers); HR
  workspace link; admin dashboard gets a slim "View HR analytics →" link under the
  KPI strip (additive, keep V3.8 layout otherwise).

## Tests (tests/test_analytics.py, own /tmp DB pattern)
Fixtures with known values (users across 2 departments with set join/left dates,
attendance records with set statuses, documents with set expiries/types, leave
balances, two locked payroll runs with payslips incl. OT qty): assert each chart's
rendered values (data attributes or text) match hand-computed expectations; attrition
formula case; absenteeism formula case; doc tier bucketing case (expired/≤30/90);
salary section ABSENT for HR-Staff-without-permission and for manager, PRESENT for
can_view_salary admin; employee 403; manager scoping (other dept's mover/absent data
absent); period param changes month count; empty-state on a fresh DB.
Full suite green (baseline 1255). Legacy edits only if nav changes break greps —
document in README-V310.md.
Screenshots: v310-analytics-pc.png (nixtio), v310-analytics-glass.png,
v310-analytics-mobile.png. Rebuild zip; README-V310.md. NO deploy.
Don't touch v2/ess-app trees.
