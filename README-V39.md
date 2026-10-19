# README-V39 — Theme options + Liquid Glass (v3.9, 2026-10-19)

Stream C report: tests, screenshots, packaging. Streams A (CSS tokens) and B
(shell/server/JS) were done and smoke-verified before this stream.
A user follow-up (same day) added theme selection to My Profile and
HR Settings — see §7.

## 1. Theme system overview

All theme colours/surfaces/shadows/radii live in CSS variables on
`:root[data-theme="<key>"]` in `app/static/style.css` (`?v=11`). Every
component consumes variables only — no hard-coded theme colours outside the
token blocks. The server renders `<html data-theme="…">` in `base.html`
(no flash of wrong theme); the picker applies changes instantly via JS
(`desk.js?v=4`, no reload).

### The 4 themes

| Key      | Look |
|----------|------|
| `nixtio` | Default. The V3.8 soft-blue panel theme, tokens verbatim (`:root, :root[data-theme="nixtio"]`). |
| `glass`  | Liquid Glass: soft neutral page gradient with pastel blobs (mint `#cfeee6`, lavender `#cfc4f5`, peach `#ffd9c9`, sky `#cfe4f7`); frosted translucent surfaces (`--glass-bg` rgba(255,255,255,.55–.7)) + `backdrop-filter: blur(14px) saturate(1.4)` + 1px rgba(255,255,255,.65) border + inset top highlight + soft violet glows on primary actions; primary button = lavender/violet glass pill; toggles violet when on. |
| `indigo` | Slate & Indigo Pro: primary `#4f46e5` / hover `#4338ca`, slate chrome, panel `#f1f5f9`. |
| `night`  | Dark slate ("Timeless Night"); absorbs the old `ess_theme=dark` cookie behaviour. The old `html[data-theme="dark"]` CSS block was removed. |

### Picker + persistence

- Palette button `#themePickerBtn` in the top bar (next to the search button)
  opens `#themePopover` (`role=dialog`) with 4 `.theme-card` options
  (`data-theme-key`), each with mini swatches + label; the active card is
  ticked. Also included on the login page (floating) and the avatar-menu
  "Theme" item (kept its `id="themeToggle"` hook) now opens the popover.
- **Follow-up:** theme selection also lives in **My Profile** ("Theme /
  Appearance" section, every logged-in user) and **HR Settings**
  ("Appearance" section, admin only — admin's own preference, with a
  per-user note). Both reuse the new `_theme_cards.html` partial (long
  display names: Nixtio Blue, Liquid Glass, Slate & Indigo, Timeless Night)
  inside a `[data-theme-cards]` wrapper; see §7.
- Choosing a theme: sets `document.documentElement[data-theme]`, writes the
  `ess_ui_theme` cookie (1 year, `SameSite=Lax`), and — when logged in
  (`body[data-authed="1"]`) — POSTs JSON to `/api/ui-theme` with the
  `X-CSRFToken` header.
- Server resolution order (`base.html`): 1) logged-in `User.ui_theme`
  (nullable `VARCHAR(16)`, added via the existing `add_column` migration in
  `app/__init__.py`), 2) `ess_ui_theme` cookie, 3) legacy `ess_theme=dark`
  cookie → `night`, 4) `nixtio`.
- `POST /api/ui-theme` (`@login_required`, in `app/desk.py`): valid key →
  200 `{"ok": true, "theme": key}` and persists `User.ui_theme`; invalid key
  → 400; unauthenticated → 302 to login. Cosmetic preference, unaudited
  (consistent with other per-user prefs).
- JS (`desk.js`) migrates a legacy `ess_theme=dark` cookie to
  `ess_ui_theme=night` on first load and clears the old cookie; also maps
  any stray `data-theme="dark"` markup to `night`.

### Glass fallbacks

- `@supports not (backdrop-filter: blur(1px))` → surfaces fall back to
  near-opaque white (`--glass-bg: rgba(255,255,255,.94)`, glows neutralised).
- `@media (prefers-reduced-transparency: reduce)` → same fallback.
- Backdrop blur is scoped to exactly: `.topbar .card .flash .toast .awesome
  .slide-panel .drawer .modal .avatar-menu .theme-popover #themePopover`
  (glass theme only) — never table rows, lists, or inputs (perf contract).
- `.35s` colour transitions on theme switch; `prefers-reduced-motion`
  kill-switch retained.

## 2. Tests

New: `tests/test_v39.py` — **66 checks, all passing**.

Run: `rm -f /tmp/test_v39.db && ESS_SECRET_KEY=testsecret12345
ESS_DATABASE_URL=sqlite:////tmp/test_v39.db
~/workspace/ess-app/venv/bin/python tests/test_v39.py`

Coverage: `data-theme` rendered from the `ess_ui_theme` cookie for each of
the 4 keys; `nixtio` default when unset/invalid; picker markup
(`#themePickerBtn`, `#themePopover`, exactly 4 `.theme-card` with
`data-theme-key`, active card ticked); logged-in `POST /api/ui-theme`
(valid → 200 + JSON + `User.ui_theme` persisted, verified with a fresh DB
read; invalid → 400; anonymous → 302); saved `ui_theme` beats the cookie on
next login; legacy `ess_theme=dark` → `night`; `style.css` carries all four
`:root[data-theme="…"]` token blocks, the `@supports not` glass fallback,
the `prefers-reduced-transparency` query, backdrop-filter scoped to the
allowed components (and a negative check that no `tr/td/input/select/
textarea` selector carries it), no leftover `html[data-theme="dark"]` block;
glass dashboard still renders ring/bars/tasks with data identical to the
nixtio render (manager tasks `0%` + row, employee KPI `20%` bar and
`24`-day balance in both); login page renders the themed `<html>` from the
cookie and includes the picker.
Follow-up checks (§7): employee's My Profile page renders the Theme /
Appearance section with 8 theme cards total (4 popover + 4 section) and the
4 long display names; POSTing `night` marks it active in both placements
and persists `User.ui_theme`; admin's `/hrms/settings` renders the
Appearance section with the per-user note (8 cards); employees get 403 on
`/hrms/settings`.

Full suite (final run): every `test_v*.py` at root + `tests/test_migrate.py`
+ `tests/test_v38.py` + `tests/test_v39.py`, each with its documented
`ESS_DATABASE_URL` pattern, fresh DBs (except `test_e2e.py`, which runs
against the repo `ess.db` per its design — it reads the staff master xlsx
for the DOB password), `ESS_SECRET_KEY=testsecret12345`, venv python.

Per-file tallies (final):

| File | Passed | Failed |
|------|--------|--------|
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
| test_v37.py | 72 | 0 |
| test_e2e.py | 32 | 0 |
| test_demo_data.py | 54 | 0 |
| tests/test_migrate.py | 38 | 0 |
| tests/test_v38.py | 76 | 0 |
| tests/test_v39.py | 66 | 0 |
| **TOTAL** | **1255** | **0** |

(Baseline was 1069 + migrate 38 + v38's 76 = 1183. The increase comes from
the new `tests/test_v39.py` (66 checks) plus small count drift in the root
suites versus the earlier baseline tally — e.g. the v36/v37 asset-version
and theme-migration checks. Every file is green.)

## 3. Legacy test edits (with reasons)

Only markup that legitimately changed in V3.9 was touched:

1. `test_v36.py` — "dark theme persisted server-side": `ess_theme=dark`
   cookie now renders `data-theme="night"` (contract migration; the old
   `data-theme="dark"` block was removed). Check updated to assert `night`,
   with a comment explaining the migration. Docstring item 7 updated.
2. `test_v36.py` — "pages link the bumped stylesheet v=10" → `v=11`
   (stream A bumped the stylesheet query string; documented in the task
   brief as `style.css?v=11`).
3. `test_v37.py` — "pages link the bumped stylesheet v=10" → `v=11`
   (same reason).
4. `test_v37.py` — "pages link desk.js v=3" → `v=4` (stream B bumped the
   picker JS; documented as `desk.js?v=4`). Docstring asset-version line
   updated.
5. `test_v37.py` — "pages link desk.js v=4" → `v=5` (follow-up: desk.js was
   generalised so one wiring function serves the popover AND every static
   `[data-theme-cards]` section; version bumped to bust caches). Docstring
   updated.

No other legacy file referenced the old theme toggle, `ess_theme`, or
`data-theme` values.

## 4. Screenshots

Captured with Playwright 1.64 (Chromium headless) against a local
demo-seeded instance (`seed_demo.py`; MGR001 for dashboards, HRADMIN for
HR pages), into `~/workspace/your_files/hrms-theme-shots/`:

- `v39-dashboard-glass-pc.png` — manager dashboard in glass, **picker
  popover open** (4 cards, "Glass" ticked) — confirms openable/visible.
- `v39-dashboard-nixtio-pc.png`, `v39-dashboard-night-pc.png`
- `v39-dashboard-glass-mobile.png` (390×844)
- `v39-employees-glass.png` — Employee Central list in glass
- `v39-form-glass.png` — Add Employee tabbed form in glass
- `v39-profile-theme-glass.png` — My Profile "Theme / Appearance" section
  in glass (follow-up; 4 cards with swatches, active one ticked)

### Contrast fixes applied (stream C)

1. **Tasks card washed out in glass** (found by eyeballing): the "My tasks"
   dark card rendered near-white with white text (unreadable).
   Root cause: the card carries both `card` and `tasks-dark` classes; the
   glass rule `:root[data-theme="glass"] .card` (sets
   `background: var(--glass-bg)`, near-white) and
   `:root[data-theme="glass"] .tasks-dark` (set only border + shadow, no
   background) have equal specificity, so the card picked up the near-white
   glass background while its text stayed `--tasks-fg: #fff`.
   Fix (CSS only, in the glass token block): added
   `background: var(--tasks-bg)` (deep violet `rgba(109,40,217,.8)`, the
   spec's "deep-violet glass" accent surface) to the
   `:root[data-theme="glass"] .tasks-dark` rule, with an explanatory
   comment. Re-shot: card is now deep violet with crisp white text.
2. **Theme-card swatches invisible** (found by eyeballing the new profile
   section): stream A's CSS styles `.theme-swatches span`, but the card
   markup used `<i>` elements — empty inline `<i>`s have no size, so the
   mini preview swatches never rendered. Fix: changed the swatch elements
   to `<span>` in `_theme_cards.html` (one line). Re-shot: swatches render
   in the popover and in the profile/settings sections.

## 5. Packaging

Rebuilt `~/workspace/your_files/hrms-ess-v3.zip` AFTER all changes (tests,
CSS contrast fix, re-shot screenshots, this README):
1. Deleted all `__pycache__` directories first.
2. Zipped the v3 root excluding `ess.db`, `instance/*`, and `*.pyc`.
3. Verified the listing: no `ess.db` / `instance/` / `.pyc` entries; includes
   `tests/test_v39.py` and `README-V39.md`. Exact size and file count were
   reported with the stream-C handoff.

## 6. Deferred / uncertain items

- **`.modal` / `.toast` class coverage (noted by stream A):** the glass
  frosted treatment for `.modal`/`.toast` exists in the stylesheet (scoped
  blur list), but no template currently renders `class="modal"` or
  `class="toast"` markup, so the glass modal/toast look is unverified in a
  screenshot. Deferred until a modal/toast surface exists in the UI.
- **`.btn-dark` hard-coded colours:** `app/templates/_frappe_css.html`
  styles `.btn-dark` (the "+ Add Employee" / "Export selected" / "Create
  Employee" buttons) with hard-coded `#171717`/`#2b2b2b` — outside the
  token blocks. It is readable in all 4 themes (white on near-black) and
  renders identically everywhere, so it was left as-is; tokenising it
  would change the design in all themes and is a stream-A design decision.
- **Avatar-menu "Theme" item:** kept its `id="themeToggle"` hook and now
  opens the picker popover (per contract); the id name is legacy but the
  behaviour is correct.
- **Unauthenticated `POST /api/ui-theme`:** returns 302 (Flask-Login
  redirect), not 401 — "blocked" either way; test accepts both.
- Screenshots are of demo-seeded data on a local instance; no deploy was
  done (per instructions). The live Render demo still runs pre-V3.9 code.

## 7. Follow-up: theme options in Settings (user request, same day)

The top-bar palette popover stays the quick switch; theme selection was
also added in two more places, sharing one persistence mechanism.

**My Profile** (`views.profile`, `profile.html`, every logged-in user): a
"Theme / Appearance" section with the 4 theme cards (long display names:
Nixtio Blue, Liquid Glass, Slate & Indigo, Timeless Night). The active
theme is server-rendered (`.active` + tick) from the resolved theme, like
the popover.

**HR Settings** (`/hrms/settings`, `settings.page`, admin only): an
"Appearance" section with the same 4-option selector for the admin's OWN
preference, plus a note that the theme is per-user. Placed OUTSIDE the
settings POST form — it persists via the cookie + `/api/ui-theme`, not via
HR Settings (which stays audited and unchanged).

**Implementation** (no duplicated logic):
- New `app/templates/_theme_cards.html` partial: just the 4 `.theme-card`
  divs, driven by an optional `theme_options` list (key, label, 3 swatch
  colours); defaults to the popover's short labels. `_theme_picker.html`
  now includes it; profile/settings set the long-name list before
  including it.
- `desk.js`: the picker JS was generalised — `wireThemeCards(container,
  afterChoose)` attaches click/keyboard → `saveTheme()` (cookie +
  `POST /api/ui-theme` when logged in) to every `[data-theme-cards]`
  wrapper on the page; the popover additionally closes after a choice.
  `applyTheme()` already updated ALL `.theme-card` elements page-wide, so
  popover + sections stay in sync with no reload. `desk.js` bumped to
  `?v=5`.
- CSS: reused the existing `.theme-grid`; added a `cols-4` variant
  (4-across ≥720px) for the wider static sections.

**Tests:** 16 new checks in `tests/test_v39.py` (§10) — profile section
renders with 8 cards total and the 4 long names; POSTing `night` marks it
active in both placements and persists `User.ui_theme`; admin settings
renders the Appearance section + per-user note (8 cards); employees get
403 on `/hrms/settings`.

