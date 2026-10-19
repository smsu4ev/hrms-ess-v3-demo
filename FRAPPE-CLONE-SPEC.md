# V3.6 — Frappe-Model Clone Spec (from the user's live demo, aagtest.k.frappe.cloud, explored read-only 2026-10-19)

Pattern clone in our own code (Frappe HR is GPL-3.0 — copy NO code/assets; clone the UX contract:
layout, option inventory, naming, conventions). ALL existing V3.5 features and routes keep working;
this round re-shells the UI and adds the missing Frappe-style pieces. Nothing is dropped.

## 1. Visual tokens (Frappe light Desk, observed)
- Background: very light gray (#f5f6f8 approx); surfaces/cards white, soft border + faint shadow, radius 12–16px.
- Sidebar/topbar/rail: white. Primary buttons: near-black (#171717-ish) with white text, radius ~8px.
  Secondary buttons light gray. Links/accent: Frappe blue-gray; keep our indigo ONLY where Frappe uses accent
  (active states, chart) — actually adopt Frappe's near-black primary as THE primary button style.
- Pills: status pills (soft bg + dark text), amber "Not Saved" pill on dirty forms, light-gray "Optional" pills,
  green approved / red rejected accents, light-blue area charts, light-blue info banner on forms.
- Inputs: light-gray rounded, dark-gray labels, red * for required. Inter-style sans (system stack ok).
- Layout: app rail ~48px; workspace sidebar ~240px (collapsible, grouped, "Collapse sidebar" at bottom);
  form pages: 2–3-col grid + ~300px right info panel (Assign / Attachments / Share / Last Edited By / Created By);
  comments + activity timeline at the bottom of form pages.
- The WhatsApp doodle background is retired from the Desk (Frappe surfaces are clean); keep the cream tint
  ONLY on the login page as a subtle nod. Keep style.css?v bump discipline (immutable caching).

## 2. Desk shell (replaces current topbar+grouped sidebar; PJAX keeps working inside it)
- Far-left RAIL (desktop): logo mark, one icon per WORKSPACE (role-filtered), avatar at bottom (menu:
  My Profile, Toggle Theme (light/dark — CSS variables; dark = Frappe "Timeless Night"-style slate),
  Settings (admin), Logout POST). On mobile the rail hides; bottom nav remains (existing 5 items).
- WORKSPACE SIDEBAR: workspace name switcher (dropdown listing workspaces + All Apps grid page),
  Search (opens Awesome Bar), Notifications (opens panel), grouped links per workspace (groups below),
  active-link highlight, collapse toggle. This REPLACES the six hover-groups; same role rules per link.
- CONTENT HEADER: breadcrumb (Workspace / Page), title, "…" menu where relevant (Edit/New/Manage →
  map to real actions), primary action button (dark, "+ Add X" on lists).
- AWESOME BAR: Ctrl+K / click Search → overlay panel "Search or type a command": live results grouped
  (Pages & Actions / Employees / Documents), keyboard ↑↓ ↵ Esc; footer hints (↑↓ navigate · ↵ select · Esc close).
  Backend: GET /api/awesome?q= returning role-filtered static page registry + employee matches
  (name/employee_id, admin+manager+hr only for employee results beyond self) + recent pages (client-side).
  Employees may only search pages + their own record. Rate-limit friendly (debounce client-side).
- NOTIFICATIONS PANEL: slide-over with tabs All / Events (reuse categories), mark-all-read — reuse
  existing notification data/routes; panel mirrors /notifications content.
- ALL APPS page (/apps): grid of app cards (HRMS workspaces + ESS) like Frappe's app switcher cards.

## 3. Workspaces (Frappe names/structure; our features slotted in; role access as today)
Each workspace HOME shows: greeting/title, onboarding-style checklist card ("Get to know X · N of M steps"
mapped to real setup completeness where cheap, else static guide links), number cards (real counts),
a simple chart (inline SVG/CSS from real data where trivial), shortcut/link cards with LIVE counts.

1. HR SETUP (admin/hr): Home, Dashboard(=existing dashboard), Employee (Employee Central list),
   Organizational Chart (our /org-chart); Masters: Visa Company (their "Company"), Department,
   Designation, Employee Group (our departments' groups → map to Department), Work Location (branch);
   Settings → HR Settings page (§5); plus OUR Admin: Admin Users, Approval Matrix, Audit Trail, Reports hub.
2. SHIFT & ATTENDANCE: Home, Dashboard, Roster (ours); Attendance: Check-in (Punch page), Attendance,
   Attendance Register (punches), Attendance Request (regularisation); Shift Assignments: Roster grid;
   Shift Setup: Shift Patterns (their Shift Type), Work Locations (their Shift Location);
   Overtime: OT Rules (their Overtime Type), OT from payroll (link); Reports: Monthly Attendance,
   Attendance reports; Settings → HR Settings tab.
3. LEAVES: Home, Leave Application, Leave Encashment (report link), Leave Allocations (balances),
   Holidays: Holiday/leave config (Compliance page), Leave Setup: Leave Config, Approvals inbox link;
   Reports: Leave Report, Leave Balance (reports hub anchors).
4. EXPENSES: Home, Dashboard; Expenses & Advances: Expense Claims (ours), Employee Advances (loans page);
   Reports: expense totals (existing page anchors).
5. PAYROLL: Home, Dashboard, Payroll Entry (runs list), Salary Assignments (their Salary Structure
   Assignment), Salary Slip (payslips), Additional inputs (Payroll Inputs), Loans & Advances;
   Setup: Salary Components, OT Rules; WPS: WPS files per visa company (OUR differentiator — keep prominent);
   Reports: Salary Register + variance + WPS pre-flight.
6. PERFORMANCE: Home, Goals (evaluations), Appraisal (evaluations list), Feedback; Setup: evaluation config link.
7. TENURE: Home, Dashboard, Onboarding, Separations (Employee Separation + Final Settlement — OUR F&F),
   Templates: Onboarding checklist templates; Grievance: Grievances; Training: Courses, Training Matrix;
   Reports: Separation/Turnover reports.
8. RECRUITMENT: Home, Dashboard, Job Openings (recruitment tracker), Applicants (tracker), Interviews (tracker),
   Offers → Letters (mail-merge incl. Offer Letter), Appointment Letter (letters).
9. TAX & BENEFITS → rename ours "EOS & Benefits": Home; Gratuity: Gratuity Calculator + Compliance
   (their Gratuity Rule/Gratuity), Leave & EOSB Liability report; Benefits: Letters/certificates links.
   (UAE has no income tax — do NOT clone tax slabs/declarations; keep the workspace for EOS/gratuity.)
10. ME (ESS, all employees; also the mobile home): Home (dashboard), My Profile, My Attendance + Punch,
    My Leave, My Payslips, My Documents, My Letters, My Training, My JD, My Expenses, My Tickets,
    My Swaps, Directory, Team Calendar, Notices, Celebrations.
11. MY TEAM (managers+): Home (Team Overview), Approvals inbox, Team Calendar, Roster view, Punches (dept).

## 4. List & Form conventions
- LISTS (apply to the main lists: Employees, Leave Applications(inbox/approvals), Attendance, Payroll Runs,
  Expense Claims, Tickets, Documents): header = title + one-line description; view label "List View";
  dark "+ Add X"; filter row (2–4 real filters per list incl. status select); sort control (Created On /
  name where trivial); status PILLS; empty state (icon, "No X created", hint, + Create / Import buttons
  where real); row click → detail. Employees list gains bulk-select checkboxes + bulk actions
  (Export selected CSV — admin only) if cheap; otherwise skip bulk.
- FORMS: Employee form becomes TABBED like Frappe's: Overview / Address & Contacts / Attendance & Leaves /
  Salary (salary-permission gated, admin edit) / Personal / Documents & Photo / Joining & Exit —
  reusing existing profile/admin-edit fields + photo + document vault, with the right INFO PANEL
  (photo, status pill, Created/Last edited from audit, Attachments = documents) and an ACTIVITY TIMELINE
  (audit entries for that employee) + comments are NOT required (timeline = audit only).
  Other detail pages (leave request, payroll run, settlement, ticket) get: breadcrumb header,
  status pill, info panel, activity timeline from AuditLog where entity matches.
- DIRTY-FORM pill: small JS — any form with data-dirty-watch shows amber "Not Saved" pill once changed,
  dark Save button. Apply on employee form + HR Settings + leave config.

## 5. HR Settings page (/hrms/settings) — tabbed, Frappe layout
Tabs: Employee / Leaves / Expenses / Shift & Attendance / Tenure / Recruitment. Store in a new
SettingKV(key,value) table; admin-only; audited; each toggle actually wired:
- Employee: celebrations reminders on/off (birthdays, work anniversaries — wire into celebration generation),
  standard working hours (display in roster settings link).
- Leaves: "Prevent self approval" (already enforced — show as locked-on with note), Leave approver =
  approval matrix (link), Send leave notifications (wire: off → no leave notifications created).
- Expenses: Expense approver mandatory (display, matrix link), multi-currency (display only, AED note).
- Shift & Attendance: Allow check-in from mobile (wire: off → punch page disabled w/ message),
  Geolocation tracking (wire: off → punch skips geofence check but still requires selfie),
  Allow multiple shift assignments same date (display/skip).
- Tenure: exit questionnaire (display — grievances/exit via Separations link).
- Recruitment: interview reminders (display only).
Wire ONLY the four flagged; others are honest display/link rows (no fake functionality).

## 6. Tests (new tests/test_v36.py) + keep ALL suites green
Shell per role (rail workspaces filtered), /apps grid, awesome API (auth required, role filtering,
employee search, no salary data), workspace homes render + live counts (employees count, pending approvals),
HR Settings save/load + wiring (celebrations off suppresses posts; geolocation off → punch without location
accepted if selfie ok; mobile-checkin off blocks punch page), employee tabbed form renders all tabs +
save round-trip + timeline shows an audit entry, employees list filters + status pill, dark theme toggle
persists (cookie/setting), doodle retired from desk pages but login keeps cream class.
Legacy template tests that grep old nav markup may need minimal updates — document each in README-V36.md.
Screenshots: login, HR Setup home, Employees list, Employee form (tabs), Payroll home, Me (ESS) home,
mobile home — Playwright local instance, demo seed.
Package ~/workspace/your_files/hrms-ess-v3.zip + README-V36.md (inventory mapping Frappe→ours, decisions,
test tally, deferred: Kanban/Gantt views, true saved-view persistence beyond existing report templates,
comments on forms (audit timeline only), Frappe Cloud console tabs → mapped to HR Settings/Admin).
