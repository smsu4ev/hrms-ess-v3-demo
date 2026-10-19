# V3.7 — "App UI": simple + animated interface (user decision 2026-10-19)

User verdict on the V3.6 Frappe desk clone: "everything is complicated — use another model,
simple and animated." Model chosen: the mobile super-app / fintech pattern (greeting home,
big task tiles, bottom tabs) adapted to desktop. The Frappe rail + workspace sidebar are
REMOVED. Every existing route/feature stays; this is a shell + home rework only.

## Shell (base.html)
- TOP BAR (all screens): logo + "800 Retail HR"; hamburger (opens drawer); search box
  (opens the existing Awesome Bar overlay — keep /api/awesome); bell with unread badge
  (opens the existing notifications panel — keep); avatar menu (My Profile / Theme /
  HR Settings (admin) / Logout POST). Desktop also shows 3 plain text links in the top bar:
  Home · All Services · (My Team for managers/admins).
- DRAWER (replaces rail+sidebar): slide-in from left, grouped collapsible sections using
  the SAME role-filtered link registry the workspace sidebar used (workspaces.py groups are
  the source — every link must remain reachable). Scrim, slide .28s cubic-bezier, the group
  containing the current page auto-expands; close on link click (PJAX) / scrim / Esc.
- MOBILE: bottom tab bar stays (Home, Punch, Leave, Payslips, More → drawer). The desktop
  hide rule for .bottomnav (V3.6.2 fix) must NOT regress.
- No .rail, no .ws-sidebar in the new shell markup. /ws/<key> pages keep working as section
  landing pages (restyle their header to the app style; they are linked from All Services,
  the drawer and search).

## Home (the /dashboard landing, role-based)
- Greeting header: "Good morning/afternoon/evening, <first name>" + date + department.
- TODAY hero card:
  - Employee: today's shift (from roster), punch status, big Check In / Check Out button
    (links to the punch page) with a subtle pulse ring animation when not yet punched.
  - Manager: team today — present / absent / on leave counts (animated count-up).
  - Admin/HR: pending approvals, visa expiries (20d), open tickets counts (count-up).
- QUICK TILES grid (2 cols mobile, 4 cols desktop), role-filtered, each tile: soft-tinted
  icon chip + plain label + live badge count where relevant. Employee tiles: Check In/Out,
  Apply Leave, My Leave, My Payslips, My Documents, Request Letter, My Expenses,
  My Tickets, Notices, Directory. Manager adds: Approvals (badge), Team Overview,
  Team Calendar, Roster. HR/Admin adds: Employees, Add Employee, Payroll, Reports,
  HR Settings, Approval Matrix. Tiles link to existing routes only.
- MY TASKS card (existing unified-inbox summary) + CELEBRATIONS strip (birthdays /
  anniversaries today, from existing data) + latest NOTICE card.
- ALL SERVICES (/apps): same tiles, grouped by area with headings — the full directory.

## Animation system (CSS transform/opacity only; low-end-phone safe)
- Page enter on PJAX swap: fade + translateY(6px), .25s.
- Tiles: staggered rise-in (per-tile --i delay), hover lift + shadow, active scale .97.
- Counters: JS count-up for [data-count] when scrolled into view.
- Drawer/panels/accordions: existing .28s cubic-bezier transitions.
- Subtle shimmer while PJAX loads.
- @media (prefers-reduced-motion: reduce): disable all of the above.

## Style
- Keep the clean light tokens; friendlier: card radius 16–20px, softer shadows, tile icon
  chips in soft tints (indigo/emerald/amber/sky/rose/violet), slate text, base font 15px.
- Bump ?v= on style.css/app.js/desk.js as changed (immutable caching).

## Tests
- New tests/test_v37.py (run with ESS_DATABASE_URL=sqlite:////tmp/test_v37.db):
  shell has no .rail/.ws-sidebar; top bar (search/bell/avatar/hamburger); drawer renders
  with role-filtered groups (admin sees HR Settings link, employee does not); home tiles
  per role (employee sees Check In + Apply Leave, not Payroll; admin sees Payroll);
  tile badge shows pending-approval count for a manager fixture; animations CSS present
  (keyframes rise + page-enter class) AND reduced-motion block present; .bottomnav base
  display:none rule intact; /apps grouped tiles render; all /ws/<key> homes still 200.
- test_v36.py: update ONLY shell-markup assertions that the new shell legitimately changes
  (rail/sidebar checks → drawer/topbar equivalents); awesome/settings/workspace tests must
  pass unchanged. Document every edit in README-V37.md. ALL other suites unchanged & green.
- Screenshots (Playwright, local demo seed): home for HRADMIN + EMP001 (desktop + mobile),
  drawer open, All Services, one /ws section page → hrms-theme-shots/ with v37- prefix.
- Rebuild ~/workspace/your_files/hrms-ess-v3.zip; README-V37.md (model, mapping, test tally,
  legacy edits). NO deploy (parent handles it). Don't touch v2/ess-app trees.
