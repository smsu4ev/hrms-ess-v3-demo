# README-V310 — HR Analytics (v3.10, 2026-10-19)

New page **HR Analytics** at `/hrms/analytics` — KPI widgets + 8 interactive
charts from REAL app data. Hand-rolled inline SVG (NO external chart CDN —
works offline); colours come from the V3.9 CSS variables (`var(--blue)`,
`var(--navy)`, `--success/--warning/--danger` + `-bg` vars), so nixtio,
glass, indigo and night all render correctly. Hover tooltips via native SVG
`<title>` (value + label) on every bar/point/segment; click-throughs where a
target list exists; a caption under EVERY chart states its exact
formula/source; honest empty states everywhere ("No data yet…" style —
demo data is sparse, nothing is fabricated).

Data contract: `V310-DATA-CONTRACT.md` (verified against `app/models.py`;
authoritative over the spec where they differ — vault-only documents,
no certifications category, absenteeism denominator as stated below).

## 1. Access & scope

- Admin / HR Manager (`is_hr_manager`) / HR Staff (`is_hr_staff`): company-wide.
- Manager (`is_manager`): same page scoped to **own department only**
  (`base_users()`); manpower shows the own-dept bar only + company total in
  the caption; no department select is rendered for managers.
- Plain employee: **403**.
- Salary section (chart 7) renders ONLY when `salary_visible(current_user)`
  (admin or `can_view_salary`) — server-side `{% if can_salary %}`; the whole
  section, including the KPI salary widget, is absent from the HTML otherwise.
- Filters (GET, PJAX-safe): `period=6m|12m` (default `12m`) for trend charts;
  `dept=all|<department>` department select for HR-wide users.

## 2. Page structure (in order)

KPI widget row (each links to its chart via anchor):
Headcount (active) · Attrition YTD % (+ voluntary/involuntary split from the
separation register when present) · Absenteeism this month % · OT hours
(latest locked run) · Vital docs expiring ≤30d (red when >0) · Joiners /
Leavers this month.

1. **Attrition ratio** — line/area chart, monthly.
   Formula: `leavers_m ÷ ((headcount at month start + headcount at month end)/2) × 100`,
   leavers from `User.separation_date` (else `exit_date`); YTD = leavers this
   calendar year ÷ avg(headcount Jan 1, today) × 100.
2. **Department manpower** — horizontal bars, active (`active`/`probation`)
   headcount per department, desc. Bar click → `/hrms/employees?dept=…`.
3. **Vital documents expiry** — stacked bars per tier × window
   (Expired / ≤30d / 31–60d / 61–90d). Tiers: T1 = Residence Visa, Emirates ID,
   Passport, Labour Card; T2 = Driving Licence (certifications are not tracked
   as a separate vault category yet); T3 = everything else. Vault only
   (`EmployeeDocument`, `superseded` excluded, expiry required); user-level
   visa/passport/Emirates-ID fields deliberately skipped to avoid
   double-counting. Plus "Soonest expiries" top-10 table (employee, document,
   days left, link to the vault).
4. **Absenteeism** — monthly bars: `absent ÷ (present + absent + late + leave) × 100`
   (`holiday`/`weekoff` excluded); by-department bars for the latest month
   with records.
5. **Annual vacation** — donut: `Σ used ÷ Σ entitled × 100` over
   `LeaveBalance(leave_type="annual")`; per-department used vs remaining
   table; remaining shown as liability **days only** (no money figure).
6. **Overtime hours** — monthly bars from `PayrollOTHours.hours` on
   `locked`/`approved` runs (last 6 by period) + by-department bars for the
   latest run. **Hours only, never AED.**
7. **Monthly salary differences** (gated) — Σ `PayrollLine.net` per locked
   run (last 6) with Δ% vs previous run; "Biggest movers" table: employees in
   BOTH of the last two locked runs, top 8 by |Δ net| (prev net, latest net,
   Δ AED, Δ%), linking to the payroll run detail. <2 locked runs → empty state.
8. **Joiners vs resignations** — grouped bars per month (`joining_date` vs
   leaver date); totals + net in the caption.

## 3. Implementation

- `app/analytics.py` (new): data engine (`base_users`, `attrition_series`,
  `manpower`, `doc_expiry_data`, `absenteeism_data`, `leave_data`, `ot_data`,
  `salary_data`, `joiners_leavers`, formatters) + hand-rolled SVG builders
  (`vbars`, `hbars`, `line_chart`, `stacked_bars`, `donut`, `grouped_bars`).
  Count axes use exact integer ticks.
- `app/hrms.py`: `analytics_access` guard + `hrms.analytics` route
  (`/hrms/analytics`), following the existing HR-page pattern
  (`@login_required` + role guard).
- `app/templates/hr_analytics.html` (new): KPI row, filters, 8 chart
  sections, template-local `<style>` (theme vars only — no `style.css` edits).
- `app/workspaces.py`: "HR Analytics" link (`hrms.analytics`, icon `chart`,
  roles `admin/hr/manager`) in a new **Analytics** group of the **HR Setup**
  workspace — this one entry drives the drawer group item, the All Services
  tile, and the `/ws/hr-setup` workspace home link. The hr-setup workspace
  roles became `["admin", "hr", "manager"]` so managers can reach the link;
  every other link in that workspace stays admin/HR-only via its own roles.
- `app/templates/dashboard.html`: additive slim "View HR analytics →" link
  under the KPI strip, shown only to admin / HR Manager / HR Staff.

## 4. Tests

New: `tests/test_analytics.py` — **79 checks, all passing**.

Run: `rm -f /tmp/test_analytics.db /tmp/test_analytics_empty.db &&`
`ESS_SECRET_KEY=testsecret12345 ESS_DATABASE_URL=sqlite:////tmp/test_analytics.db`
`~/workspace/ess-app/venv/bin/python tests/test_analytics.py`

Coverage: access (admin/HR-staff/manager 200, employee 403); attrition
formula hand-computed for two fixture months (9.52% / 10.53%) + independent
recomputation + YTD KPI + voluntary/involuntary split; manpower bars +
click-through href; doc tier/window bucketing (incl. expired/≤30/31–60/61–90,
superseded/null/>90d excluded) + soonest-table ordering + red expired row;
absenteeism formula recomputed from fixture records (holiday excluded) +
by-dept tooltips + exact-definition caption; leave donut 37.50% + remaining
days + no money figures; OT hours from locked runs only (draft ignored,
hours never AED); salary section PRESENT for `can_view_salary` admin with
hand-computed totals (30000/30400), Δ% and movers order, ABSENT for HR staff
without permission and for managers; manager scoping (other-dept names
absent, own-dept only, no dept select); `period=6m/12m` month counts;
`dept=` narrowing; joiners/leavers KPI + caption totals; workspace nav for
admin/manager/employee; `/ws/hr-setup` + All Services tile for managers;
admin dashboard link present / absent for employees; night-theme render with
CSS vars; 9 empty-state messages + KPIs on a fresh DB.

Full suite (final): every `test_v*.py` at root + `tests/test_migrate.py` +
`tests/test_v38.py` + `tests/test_v39.py` + `tests/test_analytics.py`, each
with its documented `ESS_DATABASE_URL` pattern and fresh DBs,
`ESS_SECRET_KEY=testsecret12345`, venv python. `test_e2e.py` runs against
the repo `ess.db` per its design (single-run: it changes AA670's password,
creates E2E-MGR/leave/grievance/approval rows — re-running needs the
cleanup + DOB-password reset documented below).

Per-file tallies (final):

| File | Passed | Failed |
|------|--------|--------|
| test_v2.py | 33 | 0 |
| test_v21.py | 48 | 0 |
| test_v3.py | 83 | 0 |
| test_v31.py | 102 | 0 |
| test_v311.py | 72 | 0 |
| test_v32.py | 71 | 0 |
| test_v321.py | 46 | 0 |
| test_v33.py | 107 | 0 |
| test_v331.py | 71 | 0 |
| test_v34.py | 73 | 0 |
| test_v341.py | 67 | 0 |
| test_v35.py | 39 | 0 |
| test_v36.py | 83 | 0 |
| test_v361.py | 22 | 0 |
| test_v37.py | 72 | 0 |
| test_e2e.py | 32 | 0 |
| test_demo_data.py | 54 | 0 |
| tests/test_migrate.py | 38 | 0 |
| tests/test_v38.py | 76 | 0 |
| tests/test_v39.py | 66 | 0 |
| tests/test_analytics.py | 79 | 0 |
| **TOTAL** | **1334** | **0** |

(Baseline was 1255; +79 new. `test_e2e.py` 32/32 verified after the full
cleanup described in §6.)

## 5. Legacy test edits (with reasons)

**None.** No existing test file was modified. The only legacy-surface
changes are additive: one workspace registry link (+ `"manager"` on the
hr-setup workspace roles, required so managers can reach their scoped page)
and one gated link under the dashboard KPI strip. The full suite passes
unmodified, including the v37 "admin sees all 11 workspaces" check.

## 6. Screenshots

Playwright (Chromium) against a local demo-seeded instance (`seed_demo.py`,
HRADMIN), into `~/workspace/your_files/hrms-theme-shots/`:

- `v310-analytics-pc.png` — nixtio, 1440px, full page.
- `v310-analytics-glass.png` — glass theme: frosted cards readable, charts
  recolored via CSS vars (violet accents), captions legible.
- `v310-analytics-mobile.png` — 390×844: KPI grid 2-up, charts scale,
  tables scroll horizontally.
- `v310-analytics-night.png` — night theme (extra verification): SVG bars
  recolor to the night palette, text stays readable on dark surfaces.

Eyeball findings fixed: count-axis charts (docs tiers, joiners/leavers)
first rendered fractional ticks (0.3/0.6/…) — switched to exact integer
ticks with bars scaled to the same ceiled max. Tooltips verified present
(53 `<title>` elements in the served HTML: every bar/point/segment carries
value + label).

## 7. Packaging

Rebuilt `~/workspace/your_files/hrms-ess-v3.zip` AFTER all changes:
1. Deleted all `__pycache__` directories first.
2. Zipped the v3 root excluding `ess.db`, `instance/*`, `*.pyc`.
3. Verified the listing (no `ess.db` / `instance/` / `.pyc`; includes
   `app/analytics.py`, `app/templates/hr_analytics.html`,
   `tests/test_analytics.py`, `README-V310.md`, the three required
   screenshots are in `~/workspace/your_files/hrms-theme-shots/`).

## 8. Deferred / uncertain items

- **e2e single-run caveat (pre-existing):** `test_e2e.py` mutates the repo
  `ess.db` (AA670 password → StrongPass1, E2E-MGR user, test leave/grievance,
  approval instance rows, leave balance). A second run without cleanup fails
  on UNIQUE constraints / stale approval instances / locked accounts. To
  re-run: reset AA670+AA passwords to the DOB-derived initial (from
  `../user/files/Employees_List__2.xlsx`), clear failed_attempts/lock,
  delete E2E-MGR, the "test grievance"/"test"/"too long" rows, the
  `leave`-process approval instances+actions, reset AA670.reporting_manager
  and the annual balance `used`. A backup of the pre-run DB was kept at
  `/tmp/ess.db.pre-v310-e2e.bak` during verification.
- **Awesome-bar search:** the page was deliberately NOT added to
  `PAGE_REGISTRY` in `desk.py` — no single registry role covers
  admin+HR+managers without duplicating the entry; discovery goes through
  the drawer / All Services / workspace home / dashboard link.
- **Manager dashboard:** the "View HR analytics →" strip link is shown to
  admin / HR Manager / HR Staff only (managers reach the page via the
  drawer tile and `/ws/hr-setup`).
- **Joiners/leavers "net movement line":** omitted (spec: optional).
- Screenshots use demo-seeded data on a local instance; no deploy was done.
  The live Render demo still runs pre-V3.10 code.
