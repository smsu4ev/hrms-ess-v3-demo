# V3.9 integration contract (A=CSS, B=shell/server/JS, C=tests/screenshots/package)

Theme keys (exact): `nixtio` (default; = V3.8 look), `glass`, `indigo`, `night`.
CSS selector: `:root[data-theme="<key>"]`. Remove the old `html[data-theme="dark"]` block
(absorbed into `night`); default selector `:root, :root[data-theme="nixtio"]`.

## Core token list — EVERY block must define ALL of these (no hard-coded colours elsewhere):
--bg, --panel, --navy, --navy-2, --blue, --blue2, --chip, --card, --surface-2, --border,
--border-soft, --text, --body, --muted, --primary, --primary-hover, --on-primary,
--secondary, --secondary-hover, --accent, --accent-soft, --link, --success, --success-bg,
--warning, --warning-bg, --danger, --danger-bg, --info, --info-bg, --gray-bg, --gray-text,
--radius, --radius-sm, --radius-lg, --shadow, --shadow-soft, --shadow-lift,
--panel-shadow, --cream.
Glass extras (glass block only): --glass-bg (rgba(255,255,255,.55-.7)), --glass-border
(1px rgba(255,255,255,.65)), --glass-hi (inset top highlight shadow), --glass-glow
(soft coloured outer shadow), --glass-blur (14px), --glass-sat (1.4).
Glass fallback: `@supports not (backdrop-filter: blur(1px))` sets --glass-bg to
rgba(255,255,255,.94) and neutralises glows; `@media (prefers-reduced-transparency: reduce)`
does the same.

Theme values:
- nixtio: current V3.8 tokens verbatim.
- indigo: Slate & Indigo Pro — primary #4f46e5, primary-hover #4338ca, panel #f1f5f9,
  card #ffffff, text #0f172a, body #334155, muted #64748b, border #e2e8f0, navy #312e81,
  navy-2 #4338ca, blue #4f46e5, blue2 #818cf8, chip #eef2ff, accent #4f46e5,
  accent-soft #eef2ff, link #1e1b4b, on-primary #fff; bg gradient slate-50→indigo-50 tint.
- night: current dark block values verbatim (navy #d9e8fb etc.).
- glass: page bg soft neutral gradient with pastel blobs (layered radial-gradients:
  mint #cfeee6, lavender #cfc4f5, peach #ffd9c9, sky #cfe4f7 at low alpha on #f4f6fa);
  surfaces var(--glass-bg) + backdrop-filter blur(var(--glass-blur)) saturate(var(--glass-sat));
  primary = lavender/violet glass pill (#b9a8ef bg gradient → #a78bfa, text #2e1065);
  toggles violet on; text stays dark slate (#1e1b4b / #312e81 headings).

Backdrop blur ONLY on: .topbar, .card, .flash/.toast, .awesome, .slide-panel,
.drawer, .modal, .avatar-menu, .theme-popover. NEVER on table rows, lists, inputs.

## Resolution order (server, in base.html <html data-theme>):
1. logged in AND current_user.ui_theme in keys → that
2. request.cookies["ess_ui_theme"] in keys → that
3. request.cookies.get("ess_theme") == "dark" → "night"
4. "nixtio"
JS on load: if ess_ui_theme missing but ess_theme==dark → set ess_ui_theme=night
(1yr) and delete the old cookie. Also migrate any data-theme="dark" markup to "night".

## Picker (top bar + avatar menu + login page if cheap)
- Button `#themePickerBtn` (palette icon, aria-haspopup) in the top bar next to the
  awesome/search button. Popover `#themePopover` (role=dialog) with 4 `.theme-card`
  elements, each `data-theme-key`, containing mini swatch spans + label; active card
  gets `.active` + a tick.
- JS: click card → html[data-theme]=key; document.cookie ess_ui_theme=key;max-age=31536000;path=/;SameSite=Lax;
  if logged in (body has data-authed="1" — B adds this attribute) → POST
  /api/ui-theme {theme:key} (JSON, CSRF header X-CSRFToken from meta) → 200; invalid key → 400.
- Keep the avatar-menu "Theme" item: change it to open the picker popover.

## Backend
- User.ui_theme: nullable String(16). Model attr in app/models.py + migration via the
  existing add_column("users", "ui_theme", "VARCHAR(16)") pattern in app/__init__.py.
- Endpoint in app/desk.py: @desk_bp.route("/api/ui-theme", methods=["POST"])
  @login_required — validate key, set current_user.ui_theme, db.session.commit(),
  record audit? (lightweight: skip audit, it's a cosmetic preference — but the codebase
  audits settings changes; keep it unaudited for now, consistent with other per-user prefs).
  Return {"ok": true, "theme": key}.

## Files touched by stream
- A: app/static/style.css (+ the stylesheet ?v= bump line in base.html — ONLY that line).
- B: app/templates/base.html (picker markup, data-theme resolution, body data-authed),
  app/static/desk.js (picker JS, old-toggle replacement, cookie migration),
  app/models.py (attr), app/__init__.py (add_column), app/desk.py (endpoint),
  app/templates/login.html (theme button if cheap).
- Both: keep .35s colour transitions + @media (prefers-reduced-motion: reduce) kill-switch;
  keep .bottomnav base display:none rule and all V3.8 dashboard widgets working.
