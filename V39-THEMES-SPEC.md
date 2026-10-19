# V3.9 — Theme options + Liquid Glass materials + interactive dashboard (user refs, 2026-10-19)

User's two references (in design-refs/): (1) nixtio-dashboard.jpg — soft-blue panel
dashboard (built as V3.8); (2) liquid-glass-kit.jpg — a "Liquid Glass" UI kit: frosted
translucent pill buttons with glossy top highlight + soft pastel glows (mint #cfeee6,
lavender #cfc4f5/violet, peach #ffd9c9, sky #cfe4f7), glass search/text fields, glass
dropdown, violet toggle switch, glass modal, glass toast pill, round glass icon buttons.
User asks: (a) theme CHANGE OPTIONS in the software, (b) interactive dashboards in the
theme style, (c) ALL UI materials (buttons, fields, toggles, modals, toasts, cards)
based on the uploaded references. Builds ON TOP of V3.8 (tokens/panel/dashboard).

## 1. Theme system
- Token architecture: all colours/surfaces/shadows/radii live in CSS variables on
  :root[data-theme="<key>"]. Every component (buttons, inputs, selects, toggles, pills,
  cards, tables, modals, toasts/flashes, drawer, top bar, bottom nav, dashboard widgets,
  SVG ring/bars via currentColor/CSS vars) consumes variables ONLY — no hard-coded
  theme colours outside the token blocks.
- Themes (4):
  1. nixtio (default) — the V3.8 soft-blue panel theme.
  2. glass — Liquid Glass: page bg soft neutral gradient with very subtle pastel blobs;
     surfaces = translucent white (rgba(255,255,255,.55–.7)) + backdrop-filter
     blur(14px) saturate(1.4) + 1px rgba(255,255,255,.65) border + inner top highlight
     (inset box-shadow) + soft coloured outer glows on primary actions; primary button =
     lavender/violet glass pill; secondary = clear glass pill; toggles violet when on;
     inputs glass with blur; modals/toasts glass. Fallback: if backdrop-filter is
     unsupported or prefers-reduced-transparency, surfaces fall back to near-opaque
     white (media query + @supports not). Performance: backdrop blur ONLY on top bar,
     cards, modals, toasts, drawer — not on table rows or long lists.
  3. indigo — Slate & Indigo Pro (tokens from the earlier theme: primary #4f46e5,
     slate chrome) re-expressed in the token system.
  4. night — dark slate ("Timeless Night"): absorb the existing ess_theme dark cookie
     behaviour into this key.
- Picker UI: a palette icon button in the top bar opens a small panel/popover with 4
  theme cards (mini preview swatches + name), the active one ticked; choosing applies
  INSTANTLY (no reload — set data-theme, persist). Also listed in the avatar menu and
  on the login page (small text toggle row) if cheap.
- Persistence: cookie ess_ui_theme (1 year) + server-rendered data-theme on <html> from
  the cookie (no flash of wrong theme); if the user is logged in, also save to a
  User.ui_theme column and prefer it after login. Migrate the old ess_theme=dark cookie
  → night on first read.
- Scope of "all UI materials": primary/secondary/danger buttons, icon buttons, inputs,
  selects, textareas, checkboxes/toggles, status pills, badges, cards, tables (header
  tint), tabs, accordions, drawer, modals/dialogs, flash/toast messages, bottom nav,
  Awesome Bar, notifications panel, login card. Each must look correct in ALL 4 themes
  (screenshot matrix: dashboard + employees list + a form + a modal-ish page × glass
  + night at minimum).

## 2. Interactive dashboard (in each theme)
V3.8 dashboard becomes fully token-driven (ring stroke, bar fills, dark tasks card
becomes theme's "accent surface" — in glass theme it is deep-violet glass). Interactions:
- Theme switch animates colours (transition on background/color/box-shadow .35s).
- KPI counters count up (existing), bars animate width on load, ring animates stroke.
- Cards: hover raise; tasks checklist rows tick with a small pop animation when an
  item is completed in-session (PJAX-safe, cosmetic only).
- Profile/accordion/calendar inherit tokens. No new data — V3.8 queries stay.

## 3. Tests (tests/test_v39.py, ESS_DATABASE_URL=sqlite:////tmp/test_v39.db)
data-theme rendered from cookie (each of 4 keys), default nixtio when unset; picker
panel markup + 4 options present on dashboard; choosing persists (cookie set via the
picker's endpoint/JS contract — test the server side: GET with cookie renders theme;
POST/preference save when logged in updates User.ui_theme); old ess_theme=dark maps to
night; style.css contains token blocks for all 4 themes + @supports fallback for glass
+ reduced-transparency media query; glass theme has backdrop-filter declarations scoped
to the allowed components; dashboard HTML in glass mode still contains ring/bars/tasks
(unchanged data); login page renders with theme from cookie.
Full suite green (baseline 1069 + migrate 38 + v38's new tests). Legacy edits only for
legitimate markup changes (e.g. theme attribute), documented in README-V39.md.
Screenshots: dashboard in glass + nixtio + night (PC), glass mobile, employees list in
glass, one form in glass → v39-*.png. Rebuild zip; README-V39.md. NO deploy.
