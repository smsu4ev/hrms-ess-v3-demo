# HRMS+ESS V3.4 — Configurable Approval Matrix

Build date: 2026-10-19. Codebase: `~/workspace/hrms-ess-v3/`.
Tests: **714/714 green** across 11 suites (e2e 32, v2 33, v21 48, demo_data 54,
v3 83, v31 102, v311 72, v32 68, v321 42, v33 107, **v34 73** — new).
No legacy test was edited to accommodate the wiring; all failures found
during the build were fixed in the wiring/engine (see §7).

## 1) What was built

A configurable, per-process approval chain engine (`app/approvals.py`) covering
three processes: **employee creation**, **leave**, and **attendance
regularisation**. The HR Admin customises each chain at
`/hrms/approval-matrix` (nav: HR Admin → Approval Matrix): ordered steps,
add/remove/reorder, per-step approver types, and a "who resolves this today"
preview. Chains can differ per process (e.g. Employee → Supervisor → Manager
→ HR Staff → HR Manager).

**Approver types:** Reporting Manager, Skip-Level Manager, HR Staff (pool —
any flagged staff member may act, first action wins), HR Manager (pool, falls
back to all admins when nobody is flagged), Administrator, Specific User.

**Engine rules:**
- Strict resolvers: only the current step's resolved users may act. Admins
  additionally resolve HR Manager / Administrator steps, and may decide
  chains whose steps all auto-skipped.
- Steps with no resolvable users auto-skip (recorded as `auto_skip` actions +
  audit entries) so a chain never deadlocks.
- Each instance snapshots its step list at start; later matrix edits never
  mutate in-flight requests.
- Reporting Manager with no assigned manager falls back to the active
  managers/admins of the employee's department (preserves the pre-v3.4
  department-scoped approval behaviour).
- Every advance notifies the next resolvers; final approval/rejection
  notifies the requester (V3.3 Notification model, kind `approval`).
- All engine events are audit-logged (`approval_started`, `approval_advanced`,
  `approval_auto_skip`, `approval_final_approved`, `approval_rejected`,
  `approval_matrix_changed`).

## 2) Employee creation (the headline rule)

- `/hrms/employees/new` now accepts creators with role admin **or** the
  `is_hr_staff` / `is_hr_manager` flags (previously admin-only; employee
  *edits* stay admin-only per the H1 fix).
- HR Manager (or admin) creator → record created active immediately, as
  before. HR staff creator → record created with
  `employment_status="pending_approval"`, `is_active=False` (cannot log in),
  intended status stored in `User.pending_status`; role is forced to
  `employee` and salary fields are ignored even if posted.
- Review page `/hrms/employees/<id>/review`: full read-only field display,
  step badge, approval history, Approve/Reject buttons visible only to the
  current step's resolvers. Approve → activated with the intended status;
  Reject → stays inactive, creator notified. Visible to resolvers, the
  creator, HR managers and admins.
- Employee Central shows a "Pending Approval" badge/filter; pending rows
  link straight to the review page.

## 3) Leave & regularisation

- Submitting a leave or regularisation request starts a chain. The existing
  approval endpoints act **through** the engine: intermediate approvals keep
  the request `pending` (no side-effects); the final approval runs the
  existing side-effects (leave balance deduction, approver fields, WhatsApp
  outbox; attendance correction for regularisation); any rejection sets
  `rejected` with the step comment.
- Step progress ("Step 2 of 3 — HR Staff") shows on the approvals page, the
  employee's leave list, and the regularisation approvals page.
- `/approvals` is now a unified inbox: pending leave items (with per-item
  action buttons only for the current resolver) plus "awaiting your action"
  sections for regularisation and employee-creation chains. Access widened to
  managers, admins, HR staff and HR managers; non-resolvers see only a
  waiting note, and plain employees are still blocked (403).

## 4) Defaults & demo seed

- Fresh databases get idempotent defaults at startup:
  `employee_creation=[hr_manager]`, `leave=[reporting_manager]`,
  `regularisation=[reporting_manager]` — the pre-v3.4 single-manager
  behaviour, so upgrades keep working.
- Demo seed (`ESS_SEED_DEMO=1`): HRADMIN flagged `is_hr_manager`;
  EMP005 (Fatima Noor) flagged `is_hr_staff`; demo chains
  `employee_creation=[hr_manager]`,
  `leave=[reporting_manager, hr_staff, hr_manager]`,
  `regularisation=[reporting_manager, hr_staff]`.
- New `User` columns (`is_hr_staff`, `is_hr_manager`, `pending_status`) are
  added by the existing column-migration pattern; both flags are toggleable
  on the Admin Users detail page.

## 5) Files changed

- `app/models.py` — `User` flags + `pending_status`; new models
  `ApprovalMatrix`, `ApprovalStep`, `ApprovalInstance`, `ApprovalAction`;
  `APPROVAL_PROCESSES`, `APPROVER_TYPES` constants.
- `app/approvals.py` — **new**: engine + `/hrms/approval-matrix` UI.
- `app/__init__.py` — blueprint registration, column migrations, default
  matrix seeding at startup.
- `app/hrms.py` — creation access + pending flow, review route, list
  badge/filter; `app/views.py` — leave/regularisation submit + unified
  inbox + engine-driven decisions; `app/admin.py` — flag toggles.
- `app/demo_seed.py`, `app/demo_seed_data.py` — `_seed_v34_extras`.
- Templates: `hr_approval_matrix.html` (new), `hr_employee_review.html`
  (new), `approvals.html` (unified inbox), `regularisation_approvals.html`
  (badges), `leave.html` (progress chip), `hr_employees.html`
  (pending badge/filter), `hr_employee_form.html` (HR-staff notice),
  `admin_user_detail.html` (flag toggles), `base.html` (nav link).
- `test_v34.py` — 73 checks; `README-V3.4.md` (this file).

## 6) Test matrix (all fresh DBs; e2e via `seed.py`)

| Suite | Result |
|---|---|
| test_e2e.py | 32 / 32 |
| test_v2.py | 33 / 33 |
| test_v21.py | 48 / 48 |
| test_demo_data.py | 54 / 54 |
| test_v3.py | 83 / 83 |
| test_v31.py | 102 / 102 |
| test_v311.py | 72 / 72 |
| test_v32.py | 68 / 68 |
| test_v321.py | 42 / 42 |
| test_v33.py | 107 / 107 |
| test_v34.py | 73 / 73 |
| **Total** | **714 / 714** |

`test_v34.py` covers: flag toggles (admin-only), matrix UI add/delete/reorder
+ last-step refusal, pending creation (inactive, login blocked, role/salary
forcing), HR-Manager review approve (activation + intended status + login) and
reject (stays inactive + creator notified), hr_manager direct creation,
3-step leave chain (balances only on final approval, non-resolver 403,
mid-chain reject), auto-skip with recorded action, in-flight snapshot
immunity, specific-user steps, regularisation through the engine, and inbox
scoping.

## 7) Issues found and fixed during the build (no legacy test edited)

1. Approval notifications used the shared integer `Notification.ref_id`
   namespace, colliding with notice IDs in fan-out counts (broke a v33
   check). Approval notifications no longer set `ref_id`; the `link` field
   carries the destination.
2. `can_act` short-circuited admins before checking resolver membership, so
   an admin who was also the resolved approver was denied (broke e2e leave
   approval). Resolvers are now checked first, then admin backstops.
3. Matrix step reorder hit the UNIQUE `(matrix_id, step_no)` constraint on a
   direct swap; reorder now swaps via a temporary step number.
4. Demo seed `_seed_v34_extras` missed the module's function-local import
   convention (`db`, `User`) — fixed.

## 8) Not in this build

- Unified inbox, expense claims, people directory/org chart, helpdesk
  tickets, letter requests, team leave calendar, shift-swap requests
  (V3.3.1 ESS round — queued).
- Shift-publish workflow, punch-to-shift OT pairing, Nafis tracker, bank ACK
  reconciliation, biometric connector (still awaiting machine brand/model).
- The blueprint page's embedded gratuity calculator still uses the old
  resignation scale — flagged separately for correction.
