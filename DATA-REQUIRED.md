# DATA-REQUIRED — Production data list for HRMS+ESS V3
Al-Ameen Industries LLC (800 Retail), Sharjah, UAE

**Status:** The system is built and tested with 20 FAKE demo users. Real
production data is **NOT** loaded and must **NOT** be uploaded to the free
sample demo or the public code repo. Provide the items below when the
production deployment on AWS is ready; HR will enter them into the live
system (or hand them over as Excel/CSV for import).

Do not include real names, IDs, or salaries in any document shared for
testing — fake data only for the demo environment.

---

## 1. Employee master (one row per employee)

Excel/CSV column order (header names exact; dates as YYYY-MM-DD):

| # | Column | Format / example | Required? |
|---|--------|------------------|-----------|
| 1 | `employee_id` | Text, unique, e.g. `AA670` | Yes |
| 2 | `full_name` | As on passport, e.g. `Shamsuddeen Edacheri Veettil Muhammed Ali` | Yes |
| 3 | `department` | Exact department name from the 13-department structure | Yes |
| 4 | `designation` | Job title as on MOHRE contract | Yes |
| 5 | `reporting_manager_id` | Employee ID of direct manager (must exist in file) | Yes |
| 6 | `joining_date` | `2026-08-06` | Yes |
| 7 | `exit_date` | `2026-12-31` (blank for active staff) | Only for leavers |
| 8 | `employment_status` | `active` / `resigned` / `terminated` / `retired` | Yes |
| 9 | `nationality` | Country name | Yes |
| 10 | `visa_company` | **Visa-sponsoring company per employee** — the company name/code from §2 | Yes |
| 11 | `passport_no` | Passport number | Yes |
| 12 | `passport_expiry` | `2029-03-14` | Yes |
| 13 | `visa_expiry` | Residence visa expiry, `2027-05-20` | Yes |
| 14 | `emirates_id_expiry` | Emirates ID expiry | Yes |
| 15 | `email` | Company email | If available |
| 16 | `mobile` | WhatsApp-capable mobile in `+971XXXXXXXXX` format (§7) | Yes |
| 17 | `iban` | UAE IBAN, exactly 23 characters, starts `AE` | Yes |
| 18 | `bank_name` | Full bank name, e.g. `Emirates NBD` | Yes |
| 19 | `contract_type` | `limited` / `unlimited` | Yes |
| 20 | `work_pattern` | `full-time` / `part-time` / `shift` | Yes |

Notes:
- Every employee must have **one** visa company (§2). This drives the
  per-company WPS file split.
- `employee_id` is the login username; it must never be reused.
- The `basic_salary`/`monthly_salary` in the system are informational only —
  real pay uses the salary components in §3.

## 2. Visa companies (legal entities)

One row per company that sponsors employee visas:

| Column | Example | Required? |
|--------|---------|-----------|
| `company_code` | `ALA` (short, uppercase, no spaces) | Yes |
| `company_name` | `Al-Ameen Industries LLC` | Yes |
| `mohre_employer_no` | MOHRE employer number | Yes |
| `wps_payer_id` / employer code at WPS agent | From your LuLu Exchange / bank WPS agreement | Yes |
| `wps_agent` | `LuLu Exchange` | Yes |
| `trade_license_no` + `trade_license_expiry` | | For records |

## 3. Salary component mapping (per-employee salary)

For each employee, one row per salary component they receive:

| Column | Example | Required? |
|--------|---------|-----------|
| `employee_id` | `AA670` | Yes |
| `component_code` | `BASIC`, `HRA`, `TRANSPORT`, `OTHER_ALLOW` or any custom component | Yes |
| `amount_aed` | Monthly amount, e.g. `3000.00` | Yes |
| `effective_from` | `2026-01-01` — the date this amount started applying | Yes |

Notes:
- Components available by default: `BASIC`, `HRA`, `TRANSPORT`,
  `OTHER_ALLOW` (earnings); `LOAN_RECOVERY`, `ADVANCE_RECOVERY`, `FINE`,
  `OTHER_DED` (deductions). Custom components can be added by the admin —
  tell HR their code, name, earning/deduction, and whether fixed or variable.
- **Overtime is always calculated on BASIC only.** Confirm each employee's
  BASIC figure matches their MOHRE-registered contract basic.
- Every employee in payroll **must** have a `BASIC` assignment; the payroll
  engine flags employees without one as exceptions.
- Changes to salary take effect from `effective_from`; past corrections are
  entered as arrears with the original period.

## 4. Opening loan / advance balances

| Column | Example | Required? |
|--------|---------|-----------|
| `employee_id` | `AA299` | Yes |
| `type` | `loan` or `advance` | Yes |
| `principal` | Original loan amount, AED | Yes |
| `installment` | Monthly recovery amount, AED | Yes |
| `balance` | **Outstanding balance at go-live**, AED | Yes |
| `start_period` | `2026-10` (YYYY-MM of first recovery) | Yes |
| `status` | `active` / `paused` / `closed` | Yes |

Notes:
- The payroll engine auto-recovers the installment each month, capped at the
  balance. Balances reduce **only when the payroll run is locked**, never on a
  draft — so opening balances must be exact at go-live.

## 5. Opening leave balances

| Column | Example |
|--------|---------|
| `employee_id` | `AA135` |
| `leave_type` | `annual`, `sick`, `maternity`, `parental`, `bereavement`, `study`, `hajj`, `unpaid` |
| `carried_forward_days` | Unused days from prior period to carry over |
| `entitled_days` | Annual entitlement if it differs from the UAE standard |

UAE statutory defaults are already configured (Annual 30, Sick 90, Maternity
60, Parental 5, Bereavement 5/3, Study 10, Hajj 30). Only supply balances that
differ from a fresh-year start.

## 6. IBAN / bank details

Covered in §1 columns 17–18 (`iban`, `bank_name`). Rules:
- UAE IBANs are exactly **23 characters** starting with `AE`.
- Employees without a valid IBAN are **flagged as payroll exceptions** and
  excluded from WPS until fixed.
- Bank-detail changes are maker-checker controlled — verify changes by
  calling the number on file before updating.

## 7. Mobile numbers for WhatsApp

- Covered in §1 column 16 (`mobile`), format `+971XXXXXXXXX`.
- Used for the notification outbox (payslip ready, leave approved/rejected,
  document-expiry reminders). Live sending activates only after the
  WhatsApp Business connection is configured; until then messages are queued
  in `/admin/outbox`.

## 8. LuLu Exchange WPS SIF specification

**Still to obtain from LuLu Exchange** (or your WPS agent bank):
1. The exact SIF fixed-width file layout (header record, employee detail
   records, salary control record, field positions/lengths).
2. Whether bonuses/allowances go in the SIF or are excluded (per UAE practice,
   SIF covers **regular monthly wages only** — bonuses, commissions,
   gratuity/EOSB are processed separately).
3. The WPS employer/payer identifier and submission channel (portal, SFTP,
   branch).
4. Rejection/error-file format and correction turnaround.
5. Salary-matching rule: confirm SIF figures must match MOHRE-registered
   contract salary exactly.

The system currently generates a **working CSV per visa company**
(`wps-<companycode>-<YYYYMM>.csv`) clearly labelled as pending LuLu's exact
SIF format. Do not submit it to the WPS agent as-is until the spec is
confirmed — the payroll team will map the fields once the spec arrives.

## 9. Document scans (per employee)

Upload formats: PDF, JPG, PNG — max **10 MB** per file. Categories:

| Category | Required? | Expiry tracked? |
|----------|-----------|-----------------|
| Passport | Yes | Yes |
| Emirates ID | Yes | Yes |
| Residence Visa | Yes | Yes |
| Labour Card / Work Permit | Yes | Yes |
| Education Certificate | Yes | No |
| Employment Contract | Yes | No |
| Offer Letter | Yes | No |
| Medical Certificate | As applicable | Yes |
| Driving Licence | For drivers only | Yes |
| Other | As applicable | No |

Notes:
- Name files as `<employee_id>_<category>_<version>.pdf` before upload,
  e.g. `AA670_Passport_v1.pdf` — the system stores its own random filenames
  internally.
- Employees can upload their own documents (go to HR as **Pending
  verification**); HR verifies or rejects with a reason.
- Uploading a newer version of the same category keeps the old one as
  **superseded** — full version history is kept.
- Only the employee and HR/Admin can view or download documents. Managers
  and other employees are denied (403) — by design.
- Downloads, verifications, and rejections are all audit-logged.

## 10. Employee photos

- JPG or PNG, **max 2 MB**, one per employee, named `<employee_id>.jpg/png`.
- Photos are private: visible only to the employee, their manager chain, and
  HR/Admin. Never sent to third parties without consent.

## 11. Salary visibility permissions

List the users who must see salary figures (HR Manager, finance approvers):
`employee_id` + `name`. The admin toggles `can_view_salary` for them in
Admin → Users. Everyone else is denied payroll pages (403) and sees no
salary figures in Employee Central or letters.

## 12. OT rate policy (per company)

Default multipliers are already configured (Normal 1.25, Night 1.50,
Rest-day 1.50, calculated on BASIC as monthly/30/8). If any visa company,
department group, or individual has different agreed rates, list them:

| Level | Name (company/department/employee) | Normal × | Night × | Rest-day × |

Precedence: individual → department group → company → system default.

## 13. Other configuration data

- **Holiday calendar:** UAE public holidays for the coming year (Islamic dates
  shift yearly).
- **Department list:** the 13-department structure with heads.
- **Approval chains:** leave/overtime/payroll approvers per department.
- **Grading bands** (if compensation bands are used).
- **Letter signatory:** name + title to print on generated letters.

---

*Prepared 2026-10-18. Fake demo data must never be mixed with real data —
the demo database is a separate, disposable copy.*
