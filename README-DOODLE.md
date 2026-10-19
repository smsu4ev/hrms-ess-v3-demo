# V3.6 — WhatsApp-style doodle background theme (retail-fixture industry)

## What
A WhatsApp-doodle-style background: warm-cream page backdrop covered with small
thin-outline line doodles of retail-fixture industry life (shelving units,
shopping trolleys, storefronts, forklifts, welders' tools, pallets, hard-hat
smileys…), at low contrast so text always stays readable.

## Files
- `app/static/doodle-bg.svg` (6,233 bytes) — one 560×560 pattern tile holding
  **26 line doodles**: gondola shelving unit, display rack with hooks, shopping
  trolley, clothes hanger, price tag, storefront with striped awning, mannequin,
  POS counter, cardboard box, wooden pallet, delivery truck, forklift, hard-hat
  smiley, plain smiley, winking smiley, wrench, hammer, screwdriver, welding
  spark burst, tape measure, pipe elbow, gear, light bulb, star, heart, checkmark
  badge. Strokes: `#0f172a` @ 0.5 opacity, 2.2 width, round caps, no fills.
  All doodles sit ≥30px inside the tile edges (wrap-safe).
- `app/static/style.css` — new token `--bg-cream: #f6f1e7`; `body` and
  `.authpage` use the cream + `url("doodle-bg.svg")` at `background-size:
  560px`; login card keeps white surface + indigo button, with a small indigo
  top accent so brand identity survives the cream backdrop. Sidebar/topbar/
  bottom nav stay deep slate; all cards/tables/forms stay on opaque white.
- `app/templates/base.html` — stylesheet bumped to `style.css?v=4`
  (static assets are immutable-cached for 1 year).

## Design notes
- `--bg` (slate #f1f5f9) was only used by `body`, so no component broke when
  the page background went cream.
- Pattern is deliberately visible in content-area gaps (like the WhatsApp
  chat wallpaper) but sits behind opaque cards — verified readable on
  desktop, mobile and the login page via screenshots.

## Verification
- test_v331: 71/71, test_v35: 39/39 (fresh SQLite DBs).
- Screenshots: `hrms-theme-shots/doodle-login.png`,
  `doodle-dashboard-pc.png`, `doodle-dashboard-mobile.png` (local seeded
  instance via Playwright).
- Zip rebuilt with the V3.5 exclusion rules.
