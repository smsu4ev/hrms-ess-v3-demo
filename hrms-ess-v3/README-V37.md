# V3.7 — "App UI": simple + animated interface (final pass)

User verdict on the V3.6 Frappe desk clone: "everything is complicated — use
another model, simple and animated."

## Model

The mobile super-app / fintech pattern (greeting home, big task tiles,
bottom tabs) adapted to desktop. The Frappe rail + workspace sidebar are
REMOVED. Every existing route/feature stays; this is a shell + home rework
only. Full design spec: `V37-APP-UI-SPEC.md`.

## Shell mapping (old → new)

| V3.6 | V3.7 |
|---|---|
| Rail with workspace icons (role-filtered) | Slide-in navigation drawer (same `workspaces.py` registry, grouped collapsible sections, scrim, Esc/scrim/link-click close, current-page group auto-expands) + desktop text links in the top bar: Home · All Services · My Team (managers/admins only) |
| Workspace sidebar | Drawer (replaces it; shows the current workspace's link groups) |
| Mobile bottom tab bar | Unchanged: Home, Punch, Leave, Payslips, More → drawer. Base rule `.bottomnav { display: none; }` kept BEFORE the `@media (max-width: 860px)` block that enables it (V3.6.2 regression guard, re-verified) |
| Awesome Bar (`/api/awesome`), notifications slide-over | Unchanged |
| Avatar area | Avatar dropdown: My Profile / Theme / HR Settings (admin/HR) / Logout (POST) |

Home (`/dashboard`): greeting header ("Good morning/afternoon/evening,
\<first name>" + date + department), TODAY hero card (employee: shift +
punch status + Check In/Out with pulse ring; manager: present/absent/on-leave
with count-up; admin/HR: pending approvals, visa expiries 20d, open tickets),
role-filtered QUICK TILES (`.tile-grid`, soft-tinted `.t-chip` icon chips,
live badges), My Tasks card, Celebrations strip, latest-notice card,
All Services (`/apps`): same tiles grouped by area with headings.
Animation system: `tile-rise` stagger, `page-enter` on PJAX, `[data-count]`
count-up, drawer/panel transitions, PJAX shimmer; all disabled under
`@media (prefers-reduced-motion: reduce)`.

## Final-pass code fixes (this task)

1. **Tile chip class mismatch (screenshot-found):** `dashboard.html`,
   `apps.html` and `workspaces/home.html` emitted `t-ico tint-*` classes that
   do not exist; the CSS defines the complete `.t-chip.<color>` system (6
   soft tints + dark-theme variants). Templates aligned to
   `t-chip <color>` (4 small edits). Pinned by a new `test_v37.py`
   assertion (`class="t-chip "` renders on home tiles). No layout redesign.
2. No other code changes — the shell, home, `/apps` and `/ws/<key>` pages
   were already built per spec; tests confirmed them green.

## Test tally (final full-suite run, fresh DB per file)

| File | Passed | Failed |
|---|---|---|
| test_e2e.py | 32 | 0 |
| test_v2.py | 33 | 0 |
| test_v21.py | 48 | 0 |
| test_v3.py | 83 | 0 |
| test_v31.py | 102 | 0 |
| test_v311.py | 72 | 0 |
| test_v32.py | 71 | 0 |
| test_v321.py | 46 | 0 |
| test_v33.py | 107 | 0 |
| test_v331.py | 71 | 0 |
| test_v34.py | 73 | 0 |
| test_v341.py | 67 | 0 |
| test_v35.py | 39 | 0 |
| test_v36.py | 83 | 0 |
| test_v361.py | 22 | 0 |
| test_demo_data.py (ESS_SEED_DEMO=1) | 54 | 0 |
| test_v37.py (new) | 66 | 0 |
| **Total** | **1069** | **0** |

New `tests`: `test_v37.py` (66 checks) — shell has no `.rail`/`.ws-sidebar`;
top bar (hamburger, search opener, bell, avatar menu, desktop text links,
My Team for manager+ only); drawer renders role-filtered groups (admin sees
HR Settings link, employee does not); home tiles per role (employee:
Check In/Out + Apply Leave, no Payroll; admin: Payroll; manager: Approvals
tile with pending badge > 0 in the fixture); `data-count` count-up on
manager/admin hero; `@keyframes tile-rise`, `.pjax-enter`,
`prefers-reduced-motion` in style.css; `.bottomnav` base `display:none`
before the ≤860px media query; `/apps` grouped sections; all 11
`/ws/<key>` homes 200 for admin; `?v=` asset versions; tinted chip class.

## Legacy test edits (each minimal, intent preserved)

1. `test_v36.py` §1 (rail assertions, ~lines 109–140): rewrote as
   drawer/topbar equivalents — drawer renders with `d-group`s; topbar shows
   Home + All Services (+ My Team for manager/admin); on `/ws/hr-setup` the
   drawer shows the current workspace groups (Masters, Settings & Audit) with
   the HR Settings link; employee drawer hides admin workspaces/HR Settings
   and the topbar hides My Team; manager topbar keeps My Team and the drawer
   hides the Payroll workspace. Reason: the rail + workspace sidebar no
   longer exist; the same `workspaces.py` registry now feeds the drawer.
2. `test_v36.py`: `style.css?v=7` → `style.css?v=8`. Reason: v3.7 bumped the
   stylesheet (immutable caching per spec).
3. `test_v21.py:208`: `b'class="tiles"'` → `b'class="tile-grid"'`. Reason:
   the v3.7 home quick-access grid is the `.tile-grid` component; `.tiles`
   survives only as a CSS grid alias, never in rendered markup.
4. `test_v331.py:509`: `b"Department today"` → `b"Present today"` +
   `b"data-count"`. Reason: the old dept card is now the manager TODAY hero
   with animated count-up team numbers.
5. `test_v33.py:298`: `b"Action needed"` → `b"Mandatory read"`. Reason: the
   home "Latest notice" card badges mandatory reads with "Mandatory read".
6. `test_demo_data.py:139–146`: `"Welcome, <full name>"` → greeting regex
   `Good (morning|afternoon|evening), <first name>`. Reason: v3.7 greeting
   header model.
7. `test_e2e.py:71`: `b"Welcome"` → `b"Good "` (+200 check). Reason: same
   greeting change on the post-password-change landing page.

All other suites passed UNCHANGED (awesome bar, settings, workspace homes,
theme, doodle retirement, and every v2–v3.6.1 feature suite).

## ?v= asset versions

`style.css?v=8`, `app.js?v=4`, `desk.js?v=2` (bumped with the v3.7 changes;
immutable caching).

## Screenshots (Playwright, local demo seed)

`~/workspace/your_files/hrms-theme-shots/`:
- `v37-home-admin.png` — HRADMIN home, desktop 1920px (greeting, hero stats
  3/2/1, 20 tiles incl. Payroll/HR Settings/Approval Matrix, badges)
- `v37-home-employee.png` — EMP001 home, desktop (shift hero, 10 tiles, My
  Tasks, Celebrations, Latest notice, leave balances)
- `v37-home-mobile.png` — EMP001 home, 390px (bottom tab bar visible:
  Home/Leave/Attend./Payslips/Profile)
- `v37-drawer-open.png` — drawer open over home (scrim, collapsible groups)
- `v37-all-services.png` — All Services directory, all 11 areas grouped
- `v37-ws-section.png` — `/ws/hr-setup` section page (app-style header,
  checklist, live counts)

Eyeball results: no rail/sidebar leftovers; drawer overlaps correctly;
bottomnav absent on all desktop shots, present as a proper bottom tab bar on
mobile. Minor cosmetic note: on narrow mobile tiles long labels wrap
("My / Documents") — readable, left as-is.

## Deferred / uncertain

- **test_e2e.py has no documented ESS_DATABASE_URL** and needs the real
  staff record AA670 (DOB password read from the real staff master Excel);
  it crashes at module level on a fresh DB — pre-existing, not v3.7-caused.
  This pass ran it against a throwaway copy of the live `ess.db`
  (`/tmp/test_e2e.db`, mutations land on the copy only): 32/32 green.
- **Playwright** was pip-installed into `~/workspace/ess-app/venv`
  (playwright 1.64.0) to drive the screenshots, using the pre-cached
  `chromium-1248` binary via `executable_path`. No app code there was
  touched; disclosed because the task said to leave that tree alone.
- The drawer on `/` shows the shift-attendance workspace groups (pre-existing
  first-listing-wins endpoint→workspace mapping for `views.dashboard`);
  out of scope for this pass — not changed.
- **NO deploy** performed (parent handles it).

## Post-build parent fix (2026-10-19)
- **Hero stats layout (screenshot-found):** the dashboard TODAY card shipped without
  `.hero-row`/`.hero-stats` CSS rules, so the manager/admin count-ups stacked
  vertically in a tall empty card. Fixed in `style.css`: `.hero-row` (flex,
  space-between) + `.hero-stats` (flex row with gap, top divider) + `.hstat`
  typography. Re-shot `v37-home-admin.png` to confirm the side-by-side layout.
- Asset bump: `style.css?v=8` → `?v=9` in `base.html` (immutable caching).
- Test updates: `test_v37.py` ("style.css?v=8" → "?v=9") and `test_v36.py` §9
  same bump. Re-runs by the parent: test_v36 83/83, test_v37 66/66.
