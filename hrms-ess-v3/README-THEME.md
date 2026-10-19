# Theme: "Slate & Indigo Pro"

A CSS-only retheme of the HRMS + ESS portal. No behaviour, layout, spacing or
border-radius was changed — only colour tokens and their usages.

## Palette (app/static/style.css :root)

| Token | Value | Use |
|---|---|---|
| `--primary` | `#4f46e5` (indigo-600) | Buttons, links, active states, focus borders |
| `--primary-dark` | `#4338ca` (indigo-700) | Darker accents, hover edges |
| `--primary-soft` | `#eef2ff` (indigo-50) | Soft backgrounds (unread notices, active tabs, initials) |
| `--navy` | `#0f172a` (slate-900) | Sidebar, topbar, table headers |
| `--navy-soft` | `#1e293b` (slate-800) | Sidebar hover/active, header gradient |
| `--bg` | `#f1f5f9` (slate-100) | Page background |
| `--card` | `#ffffff` | Cards, tables, forms |
| `--border` | `#e2e8f0` (slate-200) | Borders, chips, progress tracks |
| `--text` | `#0f172a` | Headings |
| `--body` | `#334155` (slate-700) | Body text token (new) |
| `--muted` | `#64748b` (slate-500) | Muted text |
| `--success` | `#059669` (emerald-600) | Success states |
| `--warning` | `#d97706` (amber-600) | Warning states |
| `--danger` | `#dc2626` (red-600) | Danger states |
| `--info` | `#0284c7` (sky-600) | Info states |
| focus glow | `rgba(79,70,229,.15)` | Input focus rings, tile hover |

Replaced: Argon violet `#5e72e4` / `#324cdd`, old navy `#172b4d`, old
soft backgrounds `#eef1fd` / `#f6f8ff`, old semantics (`#2dce89`, `#fb6340`,
`#f5365c`, `#11cdef`), violet badges `#ede9fe`/`#5b21b6` (now indigo-soft),
chip warn colours, notification badge, night-shift chip (navy + amber-400),
warning banners (amber family), old grey borders (`#e9ecef`, `#eef1f4`) →
`var(--border)`, totals row `#f7f9fc` → `#f8fafc`, settlement negative
`#a12222` → `#dc2626`, report subtotal `#f2f4f9` → `#f1f5f9`, JD signature
stroke → `#0f172a`.

Badge family (`badge.pending/approved/rejected/...`) already used Tailwind
semantic shades (emerald/amber/red/blue/grey) and was left as-is.

## Files touched

- `app/static/style.css` — token block + all literal usages listed above.
- `app/templates/base.html` — stylesheet link now `style.css?v=2`
  (static assets are served `Cache-Control: immutable` for 1 year, so the
  query bump forces browsers to fetch the new theme).
- `app/templates/hr_employees.html`, `hr_employee_detail.html` — inline
  avatar initials background `#5e72e4` → `#4f46e5`.
- `app/templates/hr_settlement_detail.html` — negative-amount colour
  `#a12222` → `#dc2626` (2 occurrences).
- `app/templates/hr_report.html` — subtotal row `#f2f4f9` → `#f1f5f9`.
- `app/static/jd_sign.js` — signature pad stroke colour only (no logic change).

No test asserts a colour value, so no test changes were needed for the theme.
