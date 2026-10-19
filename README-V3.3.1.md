# HRMS+ESS V3.3.1 — ESS Extras

**Base:** V3.4.1 (788/788 green). **Adds 71 new checks** (`test_v331.py`) — 859/859 green, no legacy suite changed.

Seven feature areas. All existing suites (e2e 32, v2 33, v21 48, demo 54,
v3 83, v31 102, v311 72, v32 71, v321 46, v33 107, v34 73, v341 67) still pass
unchanged; the demo still seeds exactly 20 users.

## 1. Unified inbox (`/inbox`, all roles)

One "My Inbox" page aggregating the current user's action items, each row
linking to where the action happens:

- **Approvals awaiting my decision** — approval instances where I am a
  current resolver (via `inbox_for()`), with process label, subject and
  "step x of y"; links to `/approvals` per item (no decision logic
  duplicated).
- **Mandatory notices I have not acknowledged** — `NoticeAck`-missing
  `requires_ack` notices; links to `/notifications`.
- **Job descriptions awaiting acknowledgement** — assigned + unacknowledged,
  incl. re-ack after a version bump; links to `/my-jd`.
- **Training assigned to me, not yet passed**; links to `/my-training`.
- **My own requests in flight** (pending leave / regularisation / expense /
  letter requests with current step/status) — informational.

**Dashboard:** "My tasks" card with the total action count + top 3 items,
linking to `/inbox`. **Nav:** "Inbox" link (SVG mail icon) for all roles.
Empty states shown when nothing is pending.

## 2. Expense claims (`expense` approval process)

- `ExpenseClaim`: user_id, claim_date, category (Travel|Fuel|Food|
  Accommodation|Materials|Other), amount (2dp, > 0 enforced), description,
  receipt_path (private storage, PDF/JPG/PNG ≤ 10 MB, magic-byte
  validated), status (pending|approved|rejected), payroll_input_id,
  decided_by, decision_comment.
- **ESS `/my-expenses`:** submit form with receipt upload + own list with
  status and current approval step.
- **Approval:** wired into `app/approvals.py` as process key `expense`
  (subject = claimant). Default matrix on fresh DBs: `[reporting_manager]`;
  demo seed: `[reporting_manager, hr_staff]`. The matrix admin UI lists the
  expense process automatically (driven by the process list), and approvers
  act from `/approvals` (expense items render amount + category).
- **Final approval side effect:** creates a `PayrollInput` — an **EARNING**
  labelled `Expense reimbursement (claim #<id>)` for the **current period**
  (`YYYY-MM`, the same period format all payroll inputs use), sets
  `claim.payroll_input_id`, notifies the claimant
  ("Expense approved — added to payroll inputs"). The payroll calculator
  (`calculate_payroll_line`) consumes it exactly like other earning inputs
  (verified by a draft-calc test).
- **Rejection:** status rejected + notification with reason; no payroll input.
- **HR `/hrms/expenses`** (admin/hr_staff/hr_manager): all claims with status
  + department filters, approved totals, link to the created payroll input.

## 3. People directory + org chart

- **`/directory`** (any logged-in active user): searchable (name/designation/
  department) grid of ACTIVE employees — photo or initials, name,
  designation, department, reporting-manager name. **Privacy:** never renders
  phone, IBAN, bank, salary, DOB or Emirates ID (tests grep the HTML for
  seeded phone/IBAN values).
- **`/org-chart`** (any logged-in user): read-only nested tree from
  `reporting_manager` links; roots = users with no (or inactive) manager;
  cycle-guarded with a depth cap of 12; name + designation + department only.

## 4. Letter requests

- `LetterRequest`: user_id, letter_type (salary_certificate |
  salary_transfer | employment_certificate | experience_letter | noc),
  purpose/note, status (submitted|in_progress|ready|delivered|rejected),
  generated_letter_id (FK), uploaded_path (HR-uploaded signed PDF),
  hr_comment.
- **ESS `/my-letters`:** request form (type + purpose) + own list with
  status. When ready/delivered, a download button (generated-letter print
  view or the uploaded file) — **only the owner can download (others get
  403)**. Employee confirms receipt → `delivered` ("Mark received" button).
- **HR `/hrms/letter-requests`** (admin/hr_staff/hr_manager): queue with
  Start (→ in_progress), **Generate** (creates the letter via the existing
  mail-merge engine and links it → ready; requires salary permission
  (admin or `can_view_salary`) for `salary_certificate`/`salary_transfer`
  types — non-salary types have no gate), **Upload** signed PDF instead
  (→ ready), **Reject** with comment. Employee notified at every status
  change.
- **New mail-merge templates:** "Employment Certificate" and "Experience
  Letter" using existing merge fields (name, ID, designation, department,
  joining date, company); NOC generates from a generic template that merges
  the request's purpose text (`{{purpose}}` placeholder, passed through
  `merge_placeholders(body, user, extra={"purpose": ...})`).

## 5. Team leave calendar (`/team-calendar`, all roles)

Current month with `?month=YYYY-MM` navigation; lists **APPROVED** leave of
employees in the viewer's **own department** (managers: their department;
employees: their own department): name, leave-type label, start–end dates.
No balances, no reasons, no pending leave. Other departments hidden;
**admins see all** with a department filter.

## 6. Department / stakeholder overview (`/team-overview`)

- **Access:** admins, hr_staff, hr_manager see ALL departments; managers see
  only their own department + departments of their direct reports; plain
  employees get **403**.
- **Summary table** (one row per department in scope): Department | Total
  staff (active users) | Absent today (`AttendanceRecord` status `absent`
  for today's Asia/Dubai date via the app's fixed UTC+4 helper) | On
  vacation now (approved `LeaveRequest`s covering today) | Joined last
  month (joining_date within the previous calendar month) — plus a totals
  row.
- **Absent-today cell links** to `/team-overview/absent?dept=<name>` listing
  today's absent employees in scope (403 outside scope).
- **Dashboard:** managers/admins get a "Department today" mini-card (staff,
  absent, on vacation in scope) linking to `/team-overview`.

## 7. Notification centre tabs

- `Notification.category`: `'important'` (default) | `'event'`. Set at
  creation: celebration-related notifications (birthday/anniversary post
  alerts, wishes received) are `'event'`; everything else (notices incl.
  mandatory acks, approvals, JD, training, payroll, expenses, letters,
  documents) is `'important'`. `notify_user()` takes a `category` kwarg
  defaulting to `'important'`; the two celebration call sites pass
  `'event'`.
- **Migration/backfill** (idempotent, runs at startup): the column is added
  to existing DBs; existing rows default `'important'`, and rows whose
  `kind` is `birthday`/`anniversary`/`wish` are re-classified to `'event'`.
- **`/notifications`:** two tabs — "Important Updates" and "Events" — each
  with its own unread-count badge; "Mark all read" acts **per tab**; the
  header bell badge still shows the total unread. The unified `/inbox` is
  unchanged (action items are 'important' by nature).

## Demo seed additions (20 users unchanged)

- Expense matrix `[reporting_manager, hr_staff]`.
- **EMP003:** expense claim pending at step 1 with a minimal valid PDF
  receipt generated in code.
- **EMP004:** approved expense claim with its linked `PayrollInput`.
- **EMP002:** salary-certificate letter request (submitted).
- **EMP001:** employment-certificate letter request (ready, mail-merged
  generated letter linked).

## Tests (`test_v331.py`, 71 checks)

- Inbox: manager with pending approval sees count + `/approvals` link;
  employee with unack mandatory notice / pending JD / pending training sees
  each; clean user sees all five empty states; dashboard shows "My tasks".
- Expense: submit with valid PDF → pending; step-1 approve → still pending;
  step-2 approve → `PayrollInput` exists with the exact amount and the draft
  payroll calc includes it in earnings; rejection → no input; bad magic
  bytes rejected; amount ≤ 0 rejected; matrix UI lists the expense process;
  `/approvals` renders the expense item.
- Directory: search filters; no seeded phone/IBAN in HTML; inactive user
  absent.
- Org chart: manager→report pair nested.
- Letters: submit → start → generate → owner 200 / other-employee 403 →
  received → delivered; salary certificate refused for HR staff without
  salary permission, succeeds for admin; NOC merges the purpose text.
- Team calendar: own-dept approved leave visible, other dept hidden, pending
  hidden, admin dept filter.
- Team overview: manager sees own + reports' departments with correct
  numbers; admin sees all + totals; employee 403 (both pages); absent list
  scoped (403 outside scope); dashboard card.
- Notification tabs: celebration in Events, notice in Important, per-tab
  unread counts, per-tab mark-all-read, backfill classification.

## Deviations

- None from the brief. Legacy suites untouched; the e2e suite passes against
  the v3.4.1 `seed.py` fixtures without modification.
