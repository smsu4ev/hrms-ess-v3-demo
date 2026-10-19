# V3.8 — "Nixtio-style" soft-blue theme + dashboard rebuild (final stream)

Spec: `V38-THEME-SPEC.md` (user-supplied reference image, 2026-10-19).
Stream A (theme shell) and Stream B (dashboard template + view) were delivered
before this stream; this stream owns: the mobile-order CSS check, the
`tests/test_v38.py` suite, all legacy dashboard-grep test updates, the full
suite run, Playwright screenshots, the rebuilt zip, and this README.
**No deploy was done.**

## What this stream changed (beyond the spec's test/shot work)

Two gaps left by the earlier streams were found and fixed:

1. **Dashboard widget CSS was missing entirely.** `dashboard.html` (Stream B)
   emits classes (`.kpi-strip`, `.kbar`, `.knum`, `.dash-grid`, `.dcol-*`,
   `.profile-card`, `.acc*`, `.weekbars`, `.cal*`, `.tracker`/`.ring`,
   `.punch-btn`, `.tasks-dark`, `.task-row`, `.pay-chip`, …) but Stream B never
   wrote their rules — the dashboard rendered unstyled. Added a complete
   "v3.8 dashboard (home) widgets" CSS block to `app/static/style.css`
   (greeting, KPI strip with gradient bars + big navy numbers, 3-column
   desktop grid, profile card, accordions, week bars, leave calendar, SVG
   tracker ring with `fill:none` circle styles, pulse punch button, dark navy
   tasks card) plus a tablet tweak (861–1100px: left/right columns share a
   row, middle full-width).
2. **Mobile stacking order of the accordion group.** The left column's `.acc`
   accordion wrapper had no `mord-*` hook, so on mobile the accordions
   stacked before the tracker card. Gave it `mord-profile` (accordions are
   the profile column's group), so mobile now stacks exactly
   tracker → tasks → profile+accordions → progress → calendar. Verified on
   the 390px screenshot.

## Theme tokens (`app/static/style.css` `:root`)

| Token | Value | Use |
|---|---|---|
| `--bg` | `linear-gradient(160deg, #d7e4f4, #e3edf8, #eef4fa)` | page background |
| `--panel` | `#f5f9fd` | the one large rounded panel (radius 26px, `--panel-shadow`) |
| `--navy` | `#16324f` | headings, active pill, primary buttons |
| `--navy-2` | `#1d4e79` | navy hover / today highlights |
| `--blue` | `#2f7fe0` | accent (bar fills, links, checks) |
| `--blue2` | `#4aa3ff` | light accent (bar gradient start) |
| `--chip` | `#e8f1fb` | pale chip / ring track background |

Top bar (inside `.panel`): white brand pill left, CENTER pill nav in a white
rounded-full container (active item = navy pill, white text; badge inverts),
round white icon buttons right (search, settings gear for admins, bell with
badge, avatar menu). Pill nav is role-filtered: Dashboard, People, Attendance,
Payroll (admin + `can_view_salary` only), Calendar, Approvals (manager+ with
pending-leave badge), More (opens drawer). Active pill from current endpoint.
Kept from V3.7: drawer, Awesome Bar, notifications slide-over, mobile bottom
tab bar (`.bottomnav { display:none }` base rule BEFORE the ≤860px query —
regression-guarded), animations (tile rise, count-up, page enter, pulse) and
the `prefers-reduced-motion` block.

## Dashboard widget → data-source mapping (`GET /`, `app/views.py::dashboard`)

| Widget | Source |
|---|---|
| Greeting "Welcome in, \<first name\>" + date/dept | `current_user` |
| KPI bars (employee: attendance %, annual leave used %, training %; manager: team attendance %, on-time %, leave decided %; admin adds tickets closed %) | `AttendanceRecord`, `PunchRecord`+`RosterAssignment`+`ShiftPattern`, `LeaveRequest` (90d), `HelpdeskTicket`; denominator 0 → `—` |
| KPI big numbers (employee: days present, annual balance, open requests; manager: team present, on leave, approvals waiting; admin: active employees, present today, pending approvals) | same tables + `User`, `inbox_tasks()` |
| Profile card: initials avatar (blue gradient) or photo; salary chip `AED X / month` ONLY when a `SalaryAssignment` exists (self only, never another employee's) else employment-status chip | `User`, `SalaryAssignment` |
| "This week": 7 Mon–Sun bars from `AttendanceRecord` (employee: own hours; manager/admin: present-counts), today highlighted | `AttendanceRecord` |
| "Today" tracker: SVG ring = worked/shift fraction (employee, from `PunchRecord` vs `RosterAssignment` shift) or present/expected (manager/admin); centre `H:MM` or `n/N`; Check In/Out button with pulse when unpunched | `PunchRecord`, `RosterAssignment`, `User` |
| "My tasks" (dark navy): % = done/(done+open) over unified-inbox items; rows: approvals, mandatory notices, JD, training, my requests (+ visa expiries ≤20d for admin) | `inbox_tasks()` (`ApprovalInstance`, `Notice`, `JobDescription`, `TrainingAssignment`, `LeaveRequest`/`RegularisationRequest`/`LetterRequest`), `get_upcoming_expiries(20)` |
| Accordions: employee → My leave balances (sick hidden, v3.3), My documents (warn tint ≤60d), My payslips (last 3); manager/admin → Expiring visas, Expiring certifications, Open tickets | `LeaveBalance`, `EmployeeDocument`, `Certification`, `HelpdeskTicket` |
| "Team leave" calendar: current week Mon–Sat, approved-leave chips (employee: own dept; manager: scope; admin: all), month label chips | `LeaveRequest` (approved) |

## Test tally (fresh /tmp DB per file, `ESS_SECRET_KEY=testsecret12345`)

| File | Passed | Failed |
|---|---|---|
| test_e2e.py (throwaway copy of ess.db) | 32 | 0 |
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
| tests/test_v38.py (new) | 76 | 0 |
| test_demo_data.py (ESS_SEED_DEMO=1) | 54 | 0 |
| **Total** | **1150** | **0** |
| tests/test_migrate.py | 38 | 0 |

New `tests/test_v38.py` (76 checks) covers every spec-Test bullet: CSS tokens +
`.panel` + pill-nav classes; a normal page inside `.panel`; pill-nav active
state on dashboard; role filtering (employee: no Payroll/Approvals pill, no
settings gear; admin: Payroll pill + gear; manager: Approvals pill); mobile
`mord-*` ordering rules in the ≤860px query; greeting with first name; KPI
numbers vs fixture (days present, annual balance); bars from fixture
(attendance 100%, annual used 20%, training `—`); salary chip only for the
user WITH an assignment, absent otherwise; tracker ring `stroke-dashoffset`
(271.43) + centre `2:00` from fixture punches; tasks card `0%` + the open
"Approvals awaiting me" row for the manager fixture; accordions (leave
balances incl. sick-hidden, documents with 45d warn tint, payslips; admin
variants); calendar approved-leave chip; privacy (no other employee's salary
figure in employee/manager HTML); `.bottomnav` base `display:none` and
`prefers-reduced-motion` regression guards. Fixtures use fixed UTC punch
times so the ring/on-time assertions are deterministic at any run hour.

## Legacy test edits (each minimal, intent preserved, v3.8 dashboard/theme)

- `test_v37.py`: hamburger `class="hamburger"` → `iconbtn-hamburger`; the
  `.topbar-links` nav was removed by Stream A → `topbar_links()` now extracts
  `<nav class="pillnav">` and text-link checks became pill checks (admin:
  Dashboard/People/Attendance/Calendar/More/Payroll; employee: no
  Payroll/Approvals; manager: Approvals). §3 rewritten for the v3.8
  dashboard: "Welcome in, \<first>" greeting, punch button, KPI strip,
  profile/tasks cards, admin Payroll pill + gear + `data-count` + "Pending
  approvals", manager Approvals pill badge + "Team present today"/"On leave
  today". §7: `style.css?v=9`→`v=10`, `app.js?v=4`→`v=5`, `desk.js?v=2`→`v=3`.
- `test_v36.py`: "Home + All Services" / "My Team" topbar-link checks → pill
  equivalents (Dashboard+People, Payroll for admin, Approvals for manager);
  stylesheet `v=9`→`v=10`.
- `test_v33.py`: leave-balance assertion → `Annual Leave` / no `Sick Leave`
  (accordion markup); "Latest notice" card assertions → the tasks-card row
  ("Mandatory notices to read" / "All notices acknowledged").
- `test_v331.py`: "My tasks" string KEPT as-is (verified present); dept-card
  caption "Present today" → "Team present today" (v3.8 capitalisation).
- `test_demo_data.py`: greeting regex → `Welcome in, <first>`.
- `test_e2e.py`: `b"Good "` → `b"Welcome in,"`.
- `test_v2.py`: expiry widget → the "Expiring visas" accordion (name-based:
  "Test Expiry").
- `test_v21.py`: quick-access tile-grid check → KPI strip (tiles are gone from
  home; `/apps` keeps the grid); `tbl-wrap` check moved from the dashboard
  (no tables anymore) to the employee `/leave` page.

## Screenshots (`~/workspace/your_files/hrms-theme-shots/`, demo seed)

- `v38-home-admin.png` — HRADMIN desktop: panel, pill nav, greeting, KPI
  bars + 20/20/1 big numbers, profile card with salary chip, Expiring visas
  accordion, This week, Team leave, Today ring 20/20, dark My tasks card.
- `v38-home-employee.png` — EMP001 desktop: role-filtered pills (no
  Payroll/Approvals/gear), 15/25/2 numbers, salary chip, 0:00 ring with
  "Shift 08:00 – 18:00" + pulsing Check In, tasks card 50%.
- `v38-home-mobile.png` — 390px: full-bleed panel, scrollable pill nav,
  bottom tab bar visible, cards stack tracker → tasks → profile+accordions →
  progress → calendar.
- `v38-inner-page.png` — Employee Central list inside `.panel`, People pill
  active, navy headings, `tbl-wrap` table.

Eyeball results: pale blue-gray gradient, one rounded panel, navy active
pill, "Welcome in, \<name>" greeting, blue KPI bars + big navy numbers, white
radius-20 cards, circular ring, dark navy tasks card — all match the
reference. No broken styles, no desktop bottomnav.

## Deferred / uncertain

- `test_e2e.py` has no documented `ESS_DATABASE_URL` and needs the real AA670
  record; it crashes at module level on a fresh DB — pre-existing, not
  v3.8-caused. Ran against a throwaway copy of the live `ess.db`
  (`/tmp/test_e2e.db`; mutations land on the copy only): 32/32 green.
  `ess.db` itself was never touched.
- The `kbar-pct` % label can sit on top of a nearly-full bar fill (e.g.
  97.5%) — readable but not perfect; cosmetic, left as-is.
- "This week" bars for future days of the current week render as empty
  slots; "No leave scheduled this week" is the honest empty state.
- Stream B's `dashboard.html` comment still says "(stream A: flatten columns
  with display:contents and order these on mobile)" — the rules are now in
  place; comment left as historical note.
- Playwright (1.64.0, chromium-1248) is installed in
  `~/workspace/ess-app/venv` only; `~/workspace/hrms-ess-v2/` and
  `~/workspace/ess-app/` were not otherwise touched. NO deploy done.
