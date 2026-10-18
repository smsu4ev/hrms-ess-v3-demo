# HRMS+ESS V3.3 — Employee Experience

Build date: 2026-10-19. Codebase: `~/workspace/hrms-ess-v3/`.
Tests: **594/594 green** across 9 suites (e2e 32, v2 33, v21 45, demo_data 54,
v3 83, v31 102, v311 72, v32 66, **v33 107** — new).

## 1) Sick-leave balance hidden from employees

- Employee-facing leave pages (dashboard "Leave Balances", `/leave`) no
  longer show a sick-leave *balance* row (keyed on the `sick` leave type).
- Applying for sick leave still works exactly as before.
- HR views keep showing it: Reports → Leave (ledger), approvals, exports.
- The statutory entitlement note (90 days, Art. 31) stays visible as legal
  information, with a note that sick leave is granted against an approved
  medical certificate and is not tracked as a balance.

## 2) Mobile geo + selfie check-in/out

- New ESS page **`/attendance/punch`** (mobile-first, linked from the
  Attendance page and dashboard tile): big Check In / Check Out buttons.
- Flow: GPS fix (high accuracy) → front-camera selfie (`capture="user"`)
  → on-device resize to ≤800px JPEG → multipart POST.
- **Work locations** (`/hrms/locations`, admin): name, lat/lng, radius (m).
  The server re-computes the haversine distance; punches outside every
  active fence are **rejected** (403 + `punch_rejected` audit entry).
  If no locations are configured, punches are accepted as "Unrestricted".
- Photos are re-validated and resized **server-side with Pillow**
  (PNG/JPEG magic bytes, ≤800px, JPEG q72), stored privately under
  `instance/uploads/punches/` with random names.
- Accepted punches update today's `AttendanceRecord` (first in, last out);
  if a rostered shift exists and check-in is later than
  shift start + grace → status `late`.
- **45-day photo retention**: `purge_punch_photos()` deletes photo files
  older than 45 days (records kept). Runs at startup, lazily on the punch
  page (once/day via `AppSetting`), and from an admin button on the
  locations page (shows the current stored-photo count).
- Photo privacy: `/attendance/punch-photo/<id>` — owner, admin, or the
  direct reporting manager only; non-owner views are audited.
- HR list: `/hrms/punches` with date filter (admin all; managers see
  direct reports); employee attendance page shows own punch history.
- **HTTPS (or localhost) is required** for geolocation and camera APIs;
  the app relaxes `Permissions-Policy` to `camera=(self),
  geolocation=(self)` on `/attendance/punch` only. If GPS/camera are
  unavailable, the page points to Attendance Regularisation.

## 3) Notices, notification centre, celebrations

- **Notices** (`/hrms/notices`, admin): title, body, audience (all or one
  department), optional expiry, **mandatory-read flag** (`requires_ack`).
  Publishing fans out a Notification to every active employee in the
  audience. HR sees ack counts (acknowledged / total) and the pending list.
- **Notification centre**: bell icon with unread badge in the top bar
  (all roles), `/notifications` list, mark-read / mark-all-read.
- Employees open a notice at `/notices/<id>` and click **Acknowledge**
  (recorded once per employee, audited).
- **Celebrations**: `User.date_of_birth` added (admin-editable in Employee
  Central). `ensure_todays_celebrations()` runs once/day on dashboard and
  `/celebrations` loads: birthday posts and ≥1-year work-anniversary
  posts; every other active employee gets a notification with a wish link.
- `/celebrations`: today's + last 7 days' posts, wishes (200 chars) and
  reactions from the fixed set 👍 ❤️ 🎉 👏 🎂 💐 (one reaction per user
  per post, re-reacting replaces). The celebrated employee is notified of
  written wishes.
- Dashboard widgets: latest 3 notices, "Action needed" card for
  unacknowledged mandatory notices, today's celebrations.

## 4) Training LMS-lite

- HR (`/hrms/training`): courses (title, category Induction/Orientation/
  Basic/Technical/Safety/Other, description, pass %, optional
  certification name + validity months), material upload
  (PDF/PNG/JPG/MP4 ≤25MB, private storage) or external link, quiz
  question editor (4 options, correct answer), assignment to all /
  department / single employee (notifications sent), results table.
- ESS (`/my-training`): assigned courses with status, material access
  (assignee or admin only, audited), quiz with instant score, unlimited
  retakes, best score kept. Passing issues a **Certification** (visible
  in the Training Matrix and compliance expiries) unless a current one
  already exists.

## 5) Job descriptions with digital-signature acknowledgement

- HR (`/hrms/jds`): JD library (position title, department, reports-to,
  structured body, optional PDF attachment). Editing the body bumps the
  **version**; acknowledgements are per-version, so old signatures show
  as "Re-acknowledgement needed".
- Assignment: to all employees, one department, or **by designation**
  (JD title matched against `User.designation`), with notifications.
  Per-JD dashboard: assigned / viewed / acknowledged / stale counts,
  pending list with a **Remind** button (re-sends notification).
- ESS (`/my-jd`): read the JD (marks `viewed`), then sign: typed full
  name + drawn signature on a canvas pad (touch/mouse) + mandatory
  checkbox. The signature PNG is validated, resized (≤600px), stored
  privately under `instance/uploads/signatures/`; acknowledgement is
  timestamped with signer IP and audit-logged.
- Signature serving (`/jd-signature/<ack_id>`): owner or admin only,
  non-owner views audited.

## Test table

| Suite | Result |
|---|---|
| test_e2e | 32/32 |
| test_v2 | 33/33 |
| test_v21 | 45/45 |
| test_demo_data | 54/54 |
| test_v3 | 83/83 |
| test_v31 | 102/102 |
| test_v311 | 72/72 |
| test_v32 | 66/66 |
| **test_v33 (new)** | **107/107** |
| **Total** | **594/594** |

`test_v33` also carries the regression test for the 2026-10-19
settlement hot-fix: a user **with** a BASIC `SalaryAssignment` —
`payroll.basic_salary()` returns the assignment amount (5000.0, not the
legacy field), and a full settlement **Calculate** succeeds for that
user (gratuity line present, status `calculated`).

## Demo seed additions

- DOBs for all 20 demo users; **EMP006's birthday is today** and
  **EMP007's 3-year joining anniversary is today** (celebrations demo).
- One work location: "Head Office — Sharjah (Demo)" 25.3054, 55.4248,
  300 m (clearly editable).
- Two courses: "Safety Induction" (PDF material, 3 questions, issues
  "HSE Safety Induction" cert valid 12 months, assigned to all 20,
  EMP001 already passed) and "Company Orientation" (link material).
- One mandatory-read notice, fanned out to all 20.
- Two JDs ("Welder", "HR Executive"); Welder JD assigned by
  designation, one welder pre-acknowledged with a generated signature.

## Notes / deferred

- Celebration/notice links use context-safe paths so demo seeding works
  at app startup (no request context).
- The app calendar is Asia/Dubai (fixed +4h offset); the demo seed's
  "today" now uses GST too, so birthday/anniversary demos line up.
- Scheduled WhatsApp sending for notices/celebrations is not wired yet
  (the WhatsApp outbox pattern from v3 can be reused later).
- Signature pad uses an external JS file (`jd_sign.js`) for CSP safety;
  the JD "Open" links carry `data-no-pjax` so the page loads fully.
