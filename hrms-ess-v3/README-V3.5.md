# HRMS+ESS V3.5 — Helpdesk Tickets + Shift Swaps

**Test status: 898/898 green** (new suite `test_v35.py`: 39/39; all prior suites
unchanged and green; e2e 32, v2 33, v21 48, demo 54, v3 83, v31 102, v311 72,
v32 71, v321 46, v33 107, v34 73, v341 67, v331 71, v35 39).

## Part A — Helpdesk Tickets (`app/helpdesk.py`, models in `app/models_v35.py`)

An internal support channel, explicitly separate from the formal **Grievances**
module (the ESS page says so and links to Grievances for formal complaints).

- `HelpdeskTicket`: requester, category (IT | HR | Admin & Facility | Payroll |
  Other), subject, description, priority (Low/Normal/High/Urgent, default
  Normal), status (open → in_progress → resolved → closed), assignee, resolved_at.
- `TicketComment`: public comments (visible to requester) and **internal notes**
  (`is_internal`) that are never rendered to the requester and are excluded from
  every requester-facing query.
- ESS `/my-tickets`: raise + own list; `/my-tickets/<id>`: thread (public only),
  reply, Close (resolved only), Reopen (resolved/closed).
- Staff `/hrms/tickets`: queue with status / category / assignee ("Mine",
  "Unassigned") filters; access = admin, HR staff, HR manager, or any current
  assignee. Detail page: status change, assign/reassign (assignees limited to
  admins + HR staff + HR managers), public comment / internal note.
- Requester is notified (`Notification`, category `important`) on assignment,
  status change and public comment. The assignee is notified on assignment.
- **SLA (display only):** Urgent 4h, High 8h, Normal 24h, Low 72h from creation;
  open/in_progress tickets past due show an "SLA overdue" flag.
- All ticket actions are audit-logged (`ticket_created`, `ticket_assign`,
  `ticket_status`, `ticket_comment`, `ticket_closed`, `ticket_reopened`).

## Part B — Shift Swaps (`ShiftSwapRequest`, approval process `shift_swap`)

A swap **exchanges the shift patterns of two `RosterAssignment` rows**; each row
keeps its user and date. Both assignments must be in the future (Asia/Dubai)
at creation.

1. ESS `/my-swaps`: create by picking one of my upcoming assignments + a
   counterpart + one of their upcoming assignments. Validation: ownership,
   future dates, patterns differ, no duplicate open swap involving either row.
   Snapshots of both pattern ids are stored at creation.
2. The counterpart accepts/rejects on `/my-swaps` (also listed in the unified
   inbox under "Shift swaps needing my consent"). Accept starts the approval
   engine chain (subject = requester); Reject ends the swap and notifies the
   requester.
3. Approvers act on `/approvals` (new "Shift Swaps" section showing
   From → To patterns, rest-gap flags and the step badge). On **final**
   approval the two pattern ids are exchanged transactionally.
4. **Staleness guard:** the roster is re-validated *before* the engine advances
   the final step. If either assignment is missing, reassigned, or its pattern
   changed since the snapshot, the approval becomes a rejection with reason
   "Roster changed since request" — the engine instance and the swap stay
   consistent (both rejected).
5. **Cancel:** the requester can cancel while pending; a system action closes
   any open engine instance as rejected ("Cancelled by requester") so it
   disappears from resolvers' inboxes.
6. **Rest-gap warning (advisory only):** shown on the swap detail and in the
   approvals table when the exchange creates a tight turnaround — a NIGHT-
   crossing assignment the day before with the new pattern starting before
   12:00, or a midnight-crossing new pattern with next-day assignment starting
   before 08:00.
7. The matrix UI picks the new `shift_swap` process up automatically (fresh-DB
   default `[reporting_manager]`; demo chain `[reporting_manager]`); approval
   delegation, self-approval invariant and consecutive-step rules from V3.4.1
   apply unchanged.

## Demo seed (`_seed_v35_extras`, still exactly 20 users)

- Tickets: EMP007 open IT ticket "VPN not connecting" (Normal); EMP002
  resolved HR ticket "Payslip copy request" (Low) with one public comment.
- One swap in `pending_counterpart`: EMP007 ↔ EMP008 (NIGHT ↔ MORN), with a
  consent notification for EMP008.

## Routes added (total app routes: 177)

`/my-tickets`, `/my-tickets/<id>`, `/my-tickets/<id>/close|reopen`,
`/hrms/tickets`, `/hrms/tickets/<id>`, `/hrms/tickets/<id>/status|assign|comment`,
`/my-swaps`, `/my-swaps/<id>`, `/my-swaps/<id>/consent|cancel`,
`/approvals/swap/<id>` (engine decision), `/approvals/swap/<id>/counterpart`
(reminder nudge). Nav: "My Tickets" + "My Shift Swaps" (all users), "Helpdesk
Queue" (Manager section).

## Files added/changed

- Added: `app/models_v35.py`, `app/helpdesk.py`, `test_v35.py`,
  `README-V3.5.md`, templates `my_tickets.html`, `my_ticket_detail.html`,
  `hr_tickets.html`, `hr_ticket_detail.html`, `my_swaps.html`,
  `swap_detail.html`.
- Changed: `app/models.py` (new process key + v35 model imports),
  `app/approvals.py` (DEFAULT_MATRICES + demo chain for `shift_swap`),
  `app/__init__.py` (register `helpdesk_bp`), `app/views.py` (`swap_items`
  on the approvals page), `app/ess_extras.py` (consent items in the unified
  inbox + dashboard summary), `app/demo_seed.py`/`demo_seed_data.py`
  (`_seed_v35_extras`), `app/templates/{base,inbox,approvals}.html`,
  `app/templates/my_swaps.html` (consent table shows the note).

## Notes / intentional deviations

- No legacy tests were changed. The full regression was re-run on fresh DBs
  (e2e via `seed.py` from the staff master): **898/898**.
- One test-only fix during the build: an uncommitted fixture pattern from
  older suites (flush without commit inside a short-lived app context) bit
  three new fixture blocks; fixed by committing.
- The mobile bottom nav keeps its 5-item quick set (full nav is in the
  sidebar); no change needed.
- Residual anti-spoofing posture for selfie/GPS punches (V3.3, Gemini round)
  is unchanged by this release.
