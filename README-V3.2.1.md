# HRMS + ESS V3.2.1 — Legal-Fix Patch (2026-10-19)

Patch release. Applies on top of V3.2 + V3.3. All 641 tests green
(e2e 32, v2 33, v21 48, demo 54, v3 83, v31 102, v311 72, v32 68,
v33 107, v321 42 new).

## Legal basis

Corrections verified by the project owner against multiple independent UAE
legal sources (law-firm guidance citing Federal Decree-Law 33/2021, one
rechecked against MOHRE/UAE Government guidance):

1. **Resignation gratuity scale — REPEALED.** Our calculator implemented
   the 1980 Labour Law scale (1/3 for 1–3 years, 2/3 for 3–5 years).
   Under **Federal Decree-Law 33/2021, Art. 51** (in force 2 Feb 2022),
   a resigning employee with **≥ 1 year of continuous service gets FULL
   gratuity, identical to employer termination**. Example: basic
   AED 3,000, 4 years of service, resignation — old (wrong) AED 5,600 →
   correct **AED 8,400**.
2. **Unpaid absence excluded from service** (Art. 51): approved unpaid
   leave days + unexcused absence days do not count toward the gratuity
   service period.
3. **Payment within 14 days of contract end** (Art. 53).
4. **The 2-year cap stays on BASIC wage** (mainstream practice; unchanged).
   Notice pay stays on full wage; leave encashment stays on basic.

## What changed

- `app/uae.py` — `gratuity()` drops the resignation multipliers
  (`separation_type` kept for signature compatibility only); new keyword
  `unpaid_days=0`. Effective service = calendar service − unpaid days,
  floored at 0; < 365 effective days → 0; 21-day years 1–5 / 30-day
  years after; 24× basic cap unchanged. Implementation note: the function
  keeps its existing end-exclusive day-count convention, so the
  "exactly N years" reference math (4 yrs → 8400.0) stays exact.
- **Settlement** (`app/settlement.py` + templates) — at Calculate, unpaid
  days = approved `LeaveRequest` days with `leave_type='unpaid'` +
  `AttendanceRecord` rows with `status='absent'` across
  joining → last working day; passed to `gratuity()`; an `info` line
  "Unpaid absence days excluded from gratuity service: N" is added when
  N > 0 (excluded from totals); N stored on the settlement.
- **Due date** — `FinalSettlement.payment_due_date` = last working day +
  14 days; shown on list, detail and the printed statement as
  "Payable by \<date\> (14 days from last working day — Art. 53)";
  red **OVERDUE** badge when status is calculated/approved and today is
  past the due date; `is_overdue` property.
- **Receipt gate** — new `cancellation_doc_path`; the signed MOHRE
  cancellation / EOS receipt (PDF/JPG/PNG, magic-byte validated, private
  storage, randomised filename) can be uploaded on an approved (or
  calculated) settlement; **Mark Paid refuses without it** (flash error,
  status unchanged); receipt view route is HR/salary-permission only and
  audited.
- **Negative net** — settlements with net < 0 show **"Recovery required"**
  on the list, detail and statement (pure display logic).
- **Repatriation ticket** — new `ticket_option` (none/cash/booked/na) +
  `ticket_amount`, editable on create; at Calculate: cash → payable line
  "Repatriation ticket (cash in lieu)"; booked → info line; na → info line.
- **Roster** (`app/roster.py` + templates) — (a) week grid flags any
  employee with a run of **≥ 7 consecutive assigned days** (extended
  window ±1 day) with a "No weekly rest" badge next to their name;
  (b) new `ShiftPattern.is_outdoor` flag (checkbox on the pattern form,
  shown in the list); assigning an outdoor pattern on a date inside the
  summer midday season flashes a warning — "Midday break 12:30–15:00
  applies to outdoor work on this date — ensure the shift plan covers
  it." — without blocking.
- UI copy on the HR gratuity calculator and the employee estimate page now
  states the current law (full entitlement on resignation after 1 year;
  unpaid absence excluded) instead of the repealed scale.
- DB migrations are idempotent (`add_column` guards) — the live database
  picks up the new columns on the next deploy; no seed-data changes needed.
  Compliance liability totals rise to the correct lawful figures.

## Tests

`test_v321.py` (42 checks): resignation == termination at 8400.0 for
identical inputs; 7 yrs − 365 unpaid days → 13500.0; unpaid pushing service
below 1 year → 0; settlement fixtures with unpaid leave + absences produce
the info line and the reduced gratuity line; due-date property and
OVERDUE badge; mark-paid blocked → receipt upload → mark-paid succeeds →
receipt view (admin 200, employee 403, audit row written, bad file rejected);
negative net → "Recovery required"; ticket cash/booked/na lines; 7-day run
→ badge, 6-day run → none; midday flash on a summer date, none on a winter
date, none for indoor patterns.

Updated older expectations (all re-run green): `test_v21.py` resignation
5600 → 8400 with the Art. 51 citation; `test_v32.py` variance absence moved
to T32-E3 (so the T32-E1 settlement fixture stays absence-free) and the
mark-paid flow now uploads the cancellation receipt first.

## Deploy note

Redeploy from `main`; keep `ESS_SEED_DEMO` set in the hosting dashboard
(not in `render.yaml`). Never enable demo seed in production.
