# HRMS+ESS V3.1 — Reports Hub, Retail-Fixture Registers, App-Like Navigation

V3.1 builds on the V3 core (payroll cycle, document vault, letters, WhatsApp
outbox — see README-V3.md). All V3 behaviour is unchanged.

## 1. Reports & Excel Export Hub (`/hrms/reports`)

Admin-only hub. Each report has an on-screen HTML view plus an **XLSX download**
(openpyxl). Payroll-money reports (Additions & Deductions, OT Summary) are
additionally open to any user the HR Manager granted **salary visibility** to
(`can_view_salary`); every other report is admin-only.

| Report | Route | Notes |
|---|---|---|
| Master Data Export | `/hrms/reports/master` | Filters: department / employment status / visa company. Salary columns (Basic, Monthly) and Bank/IBAN appear **only** for viewers with salary permission — they are omitted entirely otherwise. |
| Additions & Deductions | `/hrms/reports/additions-deductions` | One row per employee from the selected payroll run's stored lines: per earning component, per deduction component, Overtime, "of which: Inputs", "of which: Arrears", Gross, Total Deductions, Net; department subtotals + grand total. Draft/unapproved runs show their status in the header. |
| Absence Report | `/hrms/reports/absence` | Month picker. Working days, Present, Absent, Late, Absence rate %, Absence spells, Bradford factor (optional, see formula notes). |
| Leave Report | `/hrms/reports/leave` | Ledger (Entitled / Used-approved / Pending / Remaining) and a Requests view (date range + status filter). |
| Separation Report | `/hrms/reports/separations` | Register + separated users merged; tenure in years (1 decimal); filters by year/type/department. |
| Turnover Report | `/hrms/reports/turnover` | Calendar-year picker or last-12-months; separations vs headcount; voluntary/involuntary/other; per-department table. |
| OT Summary | `/hrms/reports/ot-summary` | Normal / night / rest-day hours + amounts from run lines' OT JSON; totals. |
| Loan Balances | `/hrms/reports/loans` | Principal, Recovered (= principal − balance), Balance, Installment, Status. |

**Saved templates:** each report page offers "Save current filters" (named template
stored per user, re-appliable from a list). Scheduled/automatic report delivery
is **out of scope** — see §6.

## 2. Formulas used

- **Absence rate** = absent days / (present days + absent days) × 100 (1 decimal).
  Late days are shown separately and do not enter the denominator.
- **Absence spell** = a run of consecutive calendar days marked absent.
- **Bradford factor** = spells² × absent days. Shown as an *optional indicator*
  with an on-page footnote: it is a UK-origin metric, not a Gulf/UAE standard,
  and must not be used alone for disciplinary decisions.
- **Turnover %** = (separations in period / average headcount) × 100 (1 decimal);
  average headcount = (headcount at start + headcount at end) / 2.
  Voluntary = Resignation + Retirement; Involuntary = Termination + Absconding;
  Other = Contract End + Other. Turnover definitions vary by vendor — the page
  displays the definition used here.
- **Headcount approximation:** separations come from the separation register
  plus users with a separation date; joiners from joining dates. Headcount at
  period end = users joined by period end with no separation (or separation
  after period end). For past periods this is an **approximation** derived from
  current records (the page says so); the start headcount is back-computed as
  end − joiners + separations.

## 3. Export governance

- Every export (all 8 reports, XLSX only) writes an **audit record**: action
  `report_export`, entity `report:<key>`, detail with the report name and the
  filters used, actor = exporting user. HTML views are not audited (no data
  leaves the system).
- XLSX cells pass through the CSV formula-injection guard conventions; exports
  carry a `Content-Disposition: attachment` + `no-store` header.
- Download links carry `data-full="1"` + `download` so the app-like (PJAX)
  navigation never intercepts them.

## 4. Retail Operations registers (`/hrms/*`, new nav section)

For a retail-fixtures company (factory + office + project/site staff):
admin/HR manage, managers read-only, employees denied at register level and see
only their own records on their profile page.

- **Certifications** (`/hrms/certifications`): skill/trade certificates per
  employee; expiries within 60 days also appear on the Compliance page.
- **Projects & Deployments** (`/hrms/projects`): projects (name, client,
  location, active/completed); project detail shows deployed staff and total
  monthly site-allowance cost; employee profile shows current deployment.
- **Accommodation & Transport** (`/hrms/facilities`): housing/transport
  assignments with monthly cost.
- **PPE & Uniform** (`/hrms/ppe`): issuance/return log; own items on the profile.
- **HSE Incidents** (`/hrms/hse`): injury / near miss / property damage /
  unsafe act with severity and Open/Closed status. Managers may **create**
  incidents; only admin/HR can edit details and close them.
- **Contractor Workers** (`/hrms/contractors`): name, company, trade, site,
  dates, monthly rate; per-company monthly billing summary.
- **Separations** (`/hrms/separations`): the ex-employee register feeding the
  Separation and Turnover reports. Employee Central also carries optional
  *Separation Date / Type* fields (shown when status is resigned/terminated);
  such employees appear in the reports too (register rows first).

## 5. App-like navigation (PJAX)

`app/static/app.js` intercepts internal GET link clicks (sidebar, mobile bottom
nav, dashboard tiles) and swaps only the main content container (`#pjax-main`)
plus the page title, pushing history state — with a slim progress bar and a
subtle content fade. Prefetch on hover/touchstart (10-entry, 60-second cache);
back/forward via `popstate`; inline `<script>` blocks in swapped content are
re-executed. Safety exclusions (always full navigation): logout POST form and
all forms, download/export links (`/download`, `/export`, `/wps/`, `.csv`,
`.xlsx`, `?format=xlsx`, `download` attribute, `data-full`, `data-no-pjax`),
`/photo/` images, external links, `target=_blank`; any fetch/parse error falls
back to a full load; a redirect to `/login` (expired session) becomes a full
navigation. Static assets get `Cache-Control: public, max-age=31536000,
immutable`.

## 6. Deferred items (roadmap)

- Scheduled/automatic report delivery (email/WhatsApp) — not in V3.1.
- Piece-rate production inputs for factory payroll.
- Demand-based shift scheduling / roster engine.
- Full & Final settlement run type; LuLu Exchange exact SIF spec (still awaited);
  biometric attendance connector; document OCR.

## 7. Tests

`test_v31.py` — 102 checks (reports math, exports + audit rows, register
permissions, SVG-icon assertions, PJAX markers, cache headers). Full matrix
(all fresh DBs; `test_e2e` needs the staff-master seed via `seed.py`):

| Suite | Result |
|---|---|
| `test_e2e.py` | 32 / 32 |
| `test_v2.py` | 33 / 33 |
| `test_v21.py` | 45 / 45 |
| `test_demo_data.py` | 54 / 54 |
| `test_v3.py` | 83 / 83 |
| `test_v31.py` | 102 / 102 |
| `test_v311.py` | 72 / 72 |
| **Total** | **421 / 421** |

## 8. V3.1.1 — Gemini Argon review round

Applied from the independent Gemini (Argon) design/security review, parent-verified:

**Security hardening (exports)**
- XLSX formula-injection sanitisation: every string cell in every report export
  is neutralised (prefix `'` when it starts with `= + - @`, tab or CR) —
  mirrors the existing CSV guard (`app/audit.py::csv_cell`).
- Export watermarking: workbook properties carry the exporting user and report
  name; a hidden "Export Info" sheet records user, UTC timestamp and filters.

**New reports (Reports hub)**
- Payroll Variance Report: current vs previous run, per-employee net change %,
  joiner/leaver buckets, exceptions above 10%, totals bridge.
- Leave & EOSB Liability Report: accrued gratuity (Federal Decree-Law 33/2021,
  estimate only) + annual-leave value per employee, department subtotals.
- WPS Pre-Flight Check: per-run flags — missing Emirates ID number, invalid
  IBAN (AE + 21 digits), missing bank/visa company, net < 80% of gross,
  deductions > 50% of gross. Pre-flight only; WPS export format unchanged.

**Retail-fixture additions**
- Training Matrix: employees × certifications with valid / expiring (60d) /
  expired colour coding; department filter.
- Asset & Tool Custody register (tools, IT assets, vehicles) with return
  workflow; unreturned items flagged on the Separation register and report
  (clearance warning); employees see their own assets on their profile.

Test suite: `test_v311.py` (see §7 counts).
