# V3.6 — Frappe-Model UI Clone (2026-10-19)

Pattern clone of the Frappe Desk / Frappe Cloud interface in our own code
(Frappe HR is GPL-3.0 — no code or assets copied; the UX contract was cloned:
layout, option inventory, naming, conventions). Built from the live exploration
of the user's own Frappe demo (`aagtest.k.frappe.cloud`) plus the blueprint §17
spec. Full spec: `FRAPPE-CLONE-SPEC.md`.

**UI re-shell on the existing Flask app.** Every feature, route and behaviour
keeps working — payroll engine, approval matrix, WPS, geo-selfie punch,
settlements, retail registers, helpdesk, shift swaps are functionally untouched.

## What changed

### Desk shell (replaces the navy topbar + grouped sidebar)
- Far-left **app rail** (~48px, white): logo mark, one icon per visible
  workspace (role-filtered), avatar at the bottom with a dropdown menu
  (My Profile, **Toggle Theme** light/dark, Settings (admin), Logout POST).
- **Workspace sidebar** (~240px, white, collapsible): workspace switcher
  dropdown + "All Apps" link, Search box (opens the Awesome Bar),
  Notifications button (slide-over panel with All/Events tabs + mark-all-read),
  grouped links per workspace, active-link highlight, "Collapse sidebar".
- **Awesome Bar** (Ctrl+K): overlay "Search or type a command", live results
  grouped Pages & Actions / Employees / Documents, ↑↓ ↵ Esc, footer hints.
  Backend `GET /api/awesome?q=` (rate-limited 120/min): employees may search
  pages + their own record only; managers/HR/admin get directory matches on
  name/Employee ID — **never salary data**.
- **All Apps page** (`/apps`): Frappe-style app-switcher cards per workspace.
- Content headers are rendered per page inside the PJAX region
  (`_content_header.html` partial); PJAX, mobile bottom nav (5 items) and the
  mobile drawer keep working.
- Files: `app/templates/base.html` (rewritten), `app/desk.py` (new),
  `app/static/desk.js` (new), `app/static/app.js` (v3: active-nav selector),
  `app/static/style.css` (v5 — see below).

### Visual tokens — Frappe light Desk
- Page bg `#f5f6f8`; white cards, radius 12–16px, soft borders; **primary
  buttons near-black `#171717`** (replaces indigo as the primary action style);
  status pills (green/red/amber/blue/gray); amber "Not Saved" dirty pill;
  light-blue info banner; light-gray rounded inputs; red `*` for required.
- **WhatsApp doodle background RETIRED from all desk pages** (Frappe surfaces
  are clean). The login page keeps only a flat cream tint (`var(--cream)`),
  no doodle image.
- **Dark theme**: `html[data-theme="dark"]` "Timeless Night"-style slate
  overrides; the toggle persists in the `ess_theme` cookie and is rendered
  server-side (no flash).

### 11 workspaces (Frappe names; our features slotted in)
HR Setup · Shift & Attendance · Leaves · Expenses · Payroll · Performance ·
Tenure · Recruitment · EOS & Benefits (UAE has no income tax — Frappe's tax
slabs/declarations were deliberately NOT cloned; the workspace holds gratuity/
EOSB/liability) · **Me** (ESS, also the mobile home) · **My Team** (managers).
Registry + role helpers + homes in `app/workspaces.py` (`/ws/<key>`); each
home has greeting, setup checklist with progress, live-count number cards,
an inline-SVG chart, and shortcut cards with live counts. All 112 verified
endpoints mapped; nothing dropped.

### List & form conventions
- Lists (Employees, Approvals inbox, Attendance, Payroll Runs, Expense Claims,
  Tickets, Documents): title + one-line description, "List View" label, dark
  "+ Add X", real filter rows, status pills, empty states, row-click detail.
- Employees list: bulk-select checkboxes + admin-only **Export selected (CSV)**
  (formula-injection guarded, audited).
- Employee form is now **tabbed** (Overview / Address & Contacts /
  Attendance & Leaves / Salary (permission-gated, admin edit) / Personal /
  Documents & Photo / Joining & Exit) — all existing fields reused.
- Detail pages (employee, payroll run, settlement, ticket): breadcrumb header,
  status pill, right info panel (photo/status, created by/at, last edited
  from AuditLog, attachments shortcut), **activity timeline** from AuditLog.
- Dirty-form JS: amber "Not Saved" pill on the employee form, HR Settings
  and leave config (`data-dirty-watch`, `static/forms.js`, CSP-safe).
- Shared partials: `_frappe_css.html` (only classes missing from style.css),
  `_frappe_pills.html`, `_frappe_list_header.html`, `_frappe_empty.html`,
  `_frappe_timeline.html`.

### HR Settings (`/hrms/settings`, admin-only, audited)
Tabbed page (Employee / Leaves / Expenses / Shift & Attendance / Tenure /
Recruitment) backed by a new `SettingKV` table. Exactly four toggles are
**wired**; everything else is an honest display/link row:
1. `celebrations_enabled` — off → no new birthday/anniversary posts
   (existing posts kept; per-user opt-out intact).
2. `leave_notifications` — off → leave approval notifications suppressed
   (expense/other notifications unaffected).
3. `mobile_checkin` — off → punch page shows the disabled message and hides
   the punch form.
4. `geolocation` — off → punch skips the geofence check entirely (selfie
   still mandatory).
"Prevent self approval" is shown locked-ON (enforced by the approval engine).

## Frappe → ours mapping (decisions)
| Frappe | Ours |
|---|---|
| Awesome Bar (⌘K) | Ctrl+K → `/api/awesome`, role-filtered |
| Workspaces + live-count shortcuts | 11 workspaces, `/ws/<key>`, live counts |
| Dock + Sidebar + Workspace | app rail + workspace sidebar + content |
| Site dashboard tabs (Overview/Insights/Backups/Activity/Actions) | HR Setup home + HR Settings + Audit Trail + Reports hub |
| List View (filters, pills, bulk) | list conventions + bulk CSV export |
| Form View (tabs, info panel, timeline) | tabbed employee form + info panel + audit timeline |
| Frappe HR modules (13+) | our 41 domains mapped across the 11 workspaces |
| WPS SIF export | **kept and prominent** — Frappe HR has no native UAE WPS; our per-visa-company files are a differentiator |
| Geo-fenced selfie check-in | **kept** — Frappe has no selfie punch |
| Tax slabs/declarations | **not cloned** — no UAE income tax; EOS & Benefits workspace instead |
| Comments on forms | deferred — audit timeline only (see below) |
| Dark theme | Timeless Night-style slate via `ess_theme` cookie |
| Doodle wallpaper | retired from desk; login keeps flat cream tint |

## Tests
- New `tests/test_v36.py`: **76/76 pass** (shell per role, /apps, awesome API
  auth + role filtering + no salary data, workspace homes + 403 gating,
  settings save + audit + all four wirings, tabbed employee form round-trip,
  list filters + pills, dark-theme cookie, doodle retired / login cream).
- Full suite: **e2e 32, v2 33, v21 48, demo_data 54, v3 83, v31 102,
  v311 72, v32 71, v321 46, v33 107, v34 73, v341 67, v331 71, v35 39,
  v36 76 = 974/974 green.**
- Minimal legacy edits (markup legitimately changed), each documented:
  - `test_v32.py` — "nav has Weekly Roster/Final Settlements link": the
    grouped sidebar is replaced by workspace sidebars; checks now assert the
    links inside the Shift & Attendance and Tenure workspaces ("Weekly Roster"
    renamed to "Roster" per the Frappe naming in spec §3).
  - `test_v331.py` — notification tab checks now scope to the tab-filtered
    `.notif-list` (matching-div extraction): the desk shell's notifications
    slide-over renders all recent notifications on every page, so broad page
    greps no longer isolate a tab.
- Note: workstream D's interim report claimed 2 pre-existing failures in
  test_v34/v35; the final full run shows both suites fully green (73/73,
  39/39) — the failures were transient cross-workstream edits, now resolved.

## Deferred (not built)
- Kanban/Gantt/calendar list views; true saved-view persistence beyond the
  existing report templates.
- Comments on forms (activity timeline = AuditLog only).
- Frappe Cloud console tabs (benches/servers/billing) — mapped to HR
  Settings/Admin, not cloned literally.
- Interview reminders, exit questionnaire, multi-shift-per-date — honest
  display-only rows in HR Settings (no fake functionality).

## Screenshots
Playwright, local instance + demo seed → `~/workspace/your_files/hrms-theme-shots/`
with `v36-` prefix: login, HR Setup home, Employees list, Employee form
(tabs), Payroll home, Me (ESS) home, mobile home.

## V3.6.1 — step-up gate for the Geolocation toggle (Gemini fix round)

**Change.** Turning "Geolocation tracking" OFF (from on) in HR Settings
(`/hrms/settings`) now requires a step-up: (a) the admin re-enters their own
account password (verified with `User.check_password` against the scrypt
hash), and (b) a mandatory free-text Reason. Wrong password or empty reason
-> the WHOLE save is refused atomically (nothing persists, the toggle stays
ON), with a clear error flash. This atomic behaviour is the simpler and
safer choice vs partial saves. Turning geolocation back ON needs no
step-up; the other three toggles (celebrations, leave notifications, mobile
check-in) are unchanged. The audit entry for the change now carries the
reason: `HR setting 'geolocation' changed 1 -> 0 (reason: <text>)`.

**UI.** Unticking the geolocation checkbox reveals a small inline panel
(password + reason); the server is the real gate — a crafted POST without
the step-up fields fails the same way.

**Files.** `app/settings.py` (gate + audit), `app/templates/hr_settings.html`
(panel + JS), `test_v361.py` (22 checks), `test_v36.py` §5a (minimal legacy
edit: step-up fields on the fixture save — the save would otherwise be
refused by the new gate).

**Tests (fresh DBs, ESS_SECRET_KEY=testsecret12345, venv
~/workspace/ess-app/venv/bin/python):** v361 22/22 (off without stepup
refused + atomic no-partial-save; wrong password refused; empty reason
refused; correct password+reason succeeds with reason in the audit detail;
back ON without stepup; other toggles unaffected; employee POST 403),
v36 76/76, v33 107/107 — all green.
