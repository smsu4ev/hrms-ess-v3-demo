# HRMS + ESS — V3.2: Shift Roster & Final Settlement (2026-10-19)

## What is new in V3.2

### Module 1 — Shift Roster (`app/roster.py`, blueprint `roster`)
- **Shift patterns** (`/hrms/shifts`): GEN 08:00–18:00, MORN 06:00–14:00,
  EVE 14:00–22:00, NIGHT 22:00–06:00 with break/grace minutes and an
  automatic `crosses_midnight` flag (night shifts). Admin CRUD;
  a pattern with roster assignments cannot be deleted.
- **Weekly roster grid** (`/hrms/roster`): Monday-start week picker,
  department filter, rows = employees × Mon–Sun with shift-code chips
  (NIGHT colour-coded). Per-cell set/clear (`/hrms/roster/assign`).
  **Bulk assign**: scope (all / department / single employee), shift,
  from–to dates, skip-weekly-off (None/Friday/Sunday), overwrite toggle
  (existing assignments are kept and counted unless overwritten).
- **Roster vs attendance variance** (`/hrms/roster/variance`): for a date,
  lists (a) rostered but marked absent, (b) present/late with no roster
  assignment, (c) night-shift rostered employees.
- **Roster settings** (`/hrms/roster/settings`, admin): Ramadan start/end +
  reduced daily-hours threshold, and the summer midday-break window
  (default 12:30–15:00, Jun 15 – Sep 15). Banners appear on the roster
  grid when the displayed week intersects these periods. Ramadan dates
  are intentionally left empty until the company sets them.
- **ESS** (`/my-roster`, all roles): employee's own assignments for the
  next 14 days with today highlighted; nav link added for every role.
- **Permissions**: admin writes; managers get read-only grid views scoped
  to their own department; employees are 403 on `/hrms/roster*` views and
  writes.

### Module 2 — Final Settlement (`app/settlement.py`, blueprint `settlement`)
- **FinalSettlement model**: user, separation link, last working day,
  separation type, reason, notice days + direction
  (company_pays / employee_pays / none); status flow
  **draft → calculated → approved → paid** with **creator ≠ approver**
  (SoD, same rule as payroll runs).
- **Calculate** freezes the breakdown into `lines_json` (2dp):
  1. Gratuity via the statutory `app/uae.py` calculator
     (resignation basis only for Resignation, employer otherwise),
  2. Annual leave encashment = remaining balance × (basic/30),
  3. Final month salary = fixed earnings × (LWD day-of-month / 30),
  4. Active loan/advance balances (recoverable),
  5. Notice pay (direction-aware),
  6. Manual adjustments (added while draft).
  Totals payable / recoverable / net (net may be negative → shown as
  "Net Recoverable from Employee").
- **Reopen to draft** (admin, before approval) allows correction;
  Calculate is only possible from draft; recalculation after
  approval/paid is blocked.
- **Clearance warnings** on the detail page: unreturned assets, unreturned
  PPE/uniform, pending leave requests (warnings only, non-blocking).
- **Print-friendly statement**: company header, employee block, lines,
  totals, prepared/approved by, signature placeholders.
- **Permissions**: admin OR a user granted salary visibility
  (`hr_required`); managers without it get 403. Detail views are audited.
- **Separations integration**: the Separation Register shows a
  "Settlement" link for records whose employee code matches a user
  (opens the new-settlement form prefilled, or the existing open
  settlement); the new form prefills LWD/type/reason from the user's
  separation fields or the latest separation record.

### Demo seed
`_seed_v32_extras()`: the 4 shift patterns; current-week roster for 10
demo staff across GEN/MORN/EVE/NIGHT; roster settings (midday defaults,
Ramadan empty); one **draft** settlement for EMP013 (LWD = end of last
month, Resignation). Never changes the 20 demo users or their statuses.

## Test report — 487/487 green (fresh DBs; e2e seeded via `seed.py`)

| Suite | Checks |
|---|---|
| test_e2e | 32/32 |
| test_v2 | 33/33 |
| test_v21 | 45/45 |
| test_demo_data | 54/54 |
| test_v3 | 83/83 |
| test_v31 | 102/102 |
| test_v311 | 72/72 |
| test_v32 (new) | 66/66 |

`test_v32.py` highlights: shift CRUD + `crosses_midnight`; bulk
assign skip-Friday (6 rows), skip-existing vs overwrite; week grid chips;
cell assign/clear; employee 403 on roster writes; `/my-roster` shows own
shift only; variance buckets (a)/(b)/(c); Ramadan banner on/off;
manager department-scoped grid; settlement hand-calc fixture
(basic 3000, 7.0 yrs service, Termination → gratuity **16500.00**;
leave 20 days × 100 = **2000.00**; final month 4500 × 15/30 =
**2250.00**; loan **1200.00** recoverable; notice 30 days company_pays
= **4500.00**; manuals +500 / −150 → totals payable **25750.00**,
recoverable **1350.00**, net **24400.00**); notice employee_pays →
recoverable; SoD blocks creator approval, second admin approves, mark
paid; recalc blocked after approval; reopen-to-draft works;
adjustments blocked after calculate; manager/employee 403s; view
audited; separations settlement link; prefill; nav links.

## Deferred / not built
- Roster exceptions (swap requests, approval workflow) and automatic
  OT buckets from rostered vs actual hours — future.
- Midday-break/Ramadan only banner today; no automatic hour-threshold
  enforcement in payroll.
- WPS bank-ACK reconciliation and the exact LuLu SIF still need the
  bank/LuLu file from the company.
