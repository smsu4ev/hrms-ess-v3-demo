# V3.8 — "Nixtio-style" soft-blue theme + dashboard rebuild (user-supplied reference, 2026-10-19)

User shared a dashboard concept image and said: "Use this theme and make dashboard also."
Rebuild the THEME (app-wide) and the DASHBOARD (home) in that style — our own CSS, layout
inspired by the reference, every widget fed by REAL app data (no decorative fake numbers:
empty data → "—" or an honest empty state). All routes/features/tests behaviour stay.

## Reference style (what to capture)
- Page background: pale blue-gray gradient (#dbe7f5 → #eef4fa feel). Content sits in ONE
  large rounded panel (radius ~26px, white/very light, soft wide shadow, page padding ~18px).
- Top bar INSIDE the panel: left = logo pill (white rounded-full, app name); CENTER = pill
  navigation in a white rounded-full container, active item = dark navy pill (#16324f-ish)
  with white text; right = small round icon buttons (settings gear, bell w/ badge, avatar).
- Greeting: large dark-navy heading "Welcome in, <First name>".
- KPI strip: left = 3–4 segmented progress bars (gradients of blue, % label inside, small
  caption above each); right = 2–3 BIG stat numbers (dark navy, ~44px) with tiny icon +
  caption under each.
- Cards: white, radius ~20px, soft shadow, generous padding. Accent blues: #2f7fe0 / #4aa3ff,
  deep navy #16324f / #1d4e79, pale chip bg #e8f1fb.
- Dashboard grid (desktop): left column = profile card + accordion list; middle = progress
  card + calendar card (wide); right = time-tracker card + onboarding/tasks card (with a
  DARK NAVY sub-card containing checklist rows: icon circle, title, sub-line, blue check).

## Theme application (app-wide, style.css new version)
- New tokens: --bg gradient, --panel, --navy, --blue, --blue2, --chip; body bg = gradient;
  main content wrapped in .panel (base.html wraps #pjax-main content in the panel on
  non-auth pages). Top bar restyled to the reference: logo pill + CENTER pill nav +
  round icon buttons. Pill nav items (role-filtered, existing routes): Dashboard,
  People (Employees for HR / Directory for others), Attendance (punch/attendance home),
  Payroll (admin/manager-with-access only), Calendar (Team Calendar), Approvals (badge,
  manager+), More (opens the existing drawer). Active pill from current endpoint.
- Keep: drawer, Awesome Bar, notifications panel, bottom tab bar (mobile), animations
  from V3.7 (tile rise, count-up, page enter, pulse), .bottomnav base display:none rule.
- Cards/tables/forms on other pages inherit panel + radius + navy headings (CSS only).
- Mobile: panel full-bleed (radius 0, no page padding), pill nav horizontally scrollable,
  dashboard cards stack in order: tracker → tasks → profile → progress → calendar.

## Dashboard (home) — role-based, mapped to real data
Header: "Welcome in, <First name>" + date/department sub-line.
KPI strip:
- Bars (left): EMPLOYEE: My attendance % (month), Annual leave used %, Training completed %.
  MANAGER: Team attendance % (month), On-time % (1 − late punches/punches), Leave decided %.
  ADMIN/HR: Attendance % (month, all), On-time %, Leave decided %, Tickets closed %.
  (Percent = real query; denominator 0 → bar at 0 with "—".)
- Big numbers (right): EMPLOYEE: Days present (month) · Leave balance (annual) · Open requests.
  MANAGER: Team today present · On leave today · Approvals waiting (badge-consistent).
  ADMIN/HR: Active employees · Present today · Pending approvals.
Cards:
1. PROFILE (self only): photo (or initials avatar on blue gradient), name, designation ·
   department; chip = own monthly salary ONLY if the user has a salary assignment
   (self-view; never render anyone else's), else chip = employment status.
2. PROGRESS → "This week": total worked/present time this week + 7 mini bars (Mon–Sun)
   from AttendanceRecord (employee: own hours; manager/admin: team/company present-count
   per day), today highlighted, max bar labelled.
3. TIME TRACKER → "Today": circular SVG ring = worked fraction of today's shift
   (employee: from own punches/attendance vs roster shift; before punch = 0 with shift
   window text; manager/admin: present fraction of expected staff). Centre text = elapsed
   "H:MM" (employee) or "n/N present". Round button = Check In / Check Out (links to
   punch page; pulse ring when unpunched — reuse V3.7).
4. TASKS (dark navy card) → "My tasks": % = done/(done+open) of the user's unified-inbox
   items; checklist rows (icon, title, sub-line, blue check when done / count chip when
   open): approvals awaiting me, mandatory notices to read, JD to sign, training to pass,
   my pending requests. Rows link to the item/inbox. (Admin variant adds: visa expiries
   ≤20d count row.)
5. ACCORDIONS (left column under profile): My Leave Balances (per type, chevron rows) ·
   My Documents (expiry dates, warning tint ≤60d) · My Payslips (last 3, view links).
   Manager/Admin variant: Expiring visas · Expiring certifications · Open tickets —
   same accordion shell, real lists, "View all" footer link.
6. CALENDAR (wide middle-bottom): current week strip (Mon–Sat like the reference +
   month label chips prev/current/next month names, non-functional labels OK — no fake
   nav) with approved-leave events from the team calendar (employee: own dept; manager:
   team; admin: all) rendered as chips with names + avatar initials; today column tinted.
   If none: "No leave scheduled this week".

## Tests (tests/test_v38.py, ESS_DATABASE_URL=sqlite:////tmp/test_v38.db)
Theme: style.css contains the new tokens + .panel + pill-nav classes; a normal page
renders inside .panel; pill nav active state on dashboard; role filtering (employee has
no Payroll pill). Dashboard: greeting with first name; KPI numbers match fixture data
(present count etc.); bars render % from fixture; profile chip shows salary only for the
fixture user WITH assignment and is absent for one without; tracker ring % + centre text
from fixture punches; tasks card % + rows (open approval row for manager fixture);
accordions list fixture leave balances/documents; calendar shows a fixture approved-leave
chip. Privacy: employee dashboard HTML contains no other employee's salary figure.
Legacy dashboard-grep tests (v33/v331/demo_data/e2e/v37 home assertions) updated ONLY
where the new dashboard legitimately changes text/markup — each edit documented in
README-V38.md. FULL suite green (baseline 1069 + migrate 38).
Screenshots (Playwright, demo seed): v38-home-admin.png, v38-home-employee.png,
v38-home-mobile.png, v38-inner-page.png (employees list inside the panel).
Rebuild zip; README-V38.md. NO deploy. Don't touch v2/ess-app trees.
