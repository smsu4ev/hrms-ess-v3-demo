# Sidebar Navigation — Collapsible Groups (V3.6)

The sidebar (`app/templates/base.html`, `<nav class="sidebar">`) now organises all
links into collapsible, animated groups instead of one long scrolling list.

## Groups

- **Dashboard** — standalone link at the top (unchanged, always visible)
- **My Self-Service** — the 16 employee ESS links (Inbox … Celebrations), new group, visible to all roles
- **Manager** — `is_manager` only, 6 links (Leave Approvals, Team Overview, Regularisation Approvals, Evaluations, Grievance Inbox, Helpdesk Queue)
- **Payroll** — `is_admin` or `can_view_salary`, 8 links (Payroll Runs … Document Vault)
- **Retail Operations** — `is_manager` only, 12 links; Final Settlements keeps its own nested `is_admin`/`can_view_salary` condition
- **HRMS Admin** — `is_admin` only, 12 links (Employee Central … WhatsApp Outbox)
- **Engagement** — `is_admin` only, 5 links (Work Locations … Job Descriptions)

All role conditions, link order, hrefs and labels are unchanged. The mobile bottom
nav (`bottomnav`, 5 quick links) is unchanged.

## Expansion behaviour (composing triggers)

A group's links are shown when ANY of these hold:

1. **Click/tap (`.open`)** — the group head button toggles `.open` and updates
   `aria-expanded`. This is the only way on touch/mobile (the ≤860px drawer),
   and pins the group open on desktop too. Delegated click handler on
   `document` in `app.js`, so it survives PJAX page swaps.
2. **Hover (desktop only)** — pure CSS: `@media (hover:hover) and (min-width:861px)`,
   hovering (or focusing within) a group expands it transiently; it collapses
   again on mouse-leave unless pinned `.open`.
3. **Current page (`.has-active`)** — `app.js` marks the group containing the
   active sidebar link (after full loads and after every PJAX swap, via the
   existing `swapContent`/`markActiveNav` flow), so the group you are working in
   is always expanded.

Mobile drawer (≤860px): opening one group collapses the others (accordion),
except the `.has-active` group which keeps its state.

## Animation (style.css)

- Collapsed: `grid-template-rows: 0fr`, `opacity: 0`; expanded: `1fr`, `opacity: 1`.
- Transition: `grid-template-rows .28s cubic-bezier(.4,0,.2,1)`, `opacity .22s ease`
  (smooth width-curve animation, no max-height hacks).
- Inner links settle with a subtle `translateY(-2px → 0)`; chevron rotates
  0 → 90° with the same easing; head hover brightens.
- `prefers-reduced-motion: reduce` disables all of the above.
- Sidebar is vertically scrollable so expanded groups never get clipped.

## Cache versioning

Static assets are served `Cache-Control: immutable` for 1 year, so the changed
files are version-tagged in `base.html`: `style.css?v=3`, `app.js?v=2`.

## Tests

Re-ran on fresh SQLite DBs after the change — all green, no test edits:
test_v33 107/107, test_v331 71/71, test_v35 39/39, test_v34 73/73,
test_e2e 32/32 (via `seed.py`).
