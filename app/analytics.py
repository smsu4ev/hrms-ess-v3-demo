"""HR Analytics (V3.10) — data engine + hand-rolled SVG chart builders.

No chart CDN: every chart is server-rendered inline SVG so the page works
offline. Colours come from the V3.9 CSS variables (``var(--blue)`` etc.), so
all four themes render correctly. Hover tooltips use native ``<title>``
elements. Key elements carry ``data-*`` attributes so the test suite can
assert rendered values.

Data contract: V310-DATA-CONTRACT.md (authoritative over the spec).
"""
import calendar as calmod
import html
import math
from datetime import date

from app.models import (User, SeparationRecord, AttendanceRecord,
                        EmployeeDocument, LeaveBalance,
                        PayrollRun, PayrollOTHours, PayrollLine)

ACTIVE_STATUSES = ("active", "probation")
# Absenteeism denominator per the contract: holiday/weekoff excluded.
_ABS_DENOM = ("present", "absent", "late", "leave")

# Vital-document importance tiers (vault categories only — user-level
# visa/passport/emirates-id fields are deliberately skipped to avoid
# double-counting; see the contract).
_T1 = {"Residence Visa", "Emirates ID", "Passport", "Labour Card"}
_T2 = {"Driving Licence"}
TIER_LABELS = ["Tier 1 · Critical", "Tier 2 · Important", "Tier 3 · Other"]
TIER_CAPTION = ("Tier 1 = Residence Visa, Emirates ID, Passport, Labour Card; "
                "Tier 2 = Driving Licence (certifications are not tracked as a "
                "separate document category yet); Tier 3 = everything else in "
                "the vault. User-level visa/passport/Emirates ID fields are "
                "excluded to avoid double-counting.")
WINDOW_LABELS = ["Expired", "\u226430d", "31\u201360d", "61\u201390d"]
WINDOW_COLORS = ["var(--danger)", "var(--warning)", "var(--blue)",
                 "var(--navy-2)"]


def leaver_date(u):
    """Effective separation date: separation_date if set, else exit_date."""
    return u.separation_date or u.exit_date


def months_back(n):
    """Last ``n`` calendar months as (year, month), oldest first, ending
    with the current month."""
    t = date.today()
    out = []
    y, m = t.year, t.month
    for _ in range(max(1, n)):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def month_bounds(y, m):
    start = date(y, m, 1)
    return start, date(y, m, calmod.monthrange(y, m)[1])


def month_label(y, m):
    return f"{calmod.month_abbr[m]} {y}"


def base_users(viewer):
    """Users in scope for ``viewer`` (V3.10 contract: manager scoping).

    Admin / HR Manager / HR Staff see company-wide; a plain manager sees
    only their own department. Records still awaiting HR-Manager approval
    (``pending_approval``) are never employees and are excluded.
    """
    q = User.query.filter(User.employment_status != "pending_approval")
    if viewer.is_admin or viewer.is_hr_manager or viewer.is_hr_staff:
        return q.all()
    dept = (viewer.department or "").strip()
    return q.filter(User.department == dept).all() if dept else []


def dept_filtered(users, dept):
    """Apply the HR department-select filter (``all`` = no filter)."""
    if not dept or dept == "all":
        return users
    return [u for u in users if (u.department or "") == dept]


def headcount_at(users, day):
    """Employees on the payroll on ``day`` (joined on/before, not left before)."""
    n = 0
    for u in users:
        j = u.joining_date
        lv = leaver_date(u)
        if (j is None or j <= day) and (lv is None or lv >= day):
            n += 1
    return n


def _leavers_in(users, start, end):
    return [u for u in users
            if leaver_date(u) is not None and start <= leaver_date(u) <= end]


def attrition_series(users, months):
    """Per-month attrition: leavers / avg(headcount at start, end) * 100."""
    series = []
    for y, m in months:
        start, end = month_bounds(y, m)
        leavers = _leavers_in(users, start, end)
        hs = headcount_at(users, start)
        he = headcount_at(users, end)
        denom = (hs + he) / 2
        ratio = (len(leavers) / denom * 100) if denom else None
        series.append({
            "year": y, "month": m, "label": month_label(y, m),
            "period": f"{y:04d}-{m:02d}",
            "leavers": len(leavers), "hc_start": hs, "hc_end": he,
            "ratio": ratio,
        })
    return series


def attrition_ytd(users, sep_by_code):
    """Year-to-date attrition + voluntary/involuntary split from the
    separation register (omitted when no separation records exist)."""
    t = date.today()
    start = date(t.year, 1, 1)
    leavers = _leavers_in(users, start, t)
    hs = headcount_at(users, start)
    he = headcount_at(users, t)
    denom = (hs + he) / 2
    ratio = (len(leavers) / denom * 100) if denom and leavers else (
        0.0 if denom else None)
    vol = inv = other = 0
    matched = 0
    for u in leavers:
        s = sep_by_code.get(u.id)
        if not s:
            continue
        matched += 1
        if s.separation_type in SeparationRecord.voluntary_types():
            vol += 1
        elif s.separation_type in SeparationRecord.involuntary_types():
            inv += 1
        else:
            other += 1
    return {"leavers": len(leavers), "ratio": ratio,
            "voluntary": vol, "involuntary": inv, "other": other,
            "matched": matched}


def sep_by_code():
    return {s.employee_code: s for s in SeparationRecord.query.all()}


def manpower(users):
    """Active headcount per department, descending (blank departments skipped)."""
    counts = {}
    for u in users:
        if u.employment_status in ACTIVE_STATUSES:
            d = (u.department or "").strip()
            if d:
                counts[d] = counts.get(d, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def doc_expiry_data(users, today):
    """Vault documents with an expiry date, bucketed by tier x window.

    Returns (tiers, soonest): tiers = [ [expired, d30, d3060, d6190], ... ]
    per tier index; soonest = top-10 rows ordered by expiry asc.
    """
    uids = [u.id for u in users]
    name = {u.id: u.name for u in users}
    tiers = [[0, 0, 0, 0] for _ in range(3)]
    soonest = []
    if not uids:
        return tiers, soonest
    docs = (EmployeeDocument.query
            .filter(EmployeeDocument.superseded.is_(False),
                    EmployeeDocument.expiry_date.isnot(None),
                    EmployeeDocument.user_id.in_(uids)).all())
    for d in docs:
        days = (d.expiry_date - today).days
        tier = 0 if d.category in _T1 else (1 if d.category in _T2 else 2)
        if days < 0:
            w = 0
        elif days <= 30:
            w = 1
        elif days <= 60:
            w = 2
        elif days <= 90:
            w = 3
        else:
            continue
        tiers[tier][w] += 1
        soonest.append({"user_id": d.user_id,
                        "employee": name.get(d.user_id) or d.user_id,
                        "doc": f"{d.category}: {d.title}",
                        "expiry": d.expiry_date, "days_left": days})
    soonest.sort(key=lambda r: r["expiry"])
    return tiers, soonest[:10]


def absenteeism_data(users, months):
    """Absenteeism per month: absent / (present+absent+late+leave) * 100."""
    uids = {u.id for u in users}
    dept_of = {u.id: (u.department or "").strip() for u in users}
    recs = (AttendanceRecord.query
            .filter(AttendanceRecord.user_id.in_(list(uids))).all()) if uids else []
    by_month = {}
    for r in recs:
        key = (r.date.year, r.date.month)
        by_month.setdefault(key, []).append(r)
    series = []
    dept_latest = {}
    for y, m in months:
        rows = by_month.get((y, m), [])
        counts = {s: 0 for s in _ABS_DENOM}
        for r in rows:
            if r.status in counts:
                counts[r.status] += 1
        denom = sum(counts.values())
        pct = (counts["absent"] / denom * 100) if denom else None
        series.append({"year": y, "month": m, "label": month_label(y, m),
                       "period": f"{y:04d}-{m:02d}",
                       "absent": counts["absent"], "denom": denom,
                       "pct": pct})
    # By-department bars use the latest month in the window that has records.
    latest = None
    for s in reversed(series):
        if s["denom"]:
            latest = (s["year"], s["month"])
            break
    if latest:
        agg = {}
        for r in by_month.get(latest, []):
            d = dept_of.get(r.user_id) or "(no department)"
            a = agg.setdefault(d, {"absent": 0, "denom": 0})
            if r.status == "absent":
                a["absent"] += 1
            if r.status in _ABS_DENOM:
                a["denom"] += 1
        for d, a in agg.items():
            if a["denom"]:
                dept_latest[d] = a["absent"] / a["denom"] * 100
    return series, dept_latest, latest


def leave_data(users):
    """Annual-leave utilisation: sums + per-department used vs remaining."""
    uids = {u.id for u in users}
    by_dept = {}
    used_t = ent_t = 0.0
    if uids:
        bals = (LeaveBalance.query
                .filter(LeaveBalance.leave_type == "annual",
                        LeaveBalance.user_id.in_(list(uids))).all())
        dept_of = {u.id: (u.department or "").strip() or "(no department)"
                   for u in users}
        for b in bals:
            used = b.used or 0.0
            ent = b.entitled or 0.0
            used_t += used
            ent_t += ent
            d = dept_of.get(b.user_id, "(no department)")
            agg = by_dept.setdefault(d, {"used": 0.0, "entitled": 0.0})
            agg["used"] += used
            agg["entitled"] += ent
    pct = (used_t / ent_t * 100) if ent_t else None
    return {"used": used_t, "entitled": ent_t, "pct": pct, "by_dept": by_dept}


def locked_runs(limit=6):
    """Locked/approved payroll runs, oldest first (``period`` is YYYY-MM)."""
    runs = (PayrollRun.query
            .filter(PayrollRun.status.in_(("locked", "approved")))
            .order_by(PayrollRun.period).all())
    return runs[-limit:]


def ot_data(users, runs):
    """Overtime HOURS per locked run (never amounts) + by-dept for the latest."""
    uids = {u.id for u in users}
    dept_of = {u.id: (u.department or "").strip() or "(no department)"
               for u in users}
    per_run = []
    dept_latest = {}
    run_ids = [r.id for r in runs]
    rows = (PayrollOTHours.query
            .filter(PayrollOTHours.run_id.in_(run_ids),
                    PayrollOTHours.user_id.in_(list(uids))).all()) if (
                        run_ids and uids) else []
    by_run = {}
    for r in rows:
        by_run.setdefault(r.run_id, 0.0)
        by_run[r.run_id] += r.hours or 0.0
    for r in runs:
        per_run.append({"period": r.period, "hours": by_run.get(r.id, 0.0)})
    if runs:
        latest = runs[-1]
        agg = {}
        for r in rows:
            if r.run_id != latest.id:
                continue
            d = dept_of.get(r.user_id, "(no department)")
            agg[d] = agg.get(d, 0.0) + (r.hours or 0.0)
        dept_latest = dict(sorted(agg.items(), key=lambda kv: -kv[1]))
    return per_run, dept_latest


def salary_data(users, runs):
    """Net-pay totals per locked run + biggest movers (needs >= 2 runs).

    Hours/amounts of OT are never used here; this section is only rendered
    when the viewer has salary visibility (server-side).
    """
    if len(runs) < 2:
        return None
    uids = {u.id for u in users}
    name = {u.id: u.name for u in users}
    totals = []
    lines_by_run = {}
    for r in runs:
        lines = PayrollLine.query.filter_by(run_id=r.id).all()
        net = sum((l.net or 0.0) for l in lines
                  if not uids or l.user_id in uids)
        totals.append({"period": r.period, "net": net, "run_id": r.id})
        lines_by_run[r.id] = {l.user_id: (l.net or 0.0)
                              for l in lines if not uids or l.user_id in uids}
    for i in range(1, len(totals)):
        prev, cur = totals[i - 1]["net"], totals[i]["net"]
        totals[i]["delta_pct"] = ((cur - prev) / prev * 100) if prev else None
    totals[0]["delta_pct"] = None
    prev_map = lines_by_run[runs[-2].id]
    cur_map = lines_by_run[runs[-1].id]
    movers = []
    for uid in set(prev_map) & set(cur_map):
        p, c = prev_map[uid], cur_map[uid]
        delta = c - p
        movers.append({"user_id": uid, "name": name.get(uid) or uid,
                       "prev": p, "latest": c, "delta": delta,
                       "delta_pct": (delta / p * 100) if p else None})
    movers.sort(key=lambda m: abs(m["delta"]), reverse=True)
    return {"totals": totals, "movers": movers[:8],
            "latest_run_id": runs[-1].id,
            "prev_period": runs[-2].period, "latest_period": runs[-1].period}


def joiners_leavers(users, months):
    """Joiners (joining_date) vs leavers (leaver date) per month."""
    series = []
    for y, m in months:
        start, end = month_bounds(y, m)
        j = sum(1 for u in users
                if u.joining_date and start <= u.joining_date <= end)
        lv = _leavers_in(users, start, end)
        series.append({"year": y, "month": m, "label": month_label(y, m),
                       "period": f"{y:04d}-{m:02d}",
                       "joiners": j, "leavers": len(lv)})
    return series


# ------------------------- formatting -------------------------

def _esc(s):
    return html.escape(str(s))


def fmt_pct(v):
    return "—" if v is None else f"{v:.1f}%"


def fmt_int(v):
    return f"{int(v):,}"


def fmt_hours(v):
    return f"{v:,.1f}"


def fmt_aed(v):
    return f"AED {v:,.2f}"


# ------------------------- SVG builders -------------------------

_ML, _MR, _MT, _MB = 46, 10, 14, 40


def _wrap(w, h, inner, maxw=None, aria="chart"):
    mw = maxw or w
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{_esc(aria)}" '
            f'style="max-width:{mw}px;height:auto;display:block">'
            f'<style>'
            f'.ax-t{{fill:var(--muted);font-size:11px}}'
            f'.ax-v{{fill:var(--text);font-size:12px;font-weight:600}}'
            f'.ax-bar{{transition:opacity .15s}}'
            f'.ax-bar:hover{{opacity:.8}}'
            f'.ax-pt{{transition:r .15s}}'
            f'.ax-pt:hover{{r:7}}'
            f'</style>{inner}</svg>')


def _grid_y(w, h, ml, mr, mt, mb, vmax, ticks=4, int_ticks=False):
    """Horizontal gridlines + y labels. int_ticks=True for count axes."""
    out = []
    pw = w - ml - mr
    ph = h - mt - mb
    for i in range(ticks + 1):
        v = vmax * i / ticks
        y = mt + ph - (ph * i / ticks)
        label = str(int(round(v))) if int_ticks else _y_tick(v)
        out.append(f'<line x1="{ml}" y1="{y}" x2="{ml+pw}" y2="{y}" '
                   f'stroke="var(--border,#e5e7eb)" stroke-width="1" '
                   f'{"stroke-dasharray=\'3,3\'" if i else ""}/>'
                   f'<text class="ax-t" x="{ml-6}" y="{y+4}" text-anchor="end">'
                   f'{label}</text>')
    return "".join(out), pw, ph


def _y_tick(v):
    if v >= 1000:
        return f"{v/1000:.0f}k"
    if abs(v) >= 100:
        return f"{v:.0f}"
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _esc_data(d):
    return " ".join(f'data-{k}="{_esc(v)}"' for k, v in d.items())


def vbars(items, w=560, h=250, aria="bar chart"):
    """Vertical bars. items: list of dicts with label, value, color,
    title, data (dict of data-* attrs)."""
    ml, mr, mt, mb = _ML, _MR, _MT, _MB
    vals = [i["value"] or 0 for i in items]
    vmax = max(vals) if vals else 0
    vmax = vmax * 1.18 if vmax > 0 else 1
    grid, pw, ph = _grid_y(w, h, ml, mr, mt, mb, vmax)
    n = max(1, len(items))
    slot = pw / n
    bw = min(54, slot * 0.62)
    out = [grid]
    for i, it in enumerate(items):
        v = it["value"] or 0
        bh = ph * v / vmax if vmax else 0
        x = ml + slot * i + (slot - bw) / 2
        y = mt + ph - bh
        color = it.get("color", "var(--blue)")
        out.append(
            f'<rect class="ax-bar" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
            f'height="{max(bh,1.5):.1f}" rx="4" style="fill:{color}" '
            f'{_esc_data(it.get("data", {}))}>'
            f'<title>{_esc(it.get("title", ""))}</title></rect>')
        if it.get("show_value", True) and v:
            out.append(f'<text class="ax-v" x="{x+bw/2:.1f}" y="{y-6:.1f}" '
                       f'text-anchor="middle">{_esc(it["value_label"])}</text>')
        lx = ml + slot * i + slot / 2
        rot = len(items) > 8
        if rot:
            out.append(f'<text class="ax-t" x="{lx:.1f}" y="{mt+ph+10}" '
                       f'text-anchor="end" transform="rotate(-32 {lx:.1f} {mt+ph+10})">'
                       f'{_esc(it["label"])}</text>')
        else:
            out.append(f'<text class="ax-t" x="{lx:.1f}" y="{mt+ph+22}" '
                       f'text-anchor="middle">{_esc(it["label"])}</text>')
    return _wrap(w, h, "".join(out), aria=aria)


def hbars(items, w=560, aria="bar chart", link_key=None):
    """Horizontal bars. items: label, value, color, title, data, href (opt)."""
    row_h, pad = 30, 6
    ml, mt, mr = 150, 10, 70
    h = mt * 2 + len(items) * row_h
    vals = [i["value"] or 0 for i in items]
    vmax = max(vals) * 1.12 if vals and max(vals) > 0 else 1
    pw = w - ml - mr
    out = []
    for i, it in enumerate(items):
        v = it["value"] or 0
        y = mt + i * row_h
        bw = pw * v / vmax
        color = it.get("color", "var(--blue)")
        bar = (f'<rect class="ax-bar" x="{ml}" y="{y+pad}" width="{max(bw,2):.1f}" '
               f'height="{row_h-2*pad}" rx="4" style="fill:{color}" '
               f'{_esc_data(it.get("data", {}))}>'
               f'<title>{_esc(it.get("title", ""))}</title></rect>')
        if link_key and it.get(link_key):
            bar = f'<a href="{_esc(it[link_key])}">{bar}</a>'
        out.append(bar)
        out.append(f'<text class="ax-t" x="{ml-8}" y="{y+row_h/2+4}" '
                   f'text-anchor="end">{_esc(it["label"])}</text>')
        out.append(f'<text class="ax-v" x="{ml+max(bw,2)+6:.1f}" '
                   f'y="{y+row_h/2+4}">{_esc(it["value_label"])}</text>')
    return _wrap(w, h, "".join(out), aria=aria)


def line_chart(points, w=560, h=250, aria="line chart", color="var(--blue)"):
    """Line + area. points: list of dicts label, value, title, data."""
    ml, mr, mt, mb = _ML, _MR, _MT, _MB
    vals = [p["value"] for p in points if p["value"] is not None]
    vmax = max(vals) * 1.25 if vals and max(vals) > 0 else 1
    grid, pw, ph = _grid_y(w, h, ml, mr, mt, mb, vmax)
    n = len(points)
    xs = [ml + pw * i / max(1, n - 1) for i in range(n)] if n else []
    def y_of(v):
        return mt + ph - (ph * (v or 0) / vmax)
    path = " ".join(f"{'M' if i == 0 else 'L'}{xs[i]:.1f},{y_of(points[i]['value']):.1f}"
                    for i in range(n))
    area = f"{path} L{xs[-1]:.1f},{mt+ph:.1f} L{xs[0]:.1f},{mt+ph:.1f} Z" if n else ""
    out = [grid]
    if area:
        out.append(f'<path d="{area}" style="fill:{color}" opacity="0.14"/>')
        out.append(f'<path d="{path}" fill="none" style="stroke:{color}" '
                   f'stroke-width="2.5" stroke-linecap="round"/>')
    for i, p in enumerate(points):
        v = p["value"]
        out.append(
            f'<circle class="ax-pt" cx="{xs[i]:.1f}" cy="{y_of(v):.1f}" r="4.5" '
            f'style="fill:{color};stroke:var(--card,#fff);stroke-width:2" '
            f'{_esc_data(p.get("data", {}))}>'
            f'<title>{_esc(p.get("title", ""))}</title></circle>')
        if v:
            out.append(f'<text class="ax-v" x="{xs[i]:.1f}" y="{y_of(v)-10:.1f}" '
                       f'text-anchor="middle">{_esc(p["value_label"])}</text>')
        lx = xs[i]
        rot = n > 8
        if rot:
            out.append(f'<text class="ax-t" x="{lx:.1f}" y="{mt+ph+10}" '
                       f'text-anchor="end" transform="rotate(-32 {lx:.1f} {mt+ph+10})">'
                       f'{_esc(p["label"])}</text>')
        else:
            out.append(f'<text class="ax-t" x="{lx:.1f}" y="{mt+ph+22}" '
                       f'text-anchor="middle">{_esc(p["label"])}</text>')
    return _wrap(w, h, "".join(out), aria=aria)


def stacked_bars(tiers, w=560, h=260, aria="stacked bar chart"):
    """tiers: list of 4-lists (windows) per tier; x = tier, stacks = windows."""
    ml, mr, mt, mb = _ML, _MR, _MT, _MB
    totals = [sum(t) for t in tiers]
    vmax = max(totals) * 1.2 if totals and max(totals) > 0 else 1
    _vi = max(1, int(math.ceil(vmax)))
    grid, pw, ph = _grid_y(w, h, ml, mr, mt, mb, _vi,
                            ticks=min(4, _vi), int_ticks=True)
    n = max(1, len(tiers))
    slot = pw / n
    bw = min(90, slot * 0.55)
    out = [grid]
    for ti, tvals in enumerate(tiers):
        x = ml + slot * ti + (slot - bw) / 2
        y = mt + ph
        for wi, v in enumerate(tvals):
            if not v:
                continue
            sh = ph * v / _vi
            y -= sh
            out.append(
                f'<rect class="ax-bar" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                f'height="{max(sh,1.5):.1f}" style="fill:{WINDOW_COLORS[wi]}" '
                f'data-tier="{ti}" data-window="{_esc(WINDOW_LABELS[wi])}" '
                f'data-count="{v}">'
                f'<title>{_esc(TIER_LABELS[ti])} — {WINDOW_LABELS[wi]}: {v} '
                f'document{"s" if v != 1 else ""}</title></rect>')
        if totals[ti]:
            out.append(f'<text class="ax-v" x="{x+bw/2:.1f}" y="{y-6:.1f}" '
                       f'text-anchor="middle">{totals[ti]}</text>')
        lx = ml + slot * ti + slot / 2
        out.append(f'<text class="ax-t" x="{lx:.1f}" y="{mt+ph+22}" '
                   f'text-anchor="middle">{_esc(TIER_LABELS[ti].split(" · ")[0])}</text>')
    # legend
    lx0 = ml
    for wi, lbl in enumerate(WINDOW_LABELS):
        out.append(f'<rect x="{lx0+wi*86}" y="{h-12}" width="12" height="12" rx="3" '
                   f'style="fill:{WINDOW_COLORS[wi]}"/>'
                   f'<text class="ax-t" x="{lx0+wi*86+16}" y="{h-2}">{_esc(lbl)}</text>')
    return _wrap(w, h + 18, "".join(out), aria=aria)


def donut(pct, w=200, h=200, aria="donut chart", data=None):
    """Single-ring donut for a 0–100 percentage."""
    r, cx, cy = 70, 100, 100
    c = 2 * 3.14159 * r
    p = max(0.0, min(100.0, pct or 0.0))
    out = [
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="var(--border,#e5e7eb)" stroke-width="20"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'style="stroke:var(--blue)" stroke-width="20" stroke-linecap="round" '
        f'stroke-dasharray="{p/100*c:.1f} {c:.1f}" transform="rotate(-90 {cx} {cy})" '
        f'{_esc_data(data or {})}>'
        f'<title>{fmt_pct(p)} utilised</title></circle>',
        f'<text class="ax-v" x="{cx}" y="{cy+7}" text-anchor="middle" '
        f'style="font-size:22px">{fmt_pct(p)}</text>',
    ]
    return _wrap(w, h, "".join(out), maxw=220, aria=aria)


def grouped_bars(s1, s2, w=600, h=250, aria="grouped bar chart"):
    """Two series grouped per month. s1/s2: dicts with name, color, items
    (list of dicts: label, value, title, data)."""
    ml, mr, mt, mb = _ML, _MR, _MT, _MB
    n = max(len(s1["items"]), len(s2["items"]))
    vals = [i["value"] or 0 for i in s1["items"]] + [i["value"] or 0 for i in s2["items"]]
    vmax = max(vals) * 1.25 if vals and max(vals) > 0 else 1
    _vi = max(1, int(math.ceil(vmax)))
    grid, pw, ph = _grid_y(w, h, ml, mr, mt, mb, _vi,
                            ticks=min(4, _vi), int_ticks=True)
    slot = pw / max(1, n)
    bw = min(26, slot * 0.32)
    out = [grid]
    for i in range(n):
        cx = ml + slot * i + slot / 2
        for k, s in enumerate((s1, s2)):
            it = s["items"][i] if i < len(s["items"]) else None
            v = (it["value"] or 0) if it else 0
            bh = ph * v / _vi
            x = cx + (k - 0.5) * (bw + 3) - bw / 2
            y = mt + ph - bh
            out.append(
                f'<rect class="ax-bar" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                f'height="{max(bh,1.5):.1f}" rx="3" style="fill:{s["color"]}" '
                f'{_esc_data((it or {}).get("data", {}))}>'
                f'<title>{_esc((it or {}).get("title", ""))}</title></rect>')
            if v:
                out.append(f'<text class="ax-v" x="{x+bw/2:.1f}" y="{y-5:.1f}" '
                           f'text-anchor="middle" style="font-size:11px">'
                           f'{int(v)}</text>')
        lx = ml + slot * i + slot / 2
        rot = n > 8
        if rot:
            out.append(f'<text class="ax-t" x="{lx:.1f}" y="{mt+ph+10}" '
                       f'text-anchor="end" transform="rotate(-32 {lx:.1f} {mt+ph+10})">'
                       f'{_esc(s1["items"][i]["label"])}</text>')
        else:
            out.append(f'<text class="ax-t" x="{lx:.1f}" y="{mt+ph+22}" '
                       f'text-anchor="middle">{_esc(s1["items"][i]["label"])}</text>')
    # legend
    for k, s in enumerate((s1, s2)):
        lx0 = ml + k * 130
        out.append(f'<rect x="{lx0}" y="{h-12}" width="12" height="12" rx="3" '
                   f'style="fill:{s["color"]}"/>'
                   f'<text class="ax-t" x="{lx0+16}" y="{h-2}">{_esc(s["name"])}</text>')
    return _wrap(w, h + 18, "".join(out), aria=aria)


def empty_state(msg):
    return (f'<p class="ax-empty"><em>{_esc(msg)}</em></p>')
