# HRMS + ESS V3 — Al-Ameen Industries LLC (800 Retail)

**Status:** Core build complete and tested (2026-10-18). English only.
Demo data is 100% fake (20 sample users). This build implements the approved
research blueprint
(`research_notes/advanced-ai-hrms-uae-blueprint-20261018-1644/report.md`) at
core-build depth. Items explicitly deferred to later phases are listed at
the bottom — nothing was silently dropped.

---

## 1. What's new in V3 (mapped to blueprint sections)

| # | Feature | Blueprint § | Routes (main ones) |
|---|---------|-------------|--------------------|
| A | **Companies (visa companies):** Company master + admin CRUD; `User.visa_company_id`; demo seed assigns 20 staff across 3 fake companies (`ALA`, `R800`, `AAS`) | §2.1(1) Organization management | `/hrms/companies` |
| B | **Salary components master:** code/name/kind (earning/deduction), fixed/variable, WPS bucket (basic/variable), is_statutory, active; admin add/deactivate/delete with delete-guard + audit; defaults seeded (`BASIC`, `HRA`, `TRANSPORT`, `OTHER_ALLOW`; `LOAN_RECOVERY`, `ADVANCE_RECOVERY`, `FINE`, `OTHER_DED`). Per-employee `SalaryAssignment` (amount, effective_from) edited in Employee Central salary section | §3.1 Salary structures & components | `/hrms/salary-components`, `/hrms/employees/<uid>/salary` |
| C | **`can_view_salary` permission:** admin toggles it in Admin Users; payroll pages, salary assignments, Employee Central salary figures, compliance/gratuity and salary letters require admin **or** the flag — enforced server-side (403), not just hidden in the UI | §8 field-level salary permissions; §3.4 role segregation | Admin → Users |
| D | **OT rate rules:** `OTRateRule` at company / group(department) / individual level; resolver precedence individual > group > company > default (normal 1.25, night 1.50, rest-day 1.50); hourly rate = monthly basic / 30 / 8; admin settings page; OT input lines per employee/category computed through resolver | §3.2 Overtime categories; §4.1 | `/hrms/ot-rules` |
| E | **Loans:** Loan master (loan/advance, principal, installment, start period, balance, status); HR CRUD; payroll auto-recovers installment **capped at balance**; balance reduces **only when the run is locked** (never on draft); audit | §3.1 Loans/advances | `/hrms/loans` |
| F | **Payroll inputs:** additions/deductions/arrears per period with free label, LOP days, is_arrears + original_period; arrears render on the payslip with the original-period label | §3.1 Additions; arrears | `/hrms/payroll/<id>/inputs` |
| G | **Payroll engine + run cycle:** `PayrollRun` draft → in_review → locked → approved; `PayrollLine` frozen results; payable_days = days_in_month − LOP (approved unpaid leave + manual LOP); mid-month joiner/leaver proration on payable/30 for basic + fixed components; OT via resolver; loan recovery; inputs applied. Review page shows **exceptions** (missing IBAN, missing visa company, missing salary/BASIC, negative net) and **variance vs previous approved run** (>10% flagged). Lock freezes lines (recalc blocked), admin-only unlock with audit. **SoD: creator cannot approve own run.** Run-based detailed payslip (components, arrears, OT lines, loan balance after recovery); falls back to the simple payslip when no run exists | §3.3 Payroll cycle; §3.2 attendance-linked payroll & proration; §3.4 controls & audit | `/hrms/payroll` (+ `/hrms/payslip`) |
| H | **WPS per company:** page lists each visa company with employee count + totals for a locked/approved run; export `wps-<companycode>-<YYYYMM>.csv` (Employee ID, Name, Emirates ID, IBAN, Bank, Basic, Variable, Total Net, Payable Days). **Working file only — pending LuLu Exchange's exact SIF specification** (§2 below) | §3.3(8); §4.4 WPS | `/hrms/wps` |
| I | **Employee photo:** `User.photo_path`; upload from Profile (self) and Employee Central (HR); JPG/PNG validated by extension + magic bytes, max 2 MB; stored under instance uploads (never static); authenticated `/photo/<uid>` route (self, manager chain, admin/HR only — other employees denied); shown in profile, Employee Central list + 360, dashboard; initials placeholder when none | §6 Photos & privacy | `/profile/photo`, `/photo/<uid>` |
| J | **Document vault:** `EmployeeDocument` (category, title, stored random filename, issue/expiry, status pending/verified/rejected/superseded, version, uploaded_by/verified_by); categories: Passport, Emirates ID, Residence Visa, Labour Card, Education Certificate, Employment Contract, Offer Letter, Medical Certificate, Driving Licence, Other. Employee uploads own (→ pending); HR uploads/verifies/rejects with reason; same-category re-upload increments version, old kept superseded. Download route: self + admin/HR only; managers/other employees **403**. PDF/JPG/PNG, max 10 MB, magic-byte check. Employee sees own + upload form; HR sees all with filters (employee, category, status, expiring ≤60 days); compliance page lists vault expiries with "Queue WhatsApp reminders"; audit on upload/verify/reject/download | §5 Document management | `/documents`, `/hrms/documents` |
| K | **Letters + mail merge:** `LetterTemplate` with `{{placeholders}}`; seeded Offer Letter, Salary Transfer Letter, Salary Certificate; admin edits templates; HR/manager generates for an employee → print-friendly page + `GeneratedLetter` snapshot + audit. `{{basic_salary}}`/`{{monthly_salary}}` render **only with salary permission**, otherwise generation of salary templates is blocked with a clear message | §2.5(26) Letters | `/hrms/letters` |
| L | **Rule-based insights:** "Rule-based insights (AI-assisted checks)" panel on payroll review + compliance — duplicate IBAN, negative net, missing bank/company, OT > 60 h in a period, net variance > 10%, document expiries by category, probation endings. No external calls, no fake ML claims | §7(a) anomaly detection | Payroll review + Compliance pages |
| M | **WhatsApp outbox:** `NotificationOutbox` (employee, channel=whatsapp, event, message, status=queued); entries created on run approval (per employee), leave approved/rejected, and document-expiry reminders queued from the compliance page; admin page `/admin/outbox`; `WHATSAPP_ENABLED` config flag (default **off** — live sending activates when the WhatsApp Business connection is configured). `User.phone` added and editable in Employee Central | §2.1(8) Notifications | `/admin/outbox` |
| N | **Demo seed:** extended — companies, components + assignments (BASIC/HRA/TRANSPORT) for all 20 fake users, one active loan (EMP004), 3 verified docs for EMP001 + 1 pending for EMP002 (minimal valid PDFs generated in code). **Exactly 20 users; no payroll run pre-created** | §9 V1 demo credibility | — |

Carried over from V2.1: auth + roles, dashboard tiles, Employee Central 360,
leave (UAE types), attendance + regularisation, grievances, evaluations,
approvals, recruitment, onboarding, compliance, gratuity calculator, audit
trail, 20-user demo seed.

## 2. WPS / LuLu Exchange caveat (important)

The company's WPS processor is **LuLu Exchange**, but the exact SIF
fixed-width specification has not been obtained yet. The V3 WPS export is a
**working CSV per visa company** — one file per company (`wps-<companycode>-<YYYYMM>.csv`)
from one unified payroll run — clearly labelled in the UI and in the file
context as *pending LuLu's exact SIF format*.

**Do not submit the CSV to the WPS agent as-is.** When LuLu provides the SIF
layout, the payroll team maps: header record, employee detail records,
salary control record; confirm SIF covers regular monthly wages only
(bonuses/commissions/gratuity processed separately) and that SIF figures
match MOHRE-registered contract salary exactly.

## 3. Configuration

| Setting | Where | Default |
|---------|-------|---------|
| Secret key | `ESS_SECRET_KEY` env | `dev-only-change-me-immediately` (change in production) |
| Database | `ESS_DATABASE_URL` env | `sqlite:///<project>/ess.db` |
| Demo seed | `ESS_SEED_DEMO=1` env | off — set manually in the Render dashboard (Environment tab) for the sample deploy; **never set on production** |
| WhatsApp live sending | `ESS_WHATSAPP_ENABLED=1` env (`WHATSAPP_ENABLED`) | **off** (outbox queues only) |
| HTTPS cookies | `ESS_COOKIE_SECURE=1` env | off (set on for production) |
| Photo upload | `app/files.py` / `config.PHOTO_MAX_MB` | JPG/PNG, **2 MB**, extension + magic-byte check |
| Document upload | `app/files.py` / `config.DOCUMENT_MAX_MB` | PDF/JPG/PNG, **10 MB**, extension + magic-byte check |
| Upload storage | `<instance>/uploads/photos`, `<instance>/uploads/documents` | created on boot; **never under `static/`**; served only through authenticated routes |
| Rate limiting | `flask_limiter` | on (login + sensitive endpoints) |
| Session lifetime | `PERMANENT_SESSION_LIFETIME` | 8 hours |

Migrations: `db.create_all()` runs on boot plus an idempotent
`ALTER TABLE users ADD COLUMN …` step for columns added after the original
schema — existing DBs upgrade safely.

## 4. Test instructions

```bash
cd ~/workspace/hrms-ess-v3
for s in e2e v2 v21 demo_data v3; do
  rm -f /tmp/v3test-$s.db
  ESS_SECRET_KEY=testsecret12345 ESS_DATABASE_URL=sqlite:////tmp/v3test-$s.db \
    ESS_SEED_DEMO=1 ~/workspace/ess-app/venv/bin/python test_$s.py
done
```

- Every suite uses an **isolated temp SQLite DB** (never the real database)
  and fake fixtures only.
- `test_e2e.py` mutates its seeded DB (password change, lockouts), so each
  run needs a **fresh** DB file — that is why the loop deletes first.
- Expected results (2026-10-18 full run): see §6.

**test_v3.py coverage** (per feature): component CRUD + delete guard;
`can_view_salary` enforcement (manager without flag → 403 on payroll, no
salary figures in Employee Central HTML; with flag → allowed); OT resolver
precedence (individual > group > company > default) + OT amount math on basic
(hourly = basic/30/8); payroll fixture (basic 3000 + HRA 1000, 2 LOP days in a
30-day month → fixed prorated on payable/30); loan 3000/500 recovered and
balance reduces only after lock; positive arrears on payslip with original
period; negative net flagged in exceptions; SoD (creator cannot approve own
run); lock blocks recalculation; WPS split (two companies → separate CSVs
with correct per-company totals and employees); photo upload (valid PNG
accepted, `.exe` rejected, other employee's photo denied); document
upload → pending → verify → HR download; manager download 403; other
employee's doc 403; expiry list includes seeded doc; letter mail-merge of
name/designation; salary template blocked without salary permission;
WhatsApp outbox entry created on run approval.

## 5. Deploy / run

- Sample hosting: `render.yaml` (service `hrms-ess-v3-demo`), free tier,
  fake demo data only.
- Production: AWS deployment is separate — see `DEPLOY-AWS.md` (from V2.1)
  and `DATA-REQUIRED.md` for the data list to supply.

## 6. Test results — full clean run 2026-10-18

| Suite | Result |
|-------|--------|
| `test_e2e.py` | **32 / 32 PASS** |
| `test_v2.py` | **33 / 33 PASS** |
| `test_v21.py` | **45 / 45 PASS** |
| `test_demo_data.py` | **54 / 54 PASS** (20 users, all expectations intact) |
| `test_v3.py` | **49 / 49 PASS** |
| **Total** | **213 / 213 PASS** |

(Exact per-suite totals recorded in the build report; no test was weakened.)

## 7. Blueprint items deferred to later phases (explicit)

Per the research blueprint §9 phase plan, the following are **not** in this
core build:

1. **Exact LuLu SIF format** — pending the spec from LuLu Exchange (§2 above).
2. **Arabic/English bilingual** payslips, letters, RTL UI (§4.6) — user
   decided **English only**.
3. **Final settlement / end-of-service run** with full Article 51 gratuity
   engine (§3.3(9), §4.3) — gratuity *calculator* page carried over from V2.1;
   the settlement run type, unpaid-absence exclusion, cap handling, and
   settlement statement with e-sign are later.
4. **Multi-level payroll approval** (payroll officer → HR manager → finance →
   signatory) — single-level approval + SoD (creator ≠ approver) implemented;
   full chain and maker-checker on bank-detail changes deferred.
5. **Off-cycle runs** (bonus run, correction run) and **payroll
   journal/accounting export** (§3.3(7),(10)).
6. **Automatic arrears** from backdated salary revisions; effective-dated
   revision history (§3.2) — arrears supported as payroll inputs; auto
   generation later.
7. **OT caps** (≤2 h/day, ≤144 h/3 weeks) and day-off-in-lieu (§3.2, §4.1) —
   rate multipliers implemented; cap enforcement deferred.
8. **Document OCR / expiry extraction, virus scanning, watermarked /
   time-bound downloads, retention policies & legal hold** (§5.3–5.5) —
   magic-byte validation, private storage, versioning, and download audit
   implemented; OCR/AV deferred.
9. **Photo EXIF stripping, consent records, consent-withdrawal workflow**
   (§6) — role-scoped private storage implemented.
10. **MFA / SSO / SCIM** and step-up auth for payroll approval (§8).
11. **Biometric device connector** (ADMS push / SDK pull) — attendance CSV
    import from V2.1 continues; live device connector deferred (§2.2(13)).
12. **LLM-powered features** (§7(b): HR copilot, NL reporting, letter
    drafting) — deferred pending explicit privacy/security design and PDPL
    consent handling; §7(c) do-not-automate rules respected throughout.
13. **Expenses, assets, travel, helpdesk, learning, compensation bands**
    (§2.4–2.5) — recruitment, onboarding, evaluations carried over from V2.1;
    deeper talent modules later.
14. **Payroll parallel-run tooling** and pen-test readiness (§8) — process
    gates for go-live.

---

*Built 2026-10-18 for Al-Ameen Industries LLC. UAE statutory figures in this
system (leave entitlements, OT multipliers, WPS rules) are configurable —
confirm current MOHRE executive regulations with counsel before production
use.*
