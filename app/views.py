"""Employee-facing views: dashboard, leave, attendance, payslip, profile,
grievances, evaluations, approvals."""
import json
from datetime import date, datetime, timedelta
import calendar as calmod

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app import db
from app.models import (User, LeaveBalance, LeaveRequest, AttendanceRecord,
                        Grievance, Evaluation, AuditLog, RegularisationRequest)
from app.auth import log_action
from app.audit import record as audit_record
import config as app_config

views_bp = Blueprint("views", __name__)


def manager_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_manager:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def approver_required(fn):
    """v3.4: anyone who can plausibly hold an approval step — managers and
    admins plus users flagged as HR staff or HR manager."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not (current_user.is_manager or current_user.is_hr_staff
                or current_user.is_hr_manager):
            abort(403)
        return fn(*a, **kw)
    return wrapper


# ---------------- Dashboard (v3.8 rebuild) ----------------
@views_bp.route("/")
@login_required
def dashboard():
    """v3.8 home: role-based KPI strip, profile, week progress, today
    tracker ring, dark tasks card, accordions and a team-leave calendar.
    Every widget is fed by real queries — empty data renders "—" or an
    honest empty state, never decorative numbers."""
    from app.engagement import gst_now
    from app.ess_extras import inbox_tasks, dept_today, _overview_scope
    from app.hrms import get_upcoming_expiries
    from app.settingsutil import get_setting
    from app.models import (
        RosterAssignment, PunchRecord, EmployeeDocument, Certification,
        JDAcknowledgement, JobDescription, TrainingAssignment, TrainingAttempt,
        Notice, ApprovalAction, SalaryAssignment, LeaveBalance as _LB,
        RegularisationRequest as _RR, LetterRequest as _LR)
    from app.models_v35 import HelpdeskTicket

    u = current_user
    now_gst = gst_now()
    today = now_gst.date()
    month_start = today.replace(day=1)
    window_90 = today - timedelta(days=90)
    window_30dt = datetime.utcnow() - timedelta(days=30)
    month_tag = month_start.strftime("%b")

    is_hr = bool(u.is_admin or u.is_hr_staff or u.is_hr_manager)
    role = "admin" if is_hr else ("manager" if u.is_manager else "employee")
    greet_name = (u.name or u.id).split()[0]
    today_label = today.strftime("%A, %d %B %Y")

    # ---- scope: employee=self, manager=own+reports' departments, admin=all
    manager_depts = set()
    if is_hr:
        scope_ids = [x.id for x in User.query.filter_by(is_active=True).all()]
    elif u.is_manager:
        manager_depts = _overview_scope(u) or set()
        scope_ids = [x.id for x in User.query.filter(
            User.is_active.is_(True),
            User.department.in_(sorted(manager_depts))).all()
        ] if manager_depts else []
    else:
        scope_ids = [u.id]
    if not scope_ids:
        scope_ids = ["__none__"]  # keep .in_() valid; matches nothing

    # ---- KPI bar 1: attendance % (month, scope)
    att_month = AttendanceRecord.query.filter(
        AttendanceRecord.user_id.in_(scope_ids),
        AttendanceRecord.date >= month_start,
        AttendanceRecord.date <= today).all()
    work_days = [r for r in att_month
                 if r.status in ("present", "late", "absent")]
    worked_days = [r for r in work_days if r.status in ("present", "late")]
    att_pct = (round(len(worked_days) / len(work_days) * 100, 1)
               if work_days else None)

    # ---- KPI bar 2/3 (manager/admin): on-time % from punch records.
    # Late = an "in" punch past (shift start + grace) on a rostered day —
    # the same rule the punch endpoint uses to mark AttendanceRecord late.
    punches = PunchRecord.query.filter(
        PunchRecord.user_id.in_(scope_ids),
        PunchRecord.punch_type == "in",
        PunchRecord.local_date >= month_start,
        PunchRecord.local_date <= today).all()
    ras = RosterAssignment.query.filter(
        RosterAssignment.user_id.in_(scope_ids),
        RosterAssignment.date >= month_start,
        RosterAssignment.date <= today).all()
    shift_by = {(ra.user_id, ra.date): ra.shift for ra in ras
                if ra.shift and ra.shift.start_time}
    judged = late_n = 0
    for p in punches:
        sh = shift_by.get((p.user_id, p.local_date))
        if sh is None:
            continue
        try:
            shh, shm = map(int, sh.start_time.split(":"))
        except (ValueError, AttributeError):
            continue
        local = p.punched_at + timedelta(hours=4)  # punched_at is UTC
        judged += 1
        if (local.hour * 60 + local.minute
                > shh * 60 + shm + (sh.grace_minutes or 0)):
            late_n += 1
    ontime_pct = (round((judged - late_n) / judged * 100, 1)
                  if judged else None)

    # ---- KPI bar: leave decided % (requests created in the last 90 days)
    lrs = LeaveRequest.query.filter(
        LeaveRequest.user_id.in_(scope_ids),
        LeaveRequest.created_at >= datetime.combine(window_90,
                                                    datetime.min.time()),
        LeaveRequest.status.in_(("pending", "approved", "rejected"))).all()
    decided_n = sum(1 for r in lrs if r.status in ("approved", "rejected"))
    leave_decided_pct = (round(decided_n / len(lrs) * 100, 1) if lrs else None)

    # ---- KPI bar (admin): tickets closed %
    tickets_pct = None
    if role == "admin":
        all_tickets = HelpdeskTicket.query.all()
        closed_n = sum(1 for t in all_tickets
                       if t.status in ("resolved", "closed"))
        tickets_pct = (round(closed_n / len(all_tickets) * 100, 1)
                       if all_tickets else None)

    # ---- employee-only bars: annual leave used %, training completed %
    annual_bal = _LB.query.filter_by(user_id=u.id,
                                     leave_type="annual").first()
    annual_used_pct = (round(annual_bal.used / annual_bal.entitled * 100, 1)
                       if annual_bal and annual_bal.entitled else None)
    assigns = TrainingAssignment.query.filter_by(user_id=u.id).all()
    passed_courses = {a.course_id for a in TrainingAttempt.query.filter_by(
        user_id=u.id, passed=True).all()}
    passed_assigns = [a for a in assigns if a.course_id in passed_courses]
    training_pct = (round(len(passed_assigns) / len(assigns) * 100, 1)
                    if assigns else None)

    def _bar(label, pct):
        return {"label": label, "pct": pct,
                "display": f"{pct:g}%" if pct is not None else "—",
                "width": pct if pct is not None else 0}

    if role == "employee":
        kpi_bars = [_bar(f"My attendance · {month_tag}", att_pct),
                    _bar("Annual leave used", annual_used_pct),
                    _bar("Training completed", training_pct)]
    elif role == "manager":
        kpi_bars = [_bar(f"Team attendance · {month_tag}", att_pct),
                    _bar("On-time punches", ontime_pct),
                    _bar("Leave decided · 90d", leave_decided_pct)]
    else:
        kpi_bars = [_bar(f"Attendance · {month_tag}", att_pct),
                    _bar("On-time punches", ontime_pct),
                    _bar("Leave decided · 90d", leave_decided_pct),
                    _bar("Tickets closed", tickets_pct)]

    # ---- unified inbox (drives tasks card + approvals big number)
    tasks = inbox_tasks(u)
    n_appr = len(tasks["approvals"])
    n_not = len(tasks["notices"])
    n_jd = len(tasks["jds"])
    n_tr = len(tasks["training"])
    n_mine = len(tasks["mine"])

    # ---- big numbers
    present_today_n = db.session.query(
        func.count(func.distinct(AttendanceRecord.user_id))).filter(
        AttendanceRecord.user_id.in_(scope_ids),
        AttendanceRecord.date == today,
        AttendanceRecord.status.in_(("present", "late"))).scalar() or 0

    def _num(num, cap, icon, count=True, display=None):
        return {"num": num, "cap": cap, "icon": icon, "count": count,
                "display": str(num) if display is None else display}

    if role == "employee":
        annual_balance = ((annual_bal.entitled or 0) - (annual_bal.used or 0)
                          if annual_bal else None)
        kpi_nums = [
            _num(len(worked_days), f"Days present · {month_tag}", "check"),
            _num(annual_balance if annual_balance is not None else "—",
                 "Annual leave balance (days)", "calendar",
                 count=isinstance(annual_balance, int),
                 display=(f"{annual_balance:g}"
                          if annual_balance is not None else "—")),
            _num(n_mine, "Open requests", "clipboard"),
        ]
    elif role == "manager":
        dcard = dept_today(u)
        team_present = (max(dcard["staff"] - dcard["absent"]
                            - dcard["vacation"], 0) if dcard else 0)
        team_on_leave = dcard["vacation"] if dcard else 0
        kpi_nums = [
            _num(team_present, "Team present today", "users"),
            _num(team_on_leave, "On leave today", "calendar"),
            _num(n_appr, "Approvals waiting", "check"),
        ]
    else:
        active_n = User.query.filter_by(is_active=True).count()
        kpi_nums = [
            _num(active_n, "Active employees", "users"),
            _num(present_today_n, "Present today", "check"),
            _num(n_appr, "Pending approvals", "clipboard"),
        ]

    # ---- profile card (self only; salary chip only with an assignment)
    initials = "".join(w[0] for w in (u.name or u.id).split()[:2]).upper()
    has_salary = SalaryAssignment.query.filter(
        SalaryAssignment.user_id == u.id,
        SalaryAssignment.effective_from <= today).first() is not None
    profile = {
        "photo_url": url_for("docs.photo", uid=u.id) if u.photo_path else None,
        "initials": initials,
        "name": u.name,
        "sub": " · ".join(x for x in
                           [u.designation, u.department] if x) or "—",
        "salary_chip": ("AED {:,.2f} / month".format(u.monthly_salary or 0.0)
                        if has_salary else None),
        "status_chip": (u.employment_status or "active").replace("_", " "
                                                                       ).title(),
    }

    # ---- "This week" card: 7 Mon–Sun bars from AttendanceRecord
    mon = today - timedelta(days=today.weekday())
    sun = mon + timedelta(days=6)
    week_dates = [mon + timedelta(days=i) for i in range(7)]
    if role == "employee":
        recs = {r.date: r for r in AttendanceRecord.query.filter(
            AttendanceRecord.user_id == u.id,
            AttendanceRecord.date >= mon,
            AttendanceRecord.date <= sun).all()}

        def _hrs(r):
            try:
                if r and r.check_in and r.check_out:
                    a = datetime.strptime(r.check_in, "%H:%M")
                    b = datetime.strptime(r.check_out, "%H:%M")
                    return max((b - a).total_seconds() / 3600.0, 0.0)
            except (ValueError, TypeError):
                return 0.0
            return 0.0

        vals = [_hrs(recs.get(d)) for d in week_dates]
        total_min = int(round(sum(vals) * 60))
        week_total = f"{total_min // 60}h {total_min % 60:02d}m worked"
        fmt = lambda v: f"{v:.1f}h"  # noqa: E731
    else:
        counts = dict(db.session.query(
            AttendanceRecord.date,
            func.count(func.distinct(AttendanceRecord.user_id))).filter(
            AttendanceRecord.user_id.in_(scope_ids),
            AttendanceRecord.date >= mon, AttendanceRecord.date <= sun,
            AttendanceRecord.status.in_(("present", "late")))
            .group_by(AttendanceRecord.date).all())
        vals = [counts.get(d, 0) for d in week_dates]
        week_total = f"{sum(vals)} present-days this week"
        fmt = lambda v: f"{v:g}"  # noqa: E731
    maxv = max(vals) if vals else 0
    week_bars = [{"dow": d.strftime("%a"), "date": d.day,
                  "is_today": d == today,
                  "h": round(v / maxv * 100, 1) if maxv else 0,
                  "label": fmt(v) if maxv and v == maxv else None}
                 for d, v in zip(week_dates, vals)]
    week = {"bars": week_bars, "total": week_total}

    # ---- "Today" tracker ring
    punches_today = PunchRecord.query.filter_by(
        user_id=u.id, local_date=today)\
        .order_by(PunchRecord.punched_at).all()
    last_punch = punches_today[-1] if punches_today else None
    punched_in = bool(last_punch and last_punch.punch_type == "in")
    worked_min = 0.0
    open_in = None
    for p in punches_today:
        if p.punch_type == "in" and open_in is None:
            open_in = p.punched_at
        elif p.punch_type == "out" and open_in is not None:
            worked_min += max((p.punched_at - open_in).total_seconds()
                              / 60.0, 0.0)
            open_in = None
    if open_in is not None:  # still checked in — count up to now
        worked_min += max((datetime.utcnow() - open_in).total_seconds()
                          / 60.0, 0.0)

    RING_R = 54
    RING_C = 2 * 3.14159265358979 * RING_R
    if role == "employee":
        ra_today = RosterAssignment.query.filter_by(
            user_id=u.id, date=today).first()
        shift_min = shift_label = None
        if (ra_today and ra_today.shift and ra_today.shift.start_time
                and ra_today.shift.end_time):
            try:
                shh, shm = map(int, ra_today.shift.start_time.split(":"))
                ehh, ehm = map(int, ra_today.shift.end_time.split(":"))
                shift_min = (ehh * 60 + ehm) - (shh * 60 + shm)
                if shift_min <= 0:
                    shift_min += 24 * 60  # night shift crosses midnight
                shift_label = (f"{ra_today.shift.start_time} – "
                               f"{ra_today.shift.end_time}")
            except (ValueError, AttributeError):
                shift_min = shift_label = None
        frac = min(worked_min / shift_min, 1.0) if shift_min else 0.0
        center = f"{int(worked_min // 60)}:{int(worked_min % 60):02d}"
        cap = "worked today"
        if punched_in and shift_label:
            sub = f"Checked in · shift {shift_label}"
        elif shift_label:
            sub = f"Shift {shift_label}"
        else:
            sub = "No rostered shift today"
    else:
        expected = User.query.filter(
            User.id.in_(scope_ids), User.is_active.is_(True)).count()
        frac = (present_today_n / expected) if expected else 0.0
        center = f"{present_today_n}/{expected}"
        cap = "present"
        sub = "Team checked in today"
    tracker = {"ring_c": round(RING_C, 2),
               "ring_offset": round(RING_C * (1 - frac), 2),
               "pct": round(frac * 100, 1),
               "center": center, "cap": cap, "sub": sub,
               "date_label": today.strftime("%d %b")}

    punch_disabled = get_setting("mobile_checkin", "1") != "1"
    punch = {"url": url_for("engagement.punch_page"),
             "label": "Check Out" if punched_in else "Check In",
             "pulse": (not punched_in) and not punch_disabled,
             "disabled": punch_disabled}

    # ---- "My tasks" dark card: done/(done+open) over inbox items
    d_appr = ApprovalAction.query.filter(
        ApprovalAction.actor_id == u.id,
        ApprovalAction.action.in_(("approve", "reject")),
        ApprovalAction.created_at >= window_30dt).count()
    now_utc = datetime.utcnow()
    mand_q = Notice.query.filter_by(requires_ack=True).filter(
        (Notice.expires_at.is_(None)) | (Notice.expires_at > now_utc))
    if not u.is_admin:
        mand_q = mand_q.filter(
            (Notice.audience == "all")
            | (Notice.audience == (u.department or "")))
    d_not = max(mand_q.count() - n_not, 0)
    open_jd_ids = {j["jd"].id for j in tasks["jds"] if j.get("jd")}
    done_jd_ids = set()
    for ack in JDAcknowledgement.query.filter_by(user_id=u.id).all():
        jd = JobDescription.query.get(ack.jd_id)
        if (jd and jd.is_active and ack.status == "acknowledged"
                and ack.jd_version >= jd.version
                and ack.jd_id not in open_jd_ids):
            done_jd_ids.add(ack.jd_id)
    d_jd = len(done_jd_ids)
    d_tr = len(passed_assigns)
    d_mine = LeaveRequest.query.filter(
        LeaveRequest.user_id == u.id,
        LeaveRequest.status.in_(("approved", "rejected")),
        LeaveRequest.decided_at >= window_30dt).count()
    d_mine += _RR.query.filter(
        _RR.user_id == u.id,
        _RR.status.in_(("approved", "rejected")),
        _RR.decided_at >= window_30dt).count()
    d_mine += _LR.query.filter(
        _LR.user_id == u.id,
        _LR.status.in_(("ready", "delivered", "rejected")),
        _LR.updated_at >= window_30dt).count()
    done_n = d_appr + d_not + d_jd + d_tr + d_mine
    open_n = n_appr + n_not + n_jd + n_tr + n_mine
    tasks_pct = (round(done_n / (done_n + open_n) * 100, 1)
                 if (done_n + open_n) else None)

    def _plural(n, word):
        return f"{n} {word}" + ("" if n == 1 else "s")

    first_appr_link = (tasks["approvals"][0]["link"] if tasks["approvals"]
                       else url_for("views.approvals"))
    first_not_link = (url_for("engagement.notice_view",
                              nid=tasks["notices"][0].id) if tasks["notices"]
                      else url_for("engagement.notifications"))
    task_rows = [
        {"icon": "check", "title": "Approvals awaiting me",
         "sub": (_plural(n_appr, "item") + " waiting on your decision"
                 if n_appr else "Nothing waiting — you're all caught up"),
         "open_n": n_appr, "link": first_appr_link},
        {"icon": "bell", "title": "Mandatory notices to read",
         "sub": (_plural(n_not, "notice") + " need your acknowledgement"
                 if n_not else "All notices acknowledged"),
         "open_n": n_not, "link": first_not_link},
        {"icon": "file", "title": "JD to sign",
         "sub": (_plural(n_jd, "job description") + " awaiting signature"
                 if n_jd else "All job descriptions signed"),
         "open_n": n_jd, "link": url_for("engagement.my_jd")},
        {"icon": "graduation", "title": "Training to pass",
         "sub": (_plural(n_tr, "course") + " assigned to you"
                 if n_tr else "All assigned training complete"),
         "open_n": n_tr, "link": url_for("engagement.my_training")},
        {"icon": "clipboard", "title": "My pending requests",
         "sub": (_plural(n_mine, "request") + " in flight"
                 if n_mine else "No requests in flight"),
         "open_n": n_mine, "link": url_for("extras.inbox")},
    ]
    visa20 = []
    if role == "admin":
        visa20 = get_upcoming_expiries(20)
        task_rows.append(
            {"icon": "idcard", "title": "Visa expiries · 20 days",
             "sub": (_plural(len(visa20), "document")
                     + " expiring soon" if visa20
                     else "No documents expiring in the next 20 days"),
             "open_n": len(visa20), "link": url_for("hrms.compliance")})
    tasks_card = {"pct": tasks_pct,
                  "pct_display": f"{tasks_pct:g}%" if tasks_pct is not None
                  else "—",
                  "done": done_n, "open": open_n,
                  "rows": task_rows}

    # ---- accordions (left column)
    accordions = []
    if role == "employee":
        bal_rows = []
        for b in _LB.query.filter_by(user_id=u.id).all():
            if b.leave_type == "sick":
                continue  # v3.3: sick balance hidden from employees
            label = app_config.LEAVE_TYPES.get(
                b.leave_type, {}).get("label", b.leave_type)
            bal_rows.append({"t": label,
                             "s": f"{(b.entitled or 0) - (b.used or 0):g} "
                                  "days remaining"})
        accordions.append({"title": "My leave balances", "icon": "calendar",
                           "rows": bal_rows,
                           "empty": "No leave balances on file",
                           "view_all": {"label": "View all",
                                        "url": url_for("views.leave")}})
        doc_rows = []
        for d in EmployeeDocument.query.filter_by(
                user_id=u.id, superseded=False).all():
            days_left = ((d.expiry_date - today).days
                         if d.expiry_date else None)
            doc_rows.append({
                "t": d.title,
                "s": (f"Expires {d.expiry_date.strftime('%d %b %Y')} · "
                      f"{days_left}d left" if days_left is not None
                      else "No expiry date"),
                "warn": days_left is not None and days_left <= 60})
        doc_rows.sort(key=lambda r: (not r["warn"], r["t"]))
        accordions.append({"title": "My documents", "icon": "file",
                           "rows": doc_rows,
                           "empty": "No documents on file",
                           "view_all": {"label": "View all",
                                        "url": url_for("docs.my_documents")}})
        pay_rows = []
        yy, mm = today.year, today.month
        for _ in range(3):
            pay_rows.append({
                "t": f"{calmod.month_name[mm]} {yy}",
                "s": "Salary slip",
                "link": url_for("views.payslip", year=yy, month=mm),
                "link_label": "View"})
            mm -= 1
            if mm == 0:
                mm, yy = 12, yy - 1
        accordions.append({"title": "My payslips", "icon": "money",
                           "rows": pay_rows,
                           "empty": "No payslips yet",
                           "view_all": {"label": "View all",
                                        "url": url_for("views.payslips")}})
    else:
        exp = get_upcoming_expiries(60)
        if role == "manager":
            exp = [e for e in exp
                   if (e["user"].department or "") in manager_depts]
        visa_rows = [{
            "t": e["user"].name,
            "s": (f"{e['doc'].title()} · expires "
                  f"{e['date'].strftime('%d %b %Y')} · "
                  f"{e['days_left']}d left"),
            "warn": True,
            "initials": "".join(
                w[0] for w in (e["user"].name or "?").split()[:2]).upper()}
            for e in exp[:15]]
        accordions.append({"title": "Expiring visas", "icon": "idcard",
                           "rows": visa_rows,
                           "empty": "No documents expiring in 60 days",
                           "view_all": {"label": "View all",
                                        "url": url_for("hrms.compliance")}})
        cert_q = Certification.query.filter(
            Certification.expiry_date.isnot(None),
            Certification.expiry_date >= today - timedelta(days=30),
            Certification.expiry_date <= today + timedelta(days=60))
        if role == "manager":
            cert_q = cert_q.filter(Certification.user_id.in_(scope_ids))
        certs = cert_q.order_by(Certification.expiry_date).limit(15).all()
        cert_users = {x.id: x for x in User.query.filter(
            User.id.in_({c.user_id for c in certs})).all()} if certs else {}
        cert_rows = []
        for c in certs:
            cu = cert_users.get(c.user_id)
            days_left = (c.expiry_date - today).days
            cert_rows.append({
                "t": c.name,
                "s": (f"{cu.name if cu else c.user_id} · expires "
                      f"{c.expiry_date.strftime('%d %b %Y')} · "
                      f"{days_left}d left"),
                "warn": days_left <= 60})
        accordions.append({"title": "Expiring certifications",
                           "icon": "medal",
                           "rows": cert_rows,
                           "empty": "No certifications expiring in 60 days",
                           "view_all": {"label": "View all",
                                        "url": url_for(
                                            "retail.certifications")}})
        open_tickets = HelpdeskTicket.query.filter(
            HelpdeskTicket.status.in_(("open", "in_progress"))).order_by(
            HelpdeskTicket.created_at.desc()).limit(10).all()
        req_users = {x.id: x for x in User.query.filter(
            User.id.in_({t.requester_id for t in open_tickets})).all()
        } if open_tickets else {}
        ticket_rows = [{
            "t": t.subject,
            "s": (f"#{t.id} · "
                  f"{req_users.get(t.requester_id).name if req_users.get(t.requester_id) else t.requester_id} · "
                  f"{t.status.replace('_', ' ')}"),
            "link": url_for("helpdesk.hr_ticket_detail", ticket_id=t.id),
            "link_label": "Open"} for t in open_tickets]
        accordions.append({"title": "Open tickets", "icon": "chat",
                           "rows": ticket_rows,
                           "empty": "No open tickets",
                           "view_all": {"label": "View all",
                                        "url": url_for(
                                            "helpdesk.hr_tickets")}})

    # ---- calendar: current week Mon–Sat, approved-leave chips
    sat = mon + timedelta(days=5)
    pm, py = (12, today.year - 1) if today.month == 1 else (today.month - 1,
                                                             today.year)
    nm, ny = (1, today.year + 1) if today.month == 12 else (today.month + 1,
                                                           today.year)
    month_chips = [calmod.month_name[pm], calmod.month_name[today.month],
                   calmod.month_name[nm]]
    if role == "employee":
        cal_ids = ([x.id for x in User.query.filter_by(
            is_active=True, department=u.department).all()]
            if u.department else [u.id])
    else:
        cal_ids = scope_ids
    if not cal_ids:
        cal_ids = ["__none__"]
    cal_leaves = LeaveRequest.query.filter(
        LeaveRequest.user_id.in_(cal_ids),
        LeaveRequest.status == "approved",
        LeaveRequest.start_date <= sat,
        LeaveRequest.end_date >= mon).all()
    cal_users = {x.id: x for x in User.query.filter(
        User.id.in_({l.user_id for l in cal_leaves})).all()
    } if cal_leaves else {}
    cal_days = []
    for i in range(6):
        d = mon + timedelta(days=i)
        chips = []
        for l in cal_leaves:
            if l.start_date <= d <= l.end_date:
                cu = cal_users.get(l.user_id)
                nm_ = cu.name if cu else l.user_id
                chips.append({
                    "initials": "".join(
                        w[0] for w in nm_.split()[:2]).upper(),
                    "name": nm_})
        chips.sort(key=lambda c: c["name"])
        cal_days.append({"dow": d.strftime("%a"), "date": d.day,
                         "is_today": d == today, "chips": chips})
    calendar = {"months": month_chips, "days": cal_days,
                "has": any(d["chips"] for d in cal_days),
                "url": url_for("extras.team_calendar")}

    return render_template(
        "dashboard.html",
        role=role, greet_name=greet_name, today_label=today_label,
        dept_line=" · ".join(x for x in
                             [u.designation, u.department] if x),
        kpi_bars=kpi_bars, kpi_nums=kpi_nums,
        profile=profile, week=week, tracker=tracker, punch=punch,
        tasks_card=tasks_card, accordions=accordions, calendar=calendar)

# ---------------- Leave ----------------
@views_bp.route("/leave", methods=["GET", "POST"])
@login_required
def leave():
    if request.method == "POST":
        ltype = request.form.get("leave_type", "")
        start = request.form.get("start_date", "")
        end = request.form.get("end_date", "")
        reason = (request.form.get("reason") or "").strip()[:1000]
        if ltype not in app_config.LEAVE_TYPES:
            flash("Invalid leave type.", "error")
        else:
            try:
                s = datetime.strptime(start, "%Y-%m-%d").date()
                e = datetime.strptime(end, "%Y-%m-%d").date()
            except ValueError:
                s = e = None
            if not s or not e or e < s:
                flash("Invalid date range.", "error")
            elif s < date.today():
                flash("Leave cannot start in the past.", "error")
            else:
                days = (e - s).days + 1
                bal = LeaveBalance.query.filter_by(
                    user_id=current_user.id, leave_type=ltype).first()
                cfg_entitled = app_config.LEAVE_TYPES[ltype]["entitled"]
                # v3.4.1: the employee's own balance row is authoritative
                # when present (pro-rated entitlements), else the config.
                entitled = (bal.entitled if bal and bal.entitled is not None
                            else cfg_entitled)
                used = bal.used if bal else 0
                # v3.4.1: pending requests of the same type count against
                # the balance, so two pending requests cannot together
                # exceed the entitlement (double-spend guard).
                pending_days = sum(
                    r.days for r in LeaveRequest.query.filter_by(
                        user_id=current_user.id, leave_type=ltype,
                        status="pending").all())
                if entitled > 0 and used + pending_days + days > entitled:
                    flash(f"Insufficient {app_config.LEAVE_TYPES[ltype]['label']} balance "
                          f"(used {used}, {pending_days} day(s) pending approval).",
                          "error")
                else:
                    req = LeaveRequest(user_id=current_user.id, leave_type=ltype,
                                       start_date=s, end_date=e, days=days, reason=reason)
                    db.session.add(req)
                    log_action(current_user.id, "leave_applied",
                               f"{ltype} {s} to {e} ({days}d)")
                    db.session.commit()
                    # v3.4: start the approval-matrix chain for this request.
                    from app.approvals import start_instance
                    start_instance("leave", req.id, current_user.id,
                                   current_user.id)
                    flash("Leave request submitted.", "success")
                    return redirect(url_for("views.leave"))
    balances = LeaveBalance.query.filter_by(user_id=current_user.id).all()
    requests = LeaveRequest.query.filter_by(user_id=current_user.id)\
        .order_by(LeaveRequest.created_at.desc()).limit(20).all()
    # v3.4: per-request approval-chain progress for the employee's own list.
    from app.approvals import get_instance, step_badge
    badges = {}
    for r in requests:
        if r.status == "pending":
            inst = get_instance("leave", r.id)
            if inst:
                badges[r.id] = step_badge(inst)
    return render_template("leave.html", balances=balances, requests=requests,
                           leave_types=app_config.LEAVE_TYPES, badges=badges)


@views_bp.route("/leave/<int:req_id>/cancel", methods=["POST"])
@login_required
def cancel_leave(req_id):
    req = LeaveRequest.query.get_or_404(req_id)
    if req.user_id != current_user.id or req.status != "pending":
        abort(403)
    req.status = "cancelled"
    log_action(current_user.id, "leave_cancelled", f"request #{req.id}")
    db.session.commit()
    flash("Leave request cancelled.", "success")
    return redirect(url_for("views.leave"))


# ---------------- Attendance ----------------
@views_bp.route("/attendance")
@login_required
def attendance():
    today = date.today()
    # v3.6: month can come from a date/month picker (ym=YYYY-MM) or the
    # legacy y/m params.
    ym = (request.args.get("ym") or "").strip()
    if ym:
        try:
            y, m = ym.split("-")
            view_date = date(int(y), int(m), 1)
        except (ValueError, TypeError):
            view_date = today.replace(day=1)
    else:
        try:
            year = int(request.args.get("y", today.year))
            month = int(request.args.get("m", today.month))
            view_date = date(year, month, 1)
        except ValueError:
            view_date = today.replace(day=1)
    month_start = view_date
    month_end = date(view_date.year, view_date.month,
                     calmod.monthrange(view_date.year, view_date.month)[1])
    records = AttendanceRecord.query.filter(
        AttendanceRecord.user_id == current_user.id,
        AttendanceRecord.date >= month_start,
        AttendanceRecord.date <= month_end).all()
    by_date = {r.date: r for r in records}
    weeks = calmod.monthcalendar(view_date.year, view_date.month)
    prev_m = month_start - timedelta(days=1)
    next_m = (month_end + timedelta(days=1)).replace(day=1)
    # v3.3: recent geo/selfie punches for this month.
    from app.models import PunchRecord, WorkLocation
    # v3.6: punch-type filter (check-in / check-out).
    punch = (request.args.get("punch") or "").strip()
    if punch not in ("in", "out"):
        punch = ""
    pq = PunchRecord.query.filter(
        PunchRecord.user_id == current_user.id,
        PunchRecord.local_date >= month_start,
        PunchRecord.local_date <= month_end)
    if punch:
        pq = pq.filter(PunchRecord.punch_type == punch)
    punches = pq.order_by(PunchRecord.punched_at.desc()).limit(60).all()
    locations = {l.id: l for l in WorkLocation.query.all()}
    return render_template("attendance.html", weeks=weeks, by_date=by_date,
                           view_date=view_date, prev_m=prev_m, next_m=next_m,
                           today=today, punches=punches, locations=locations,
                           punch=punch)


# ---------------- Payslip ----------------
def build_payslip(user, year, month):
    basic = user.basic_salary or 0.0
    gross = user.monthly_salary or 0.0
    allowances = max(gross - basic, 0.0)
    deductions = 0.0
    net = gross - deductions
    return {
        "year": year, "month": month,
        "month_name": calmod.month_name[month],
        "earnings": [("Basic Salary", basic), ("Allowances", allowances)],
        "deductions": [("Total Deductions", deductions)],
        "gross": gross, "total_deductions": deductions, "net": net,
    }


@views_bp.route("/payslip")
@login_required
def payslips():
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        months.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return render_template("payslips.html", months=months)


@views_bp.route("/payslip/<int:year>/<int:month>")
@login_required
def payslip(year, month):
    if not (1 <= month <= 12) or year < 2020 or year > date.today().year:
        abort(404)
    slip = build_payslip(current_user, year, month)
    run = None
    run_slip = None
    from app.models import PayrollRun, PayrollLine
    period = f"{year:04d}-{month:02d}"
    run = PayrollRun.query.filter_by(period=period, status="approved").first()
    if run:
        line = PayrollLine.query.filter_by(run_id=run.id,
                                            user_id=current_user.id).first()
        if line:
            import json as _json
            run_slip = {
                "period": period, "user": current_user,
                "payable_days": line.payable_days,
                "lop_days": line.lop_days,
                "basic_earned": line.basic_earned,
                "earnings": _json.loads(line.earnings_json),
                "deductions": _json.loads(line.deductions_json),
                "ot": _json.loads(line.ot_json),
                "gross": line.gross,
                "total_deductions": line.total_deductions,
                "net": line.net,
            }
    return render_template("payslip.html", slip=slip, run=run, run_slip=run_slip)


# ---------------- Profile ----------------
@views_bp.route("/profile")
@login_required
def profile():
    from app.uae import gratuity
    from app.models import Certification, PPEIssue, Deployment, Project, AssetCustody
    est = None
    if current_user.joining_date and (current_user.basic_salary or 0) > 0:
        est = gratuity(current_user.joining_date, date.today(),
                       current_user.basic_salary, "employer")
    my_certs = Certification.query.filter_by(
        user_id=current_user.id).order_by(Certification.expiry_date).all()
    my_ppe = PPEIssue.query.filter_by(
        user_id=current_user.id).order_by(PPEIssue.issued_date.desc()).all()
    my_assets = AssetCustody.query.filter_by(
        user_id=current_user.id).order_by(
        AssetCustody.returned_date.isnot(None),
        AssetCustody.issued_date.desc()).all()
    today = date.today()
    my_deps = (Deployment.query.filter(
        Deployment.user_id == current_user.id,
        (Deployment.to_date.is_(None)) | (Deployment.to_date >= today))
        .order_by(Deployment.from_date.desc()).all())
    dep_projects = {p.id: p for p in Project.query.all()}
    return render_template("profile.html", gratuity_est=est, my_certs=my_certs,
                           my_ppe=my_ppe, my_deps=my_deps, my_assets=my_assets,
                           dep_projects=dep_projects)


@views_bp.route("/profile/celebrations", methods=["POST"])
@login_required
def profile_celebrations():
    """v3.4.1: employee toggles birthday/anniversary feed sharing."""
    share = request.form.get("share_celebrations") == "on"
    current_user.share_celebrations = share
    log_action(current_user.id, "celebration_pref_updated",
               f"share_celebrations={share}")
    db.session.commit()
    flash("Celebration feed preference saved.", "success")
    return redirect(url_for("views.profile"))


# ---------------- My End-of-Service Estimate (employee) ----------------
@views_bp.route("/gratuity")
@login_required
def my_gratuity():
    """Employee-facing end-of-service estimate (employer-termination basis)."""
    from app.uae import gratuity
    est = None
    if current_user.joining_date and (current_user.basic_salary or 0) > 0:
        est = gratuity(current_user.joining_date, date.today(),
                       current_user.basic_salary, "employer")
    return render_template("my_gratuity.html", est=est)


# ---------------- Grievances ----------------
@views_bp.route("/grievances", methods=["GET", "POST"])
@login_required
def grievances():
    if request.method == "POST":
        category = request.form.get("category", "")
        subject = (request.form.get("subject") or "").strip()[:200]
        description = (request.form.get("description") or "").strip()[:5000]
        anonymous = request.form.get("anonymous") == "on"
        if category not in app_config.GRIEVANCE_CATEGORIES or not subject or not description:
            flash("Please complete category, subject and description.", "error")
        else:
            g = Grievance(user_id=current_user.id, category=category,
                          subject=subject, description=description,
                          is_anonymous=anonymous)
            db.session.add(g)
            log_action(current_user.id, "grievance_raised", f"{category}: {subject[:60]}")
            db.session.commit()
            flash("Grievance submitted. HR will review it confidentially.", "success")
            return redirect(url_for("views.grievances"))
    items = Grievance.query.filter_by(user_id=current_user.id)\
        .order_by(Grievance.created_at.desc()).all()
    return render_template("grievances.html", items=items,
                           categories=app_config.GRIEVANCE_CATEGORIES)


@views_bp.route("/grievances/<int:gid>")
@login_required
def grievance_detail(gid):
    g = Grievance.query.get_or_404(gid)
    if g.user_id != current_user.id and not current_user.is_manager:
        abort(403)
    return render_template("grievance_detail.html", g=g)


# ---------------- Evaluations (employee view) ----------------
@views_bp.route("/evaluations")
@login_required
def evaluations():
    items = Evaluation.query.filter_by(user_id=current_user.id)\
        .order_by(Evaluation.created_at.desc()).all()
    for e in items:
        e.scores = json.loads(e.scores_json)
    return render_template("evaluations.html", items=items)


@views_bp.route("/evaluations/<int:eid>/acknowledge", methods=["POST"])
@login_required
def acknowledge_evaluation(eid):
    e = Evaluation.query.get_or_404(eid)
    if e.user_id != current_user.id:
        abort(403)
    e.acknowledged = True
    log_action(current_user.id, "evaluation_acknowledged", f"evaluation #{e.id}")
    db.session.commit()
    flash("Evaluation acknowledged.", "success")
    return redirect(url_for("views.evaluations"))


# ---------------- Approvals (v3.4: unified matrix-driven inbox) ----------------
@views_bp.route("/approvals")
@login_required
@approver_required
def approvals():
    from app.approvals import (get_or_start_instance, can_act, step_badge,
                               inbox_for)
    from app.models import RegularisationRequest
    # v3.6: process-type + status filters (GET params). Default status
    # "pending" preserves the original inbox behaviour exactly; the
    # "approved"/"rejected" options list decided leave requests
    # (other chains only exist in a pending state).
    process = (request.args.get("process") or "all").strip()
    if process not in ("all", "leave", "regularisation", "employee_creation",
                       "expense", "shift_swap"):
        process = "all"
    status = (request.args.get("status") or "pending").strip()
    if status not in ("pending", "approved", "rejected"):
        status = "pending"
    # --- leave section (legacy default 1-step behaviour preserved)
    q = LeaveRequest.query.filter_by(status=status)
    if not current_user.is_admin:
        # Non-admins see requests from their own department only, and only
        # the ones sitting on their own approval step.
        dept_users = [u.id for u in User.query.filter_by(
            department=current_user.department, is_active=True).all()]
        q = q.filter(LeaveRequest.user_id.in_(dept_users))
    leave_items = []
    for r in q.order_by(LeaveRequest.created_at.desc()).all():
        inst = get_or_start_instance("leave", r.id, r.user_id, r.user_id)
        item_can_act = can_act(inst, current_user)
        if current_user.is_admin or item_can_act:
            leave_items.append({"req": r, "instance": inst,
                                "badge": step_badge(inst),
                                "can_act": item_can_act})
    users = {u.id: u for u in User.query.filter(
        User.id.in_([i["req"].user_id for i in leave_items])).all()} \
        if leave_items else {}
    # --- unified inbox: other chains awaiting this user (pending only)
    reg_items, creation_items, expense_items, swap_items = [], [], [], []
    if status == "pending":
        for inst in inbox_for(current_user):
            if inst.process_key == "regularisation":
                rr = RegularisationRequest.query.get(int(inst.ref_id))
                if rr is not None and rr.status == "pending":
                    reg_items.append({"req": rr, "instance": inst,
                                      "badge": step_badge(inst)})
            elif inst.process_key == "employee_creation":
                u = User.query.get(inst.ref_id)
                if u is not None and u.employment_status == "pending_approval":
                    creation_items.append({"user": u, "instance": inst,
                                           "badge": step_badge(inst)})
            elif inst.process_key == "expense":
                from app.models import ExpenseClaim
                claim = ExpenseClaim.query.get(int(inst.ref_id))
                if claim is not None and claim.status == "pending":
                    expense_items.append({"claim": claim, "instance": inst,
                                          "badge": step_badge(inst)})
            elif inst.process_key == "shift_swap":
                from app.models import ShiftSwapRequest
                from app.helpdesk import rest_gap_flags
                swap = ShiftSwapRequest.query.get(int(inst.ref_id))
                if swap is not None and swap.status == "pending_approval":
                    swap_items.append({"swap": swap, "instance": inst,
                                       "badge": step_badge(inst),
                                       "flags": rest_gap_flags(swap)})
    # --- shift swaps: names + pattern labels for the table
    swap_user_ids, swap_pattern_ids = set(), set()
    for it in swap_items:
        s = it["swap"]
        swap_user_ids.update([s.requester_id, s.counterpart_user_id])
        if s.my_pattern_id:
            swap_pattern_ids.add(s.my_pattern_id)
        if s.their_pattern_id:
            swap_pattern_ids.add(s.their_pattern_id)
    from app.models import ShiftPattern as _ShiftPattern
    swap_users = {u.id: u for u in User.query.filter(
        User.id.in_(swap_user_ids)).all()} if swap_user_ids else {}
    swap_patterns = {p.id: p for p in _ShiftPattern.query.filter(
        _ShiftPattern.id.in_(swap_pattern_ids)).all()} \
        if swap_pattern_ids else {}
    return render_template("approvals.html",
                           items=[i["req"] for i in leave_items],
                           meta={i["req"].id: i for i in leave_items},
                           users=users, leave_types=app_config.LEAVE_TYPES,
                           process=process, status=status,
                           reg_items=reg_items,
                           creation_items=creation_items,
                           expense_items=expense_items,
                           swap_items=swap_items,
                           swap_users=swap_users,
                           swap_patterns=swap_patterns)


def _leave_approve_side_effects(req, comment):
    """Final-approval side-effects for leave (balance, status, outbox)."""
    req.status = "approved"
    bal = LeaveBalance.query.filter_by(
        user_id=req.user_id, leave_type=req.leave_type).first()
    if bal:
        bal.used += req.days
    else:
        entitled = app_config.LEAVE_TYPES.get(req.leave_type, {}).get("entitled", 0)
        db.session.add(LeaveBalance(user_id=req.user_id,
                                    leave_type=req.leave_type,
                                    entitled=entitled, used=req.days))
    req.approver_id = current_user.id
    req.approver_comment = comment
    req.decided_at = datetime.utcnow()
    log_action(current_user.id, f"leave_{req.status}",
               f"request #{req.id} ({req.user_id})")
    from app.models import NotificationOutbox
    db.session.add(NotificationOutbox(
        user_id=req.user_id, channel="whatsapp",
        event=f"leave_{req.status}",
        message=f"Your leave request ({app_config.LEAVE_TYPES.get(req.leave_type, {}).get('label', req.leave_type)}, "
                f"{req.start_date} to {req.end_date}) has been {req.status}.",
        status="queued"))


def _leave_reject_side_effects(req, comment):
    req.status = "rejected"
    req.approver_id = current_user.id
    req.approver_comment = comment
    req.decided_at = datetime.utcnow()
    log_action(current_user.id, f"leave_{req.status}",
               f"request #{req.id} ({req.user_id})")
    from app.models import NotificationOutbox
    db.session.add(NotificationOutbox(
        user_id=req.user_id, channel="whatsapp",
        event=f"leave_{req.status}",
        message=f"Your leave request ({app_config.LEAVE_TYPES.get(req.leave_type, {}).get('label', req.leave_type)}, "
                f"{req.start_date} to {req.end_date}) has been {req.status}.",
        status="queued"))


@views_bp.route("/approvals/<int:req_id>", methods=["POST"])
@login_required
@approver_required
def decide_leave(req_id):
    from app.approvals import get_or_start_instance, act, self_blocked
    req = LeaveRequest.query.get_or_404(req_id)
    if req.status != "pending":
        flash("Request already decided.", "error")
        return redirect(url_for("views.approvals"))
    instance = get_or_start_instance("leave", req.id, req.user_id,
                                     req.user_id)
    action = request.form.get("action")
    comment = (request.form.get("comment") or "").strip()[:500]
    if action not in ("approve", "reject"):
        abort(400)
    # v3.4.1: re-validate the balance at the FINAL step before the chain
    # advances — a request must never overdraw, even if earlier steps were
    # approved while the balance was still intact.
    if action == "approve" and instance.current_step == instance.total_steps \
            and not instance.exhausted:
        bal = LeaveBalance.query.filter_by(
            user_id=req.user_id, leave_type=req.leave_type).first()
        cfg_entitled = app_config.LEAVE_TYPES.get(req.leave_type, {}).get(
            "entitled", 0)
        entitled = (bal.entitled if bal and bal.entitled is not None
                    else cfg_entitled)
        used = bal.used if bal else 0
        if entitled > 0 and used + req.days > entitled:
            flash("Insufficient remaining balance — request stays pending.",
                  "error")
            return redirect(url_for("views.approvals"))
    try:
        outcome = act(instance, current_user, action == "approve", comment)
    except PermissionError as e:
        if self_blocked(instance, current_user) and \
                "own request" in str(e):
            flash("You cannot approve your own request.", "error")
            return redirect(url_for("views.approvals"))
        abort(403)
    if outcome == "rejected":
        _leave_reject_side_effects(req, comment)
        flash("Leave rejected.", "success")
    elif outcome == "final_approved":
        _leave_approve_side_effects(req, comment)
        flash("Leave approved.", "success")
    else:
        flash("Approved at this step; forwarded to the next approver.",
              "success")
    db.session.commit()
    return redirect(url_for("views.approvals"))


# ---------------- Evaluations (manager/admin create) ----------------
@views_bp.route("/evaluations/manage")
@login_required
@manager_required
def evaluations_manage():
    q = User.query.filter_by(is_active=True)
    if not current_user.is_admin:
        q = q.filter_by(department=current_user.department)
    employees = q.order_by(User.name).all()
    recent = Evaluation.query.order_by(Evaluation.created_at.desc()).limit(20).all()
    users = {u.id: u for u in User.query.all()}
    return render_template("evaluations_manage.html", employees=employees,
                           recent=recent, users=users)


@views_bp.route("/evaluations/new/<uid>", methods=["GET", "POST"])
@login_required
@manager_required
def evaluation_new(uid):
    target = User.query.get_or_404(uid)
    if not target.is_active:
        abort(404)
    if not current_user.is_admin and target.department != current_user.department:
        abort(403)
    if request.method == "POST":
        period = (request.form.get("period") or "").strip()[:40] or f"{date.today().year} H2"
        remarks = (request.form.get("remarks") or "").strip()[:2000]
        scores = []
        total = 0
        for i, criterion in enumerate(app_config.EVAL_CRITERIA):
            try:
                rating = int(request.form.get(f"rating_{i}", "0"))
            except ValueError:
                rating = 0
            comment_choice = request.form.get(f"comment_{i}", "")
            comment_text = (request.form.get(f"comment_text_{i}") or "").strip()[:500]
            if rating < 1 or rating > 5:
                flash(f"Rating required for: {criterion} (1-5).", "error")
                return render_template("evaluation_form.html", target=target,
                                       criteria=app_config.EVAL_CRITERIA,
                                       options=app_config.EVAL_COMMENT_OPTIONS)
            if comment_choice not in app_config.EVAL_COMMENT_OPTIONS:
                comment_choice = "Meets Expectations"
            scores.append({"criterion": criterion, "rating": rating,
                           "comment": comment_choice, "note": comment_text})
            total += rating
        overall = round(total / len(scores), 2)
        ev = Evaluation(user_id=target.id, period=period,
                        evaluator_id=current_user.id,
                        scores_json=json.dumps(scores),
                        overall=overall, remarks=remarks)
        db.session.add(ev)
        log_action(current_user.id, "evaluation_created",
                   f"{target.id} period {period} overall {overall}")
        db.session.commit()
        flash("Evaluation saved.", "success")
        return redirect(url_for("views.evaluations_manage"))
    return render_template("evaluation_form.html", target=target,
                           criteria=app_config.EVAL_CRITERIA,
                           options=app_config.EVAL_COMMENT_OPTIONS)


# ---------------- Grievance inbox (manager/admin) ----------------
@views_bp.route("/grievances/inbox", methods=["GET", "POST"])
@login_required
@manager_required
def grievance_inbox():
    if request.method == "POST":
        gid = int(request.form.get("gid", 0))
        g = Grievance.query.get_or_404(gid)
        new_status = request.form.get("status", "")
        resolution = (request.form.get("resolution") or "").strip()[:2000]
        if new_status in ("submitted", "under_review", "in_progress", "resolved", "closed"):
            g.status = new_status
            g.resolution = resolution
            log_action(current_user.id, "grievance_updated",
                       f"#{g.id} -> {new_status}")
            db.session.commit()
            flash("Grievance updated.", "success")
        return redirect(url_for("views.grievance_inbox"))
    items = Grievance.query.order_by(Grievance.created_at.desc()).all()
    users = {u.id: u for u in User.query.all()}
    return render_template("grievance_inbox.html", items=items, users=users)


# ---------------- Attendance Regularisation (ESS + MSS) ----------------
@views_bp.route("/regularisation", methods=["GET", "POST"])
@login_required
def regularisation():
    """Employee: request a correction to an attendance in/out entry."""
    if request.method == "POST":
        d_raw = request.form.get("date", "")
        cin = (request.form.get("corrected_in") or "").strip()
        cout = (request.form.get("corrected_out") or "").strip()
        reason = (request.form.get("reason") or "").strip()[:500]
        try:
            d = datetime.strptime(d_raw, "%Y-%m-%d").date()
        except ValueError:
            d = None
        if not d:
            flash("Invalid date.", "error")
        elif d > date.today():
            flash("Regularisation cannot be for a future date.", "error")
        elif not reason:
            flash("A reason is required.", "error")
        elif RegularisationRequest.query.filter_by(
                user_id=current_user.id, date=d, status="pending").first():
            flash("A pending request already exists for this date.", "error")
        else:
            rr = RegularisationRequest(user_id=current_user.id, date=d,
                                       corrected_in=cin or None,
                                       corrected_out=cout or None, reason=reason)
            db.session.add(rr)
            log_action(current_user.id, "regularisation_applied",
                       f"{d} in={cin or '-'} out={cout or '-'}")
            db.session.commit()
            # v3.4: start the approval-matrix chain for this request.
            from app.approvals import start_instance
            start_instance("regularisation", rr.id, current_user.id,
                           current_user.id)
            flash("Regularisation request submitted.", "success")
            return redirect(url_for("views.regularisation"))
    items = RegularisationRequest.query.filter_by(user_id=current_user.id)\
        .order_by(RegularisationRequest.date.desc()).all()
    return render_template("regularisation.html", items=items)


@views_bp.route("/regularisation/approve", methods=["GET", "POST"])
@login_required
@approver_required
def regularisation_approvals():
    """Matrix-driven approval list for regularisation requests (v3.4)."""
    from app.approvals import (get_or_start_instance, act, can_act,
                               step_badge, self_blocked)
    q = RegularisationRequest.query.filter_by(status="pending")
    if request.method == "POST":
        rid = int(request.form.get("rid", 0))
        rr = RegularisationRequest.query.get_or_404(rid)
        if rr.status != "pending":
            flash("Request already decided.", "error")
            return redirect(url_for("views.regularisation_approvals"))
        instance = get_or_start_instance("regularisation", rr.id,
                                         rr.user_id, rr.user_id)
        action = request.form.get("action")
        comment = (request.form.get("comment") or "").strip()[:500]
        if action not in ("approve", "reject"):
            abort(400)
        try:
            outcome = act(instance, current_user, action == "approve",
                          comment)
        except PermissionError as e:
            if self_blocked(instance, current_user) and \
                    "own request" in str(e):
                flash("You cannot approve your own request.", "error")
                return redirect(url_for("views.regularisation_approvals"))
            abort(403)
        if outcome == "rejected":
            rr.status = "rejected"
            flash("Regularisation rejected.", "success")
        elif outcome == "final_approved":
            rr.status = "approved"
            # Apply the correction to the attendance record on final approval.
            rec = AttendanceRecord.query.filter_by(user_id=rr.user_id, date=rr.date).first()
            if not rec:
                rec = AttendanceRecord(user_id=rr.user_id, date=rr.date, status="present")
                db.session.add(rec)
            if rr.corrected_in:
                rec.check_in = rr.corrected_in
            if rr.corrected_out:
                rec.check_out = rr.corrected_out
            flash("Regularisation approved and attendance updated.", "success")
        else:
            flash("Approved at this step; forwarded to the next approver.",
                  "success")
        rr.approver_id = current_user.id
        rr.approver_comment = comment
        rr.decided_at = datetime.utcnow()
        log_action(current_user.id, f"regularisation_{rr.status}",
                   f"request #{rr.id} ({rr.user_id})")
        audit_record(f"regularisation_{rr.status}",
                     f"request #{rr.id} ({rr.user_id}) date {rr.date}",
                     entity="regularisation", entity_id=rr.id,
                     field_name="status", old_value="pending", new_value=rr.status)
        db.session.commit()
        return redirect(url_for("views.regularisation_approvals"))
    items = []
    for rr in q.order_by(RegularisationRequest.created_at.desc()).all():
        inst = get_or_start_instance("regularisation", rr.id, rr.user_id,
                                     rr.user_id)
        item_can_act = can_act(inst, current_user)
        if not current_user.is_admin:
            # Non-admins see their own department's requests only, and only
            # the ones sitting on their own approval step.
            target = User.query.get(rr.user_id)
            if not target or target.department != current_user.department:
                continue
            if not item_can_act:
                continue
        items.append({"rr": rr, "badge": step_badge(inst),
                      "can_act": item_can_act})
    users = {u.id: u for u in User.query.filter(
        User.id.in_([i["rr"].user_id for i in items])).all()} if items else {}
    return render_template("regularisation_approvals.html", items=items,
                           users=users)


# ---------------- My Roster (employee, v3.2) ----------------
@views_bp.route("/my-roster")
@login_required
def my_roster():
    """Employee's own shift assignments for the next 14 days."""
    from app.models import RosterAssignment
    today = date.today()
    rows = []
    for i in range(14):
        d = today + timedelta(days=i)
        a = RosterAssignment.query.filter_by(user_id=current_user.id,
                                             date=d).first()
        rows.append({"date": d, "assignment": a, "today": d == today})
    return render_template("my_roster.html", rows=rows)
