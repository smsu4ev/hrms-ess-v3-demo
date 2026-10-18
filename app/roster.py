"""Shift patterns + weekly roster + Ramadan/midday-break settings (v3.2).

Permissions: admin/HR (admin role) write; managers get read-only roster
views for their own department; employees use /my-roster only (403 on
/hrms/roster* write actions and views).
"""
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, request, render_template, abort, redirect, url_for, flash
from flask_login import login_required, current_user

from app import db
from app.models import User, ShiftPattern, RosterAssignment, RosterSettings, AttendanceRecord
from app.audit import record
from app.hrms import admin_required

roster_bp = Blueprint("roster", __name__, url_prefix="/hrms")


def _mgr_required(fn):
    """Any manager (admin included) may view; employees get 403."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_manager:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _write_required(fn):
    """Admin-only write operations."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def _parse_date(s):
    s = (s or "").strip()
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _parse_hhmm(s):
    s = (s or "").strip()
    if len(s) != 5 or s[2] != ":":
        return None
    h, m = s.split(":")
    if not (h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59):
        return None
    return s


def get_settings():
    s = db.session.get(RosterSettings, 1)
    if s is None:
        s = RosterSettings(id=1)
        db.session.add(s)
        db.session.commit()
    return s


def _monday(d):
    return d - timedelta(days=d.weekday())


def _in_ramadan(s, day):
    return bool(s.ramadan_start and s.ramadan_end
                and s.ramadan_start <= day <= s.ramadan_end)


def _in_midday_season(s, day):
    # "MM-DD" strings compare lexicographically; season does not wrap the year.
    md = day.strftime("%m-%d")
    return s.midday_season_start_md <= md <= s.midday_season_end_md


def _md_label(md):
    months = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May",
              "06": "Jun", "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct",
              "11": "Nov", "12": "Dec"}
    try:
        mm, dd = md.split("-")
        return f"{months.get(mm, mm)} {int(dd)}"
    except Exception:
        return md


def _midday_outdoor_warning(s, shift, day):
    """v3.2.1: warn (do not block) when an outdoor shift is assigned on a
    date inside the summer midday-break season."""
    if shift and shift.is_outdoor and _in_midday_season(s, day):
        return (f"Midday break {s.midday_break_start}\u2013{s.midday_break_end} applies "
                f"to outdoor work on {day.isoformat()} ({shift.code}) \u2014 ensure "
                f"the shift plan covers it.")
    return None


def _visible_employees():
    """Admin sees everyone; a non-admin manager sees only their department."""
    q = User.query.filter(User.is_active.is_(True))
    if not current_user.is_admin and current_user.department:
        q = q.filter(User.department == current_user.department)
    return q.order_by(User.name).all()


# ---------------- Shift patterns ----------------

@roster_bp.route("/shifts", methods=["GET", "POST"])
@login_required
@_mgr_required
def shifts():
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        f = request.form
        code = (f.get("code") or "").strip().upper()
        start = _parse_hhmm(f.get("start_time"))
        end = _parse_hhmm(f.get("end_time"))
        if not code or not f.get("name") or not start or not end:
            flash("Code, name and valid start/end times (HH:MM) are required.", "error")
        elif ShiftPattern.query.filter_by(code=code).first():
            flash(f"Shift code {code} already exists.", "error")
        else:
            try:
                brk = max(0, int(f.get("break_minutes") or 60))
                grace = max(0, int(f.get("grace_minutes") or 10))
            except ValueError:
                brk, grace = 60, 10
            db.session.add(ShiftPattern(
                code=code, name=f.get("name").strip(), start_time=start,
                end_time=end, break_minutes=brk, grace_minutes=grace,
                is_outdoor=bool(f.get("is_outdoor"))))
            record("shift_pattern_created", code, entity="shift_pattern", entity_id=code)
            db.session.commit()
            flash(f"Shift {code} created.", "success")
        return redirect(url_for("roster.shifts"))
    patterns = ShiftPattern.query.order_by(ShiftPattern.code).all()
    return render_template("hr_shifts.html", patterns=patterns,
                           can_write=current_user.is_admin)


@roster_bp.route("/shifts/<int:pid>/delete", methods=["POST"])
@login_required
@_write_required
def shift_delete(pid):
    p = ShiftPattern.query.get_or_404(pid)
    if RosterAssignment.query.filter_by(shift_id=p.id).first():
        flash(f"Shift {p.code} has roster assignments and cannot be deleted.", "error")
    else:
        record("shift_pattern_deleted", p.code, entity="shift_pattern", entity_id=p.code)
        db.session.delete(p)
        db.session.commit()
        flash(f"Shift {p.code} deleted.", "success")
    return redirect(url_for("roster.shifts"))


# ---------------- Weekly roster grid ----------------

@roster_bp.route("/roster")
@login_required
@_mgr_required
def roster():
    s = get_settings()
    week = _parse_date(request.args.get("week")) or date.today()
    week = _monday(week)
    days = [week + timedelta(days=i) for i in range(7)]
    dept = (request.args.get("dept") or "").strip()
    employees = _visible_employees()
    if dept:
        employees = [u for u in employees if (u.department or "") == dept]
    depts = sorted({u.department for u in _visible_employees() if u.department})
    patterns = ShiftPattern.query.filter_by(is_active=True).order_by(ShiftPattern.code).all()
    assign = {}
    for a in RosterAssignment.query.filter(
            RosterAssignment.date >= days[0],
            RosterAssignment.date <= days[-1]).all():
        assign[(a.user_id, a.date.isoformat())] = a
    # v3.2.1: detect runs of >= 7 consecutive assigned days (no weekly rest)
    # across an extended window [week-1 .. week+6].
    ext0, ext1 = days[0] - timedelta(days=1), days[-1] + timedelta(days=1)
    visible_ids = {u.id for u in employees}
    by_user = {}
    ext_q = RosterAssignment.query.filter(
        RosterAssignment.date >= ext0,
        RosterAssignment.date <= ext1)
    if visible_ids:
        ext_q = ext_q.filter(RosterAssignment.user_id.in_(visible_ids))
    for a in ext_q.all():
        by_user.setdefault(a.user_id, set()).add(a.date)
    no_rest = set()
    for uid, dates in by_user.items():
        run = 0
        d = ext0
        flagged = False
        while d <= ext1:
            run = run + 1 if d in dates else 0
            if run >= 7 and days[0] <= d <= days[-1]:
                flagged = True
                break
            d += timedelta(days=1)
        if flagged:
            no_rest.add(uid)
    rows = []
    for u in employees:
        cells = []
        for d in days:
            a = assign.get((u.id, d.isoformat()))
            cells.append({"date": d, "assignment": a})
        rows.append({"user": u, "cells": cells,
                     "no_rest": u.id in no_rest})
    banners = []
    if any(_in_ramadan(s, d) for d in days):
        banners.append(
            f"Ramadan period: daily hours threshold {s.ramadan_daily_hours:g}h "
            f"(reduced from {s.standard_daily_hours:g}h).")
    if any(_in_midday_season(s, d) for d in days):
        banners.append(
            f"Summer midday break {s.midday_break_start}\u2013{s.midday_break_end} "
            f"applies to outdoor/site work ({_md_label(s.midday_season_start_md)} \u2013 "
            f"{_md_label(s.midday_season_end_md)}).")
    return render_template(
        "hr_roster.html", days=days, rows=rows, week=week,
        prev_week=week - timedelta(days=7), next_week=week + timedelta(days=7),
        depts=depts, dept=dept, patterns=patterns, banners=banners,
        can_write=current_user.is_admin,
        all_users=User.query.filter(User.is_active.is_(True)).order_by(User.name).all())


@roster_bp.route("/roster/bulk", methods=["POST"])
@login_required
@_write_required
def roster_bulk():
    f = request.form
    shift = ShiftPattern.query.filter_by(code=(f.get("shift") or "")).first()
    frm, to = _parse_date(f.get("from_date")), _parse_date(f.get("to_date"))
    if not shift or not frm or not to or to < frm:
        flash("Valid shift, from and to dates are required.", "error")
        return redirect(url_for("roster.roster"))
    scope = f.get("scope") or "all"
    targets = User.query.filter(User.is_active.is_(True))
    if scope.startswith("dept:"):
        targets = targets.filter(User.department == scope[5:])
    elif scope.startswith("user:"):
        targets = targets.filter(User.id == scope[5:])
    targets = targets.order_by(User.name).all()
    skip_off = f.get("skip_off") or "none"
    off_days = {"friday": {4}, "sunday": {6}}.get(skip_off, set())
    overwrite = bool(f.get("overwrite"))
    assigned = kept = skipped_off = 0
    midday_warned = 0
    s = get_settings()
    for u in targets:
        d = frm
        while d <= to:
            if d.weekday() in off_days:
                skipped_off += 1
            else:
                existing = RosterAssignment.query.filter_by(user_id=u.id, date=d).first()
                if existing and not overwrite:
                    kept += 1
                else:
                    if existing:
                        db.session.delete(existing)
                    db.session.add(RosterAssignment(
                        user_id=u.id, date=d, shift_id=shift.id,
                        created_by=current_user.id))
                    assigned += 1
                    if shift.is_outdoor and _in_midday_season(s, d):
                        midday_warned += 1
            d += timedelta(days=1)
    record("roster_bulk_assigned",
           f"{shift.code} {frm}..{to} scope={scope} assigned={assigned} kept={kept}",
           entity="roster")
    db.session.commit()
    flash(f"Roster updated: {assigned} assigned, {kept} existing kept "
          f"(tick Overwrite to replace), {skipped_off} weekly-off day(s) skipped.",
          "success")
    if midday_warned:
        flash(f"Midday break {s.midday_break_start}\u2013{s.midday_break_end} applies "
              f"to outdoor work on {midday_warned} assigned date(s) inside the "
              f"summer season \u2014 ensure the shift plans cover it.", "warning")
    return redirect(url_for("roster.roster", week=frm.isoformat()))


@roster_bp.route("/roster/assign", methods=["GET", "POST"])
@login_required
@_write_required
def roster_assign():
    uid = request.values.get("user") or ""
    d = _parse_date(request.values.get("date"))
    user = User.query.get(uid) if uid else None
    if request.method == "POST":
        if not user or not d:
            flash("Employee and date are required.", "error")
            return redirect(url_for("roster.roster"))
        code = (request.form.get("shift") or "").strip()
        existing = RosterAssignment.query.filter_by(user_id=user.id, date=d).first()
        if existing:
            db.session.delete(existing)
        if code:
            shift = ShiftPattern.query.filter_by(code=code).first()
            if not shift:
                flash("Unknown shift code.", "error")
                return redirect(url_for("roster.roster"))
            db.session.add(RosterAssignment(
                user_id=user.id, date=d, shift_id=shift.id, created_by=current_user.id))
            record("roster_assigned", f"{user.id} {d} -> {shift.code}",
                   entity="roster", entity_id=user.id)
            flash(f"{user.name}: {d.isoformat()} set to {shift.code}.", "success")
            warn = _midday_outdoor_warning(get_settings(), shift, d)
            if warn:
                flash(warn, "warning")
        else:
            record("roster_cleared", f"{user.id} {d}", entity="roster",
                   entity_id=user.id)
            flash(f"{user.name}: {d.isoformat()} cleared.", "success")
        db.session.commit()
        return redirect(url_for("roster.roster", week=_monday(d).isoformat()))
    if not user or not d:
        abort(404)
    existing = RosterAssignment.query.filter_by(user_id=user.id, date=d).first()
    patterns = ShiftPattern.query.filter_by(is_active=True).order_by(ShiftPattern.code).all()
    return render_template("hr_roster_assign.html", user=user, day=d,
                           existing=existing, patterns=patterns)


# ---------------- Roster vs attendance variance ----------------

@roster_bp.route("/roster/variance")
@login_required
@_mgr_required
def roster_variance():
    day = _parse_date(request.args.get("date")) or date.today()
    assignments = RosterAssignment.query.filter_by(date=day).all()
    att = {a.user_id: a.status for a in AttendanceRecord.query.filter_by(date=day).all()}
    rostered_absent, present_unrostered, night_rostered = [], [], []
    users = {u.id: u for u in User.query.all()}
    rostered_ids = set()
    for a in assignments:
        rostered_ids.add(a.user_id)
        u = users.get(a.user_id)
        name = f"{u.name} ({u.id})" if u else a.user_id
        code = a.shift.code if a.shift else ""
        if code == "NIGHT":
            night_rostered.append({"employee": name, "shift": code})
        if att.get(a.user_id) == "absent":
            rostered_absent.append({"employee": name, "shift": code})
    for uid, status in att.items():
        if status in ("present", "late") and uid not in rostered_ids:
            u = users.get(uid)
            present_unrostered.append(
                {"employee": f"{u.name} ({u.id})" if u else uid, "status": status})
    return render_template("hr_roster_variance.html", day=day,
                           rostered_absent=rostered_absent,
                           present_unrostered=present_unrostered,
                           night_rostered=night_rostered)


# ---------------- Roster settings ----------------

@roster_bp.route("/roster/settings", methods=["GET", "POST"])
@login_required
@admin_required
def roster_settings():
    s = get_settings()
    if request.method == "POST":
        f = request.form
        s.ramadan_start = _parse_date(f.get("ramadan_start"))
        s.ramadan_end = _parse_date(f.get("ramadan_end"))
        try:
            s.ramadan_daily_hours = float(f.get("ramadan_daily_hours") or 6.0)
            s.standard_daily_hours = float(f.get("standard_daily_hours") or 8.0)
        except ValueError:
            pass
        s.midday_break_start = _parse_hhmm(f.get("midday_break_start")) or s.midday_break_start
        s.midday_break_end = _parse_hhmm(f.get("midday_break_end")) or s.midday_break_end
        for key in ("midday_season_start_md", "midday_season_end_md"):
            v = (f.get(key) or "").strip()
            if len(v) == 5 and v[2] == "-":
                setattr(s, key, v)
        record("roster_settings_updated", "Ramadan/midday settings updated",
               entity="roster_settings", entity_id="1")
        db.session.commit()
        flash("Roster settings saved.", "success")
        return redirect(url_for("roster.roster_settings"))
    return render_template("hr_roster_settings.html", s=s)
