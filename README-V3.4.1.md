# HRMS+ESS V3.4.1 — Gemini-review fix round

Date: 2026-10-19. All items below were verified against the code by the
parent agent from the Gemini (Argon) review findings; every item is covered
by automated tests. Baseline: 714/714. After this round: **788/788 green**
(e2e 32, v2 33, v21 48, demo 54, v3 83, v31 102, v311 72, v32 71, v321 46,
v33 107, v34 73, v341 67). New suite: `test_v341.py` (67 tests).

## 1) Approval engine hardening (`app/approvals.py`)

a) **Self-approval invariant.** `can_act()` now returns `False` when the actor
is the request's subject or requester — on every path, including the admin
backstop. Decision endpoints (`/approvals/<id>`, regularisation approvals,
employee review) catch the resulting `PermissionError` and flash
"You cannot approve your own request." instead of a bare 403.

b) **Consecutive-step rule.** In `act()`, if the immediately preceding human
action on the instance was by the same actor, the action is denied
(`PermissionError`) when at least one other user can resolve the current
step. When the actor is the sole resolver, the action is allowed and the
recorded comment gets the server-side suffix
"[sole resolver — consecutive step]".

c) **Final-step no-skip + halt.** `_auto_skip_empty()` never skips the last
step of a chain. If the last step has no resolvers, the instance gets
`halted=True` (new column on `approval_instances`), stays pending, all
admins get a "Approval routing halted" notification, and an
`approval_halted` audit record is written (plus an `ApprovalAction` of type
`halted`). An admin who is neither subject nor requester may then decide
the halted instance (`can_act` covers this).

d) **Delegation.** New `ApprovalDelegation` model (delegator → delegate,
optional process, start/end dates, active flag). For person-specific
resolutions (reporting manager, skip-level manager, flagged HR Manager
holders, specific user), an in-window active delegate is added as an
*additional* resolver (the original person stays eligible). Delegated
actions are audited as `approval_delegated_action` naming delegator and
delegation id. Admin UI: a Delegations section on `/hrms/approval-matrix`
(add with validation — delegate ≠ delegator, delegate must be active;
deactivate button; "Active/Inactive" state per delegation).

## 2) Leave double-spend guard (`app/views.py`)

- Submission: pending requests of the same leave type count against the
  balance — a new request is rejected when
  `used + pending_days + new_days > entitled` (the flash shows the pending
  amount). The employee's own `LeaveBalance.entitled` is now authoritative
  when present (previously the config default was used even for pro-rated
  balances).
- Final approval: the balance is re-validated at the last step before the
  chain advances. If the request would overdraw, approval is refused with
  "Insufficient remaining balance — request stays pending"; the instance
  stays at the final step for HR to resolve or cancel. Applies to the
  single-step legacy flow identically.

## 3) Settlement disbursement split (`app/settlement.py`)

Flow is now **draft → calculated → approved → disbursed → paid**:
- Gate 1 (disbursement): `POST /hrms/settlements/<id>/mark-disbursed`
  requires a payment proof upload (bank transfer advice / WPS confirmation /
  cheque copy; PDF/JPG/PNG, magic-byte validated, private
  `settlement_payments` storage, randomised name) plus a payment reference.
- Gate 2 (closure): Mark Paid now requires status `disbursed` **and** the
  signed MOHRE cancellation / EOS receipt (unchanged requirement, now the
  second gate). Mark Paid from `approved` is refused.
- Disbursement stops the Art. 53 overdue clock (`is_overdue` only counts
  `calculated`/`approved`). List, detail and statement show the disbursed
  date, reference, and a payment-proof view link (salary-permission, audited
  as `settlement_payment_proof_viewed`).

## 4) Unpaid-exclusion refinement (`app/settlement.py`)

`unpaid_absence_days()` no longer counts an `AttendanceRecord` 'absent' day
that falls inside an **approved** leave period of any non-unpaid type (e.g.
sick days marked absent by a bulk import — approved statutory leave, not
unexcused absence). Approved unpaid-leave days still count, and an absent
day inside an unpaid period counts exactly once (no double counting).

## 5) Punch hardening (`app/engagement.py`)

a) `PunchRecord.low_accuracy`: set when reported GPS accuracy > 150 m. The
punch is still accepted inside a geofence; the HR punch list shows a
"Low GPS accuracy" flag and a `punch_low_accuracy` audit record is written.
b) Rejection message: when the nearest active location is more than 2 km
away, the response is the generic "You are outside every authorised work
location." with no distance or location detail; within 2 km the
nearest-location + distance hint is kept.
c) Legal hold: `PunchRecord.legal_hold`; `purge_punch_photos()` skips held
records; `/hrms/punches` has an admin-only hold/release toggle per record
(POST, audited as `punch_legal_hold` / `punch_legal_hold_released`).

## 6) JD tamper evidence (`app/engagement.py`)

`JDAcknowledgement` gains `jd_text_hash` (SHA-256 of
`title + "\n" + body + "\n" + version`, hex) and `user_agent` (request
User-Agent, truncated to 255) recorded at acknowledgement. The HR JD detail
page shows the first 12 hash characters per acknowledgement; the
`jd_acknowledged` audit detail carries the hash prefix. Pre-existing seed
acknowledgements keep a null hash (accepted).

## 7) Celebration opt-out

`User.share_celebrations` (default True); a toggle on the employee profile
page ("Share my birthday and work anniversary on the company feed").
`ensure_todays_celebrations()` skips post creation for opted-out users —
they still receive notifications and can wish others.

## 8) Quiz shuffle (`app/engagement.py` + `my_training_quiz.html`)

The quiz render shuffles question order and each question's option order
server-side on every attempt. Options are submitted by their **original**
index, so grading (against `correct_index`) is identical to an unshuffled
render — verified by test (100% pass with correct original indices).

## Legacy test changes

- `test_v32.py` / `test_v321.py`: paid flows now pass through disbursement
  (payment proof + reference first), with comments citing the two-gate
  design (payment proof at disbursement; signed MOHRE/EOS receipt at
  closure).
- `test_e2e.py`: the admin approving their own leave now correctly hits the
  self-approval invariant — the test assigns a reporting manager and
  approves as them, then logs back in as the admin for the remaining
  admin-only steps (commented).
- `test_v33.py`: the far-rejection test now expects the generic message
  (no distance detail beyond 2 km), per item 5b.

No changes to `hrms-ess-v2/` or `ess-app/`.
