"""Employee-experience module (HRMS+ESS v3.3).

Covers:
  1. Mobile geo + selfie check-in/out with WorkLocation geo-fences and
     45-day punch-photo retention.
  2. Notices, notification centre, birthday/work-anniversary celebrations
     with wishes and reactions.
  3. LMS-lite: training courses, materials, quizzes, assignments, attempts
     and pass-triggered certifications.
  4. Job descriptions with digital-signature acknowledgement and versioning.

Private files (punch photos, training material, signatures) live under
instance/uploads/<subdir>/ and are served only through authenticated,
permission-checked routes — the same pattern as app/files.py.
"""
import io
import json
import math
from datetime import date, datetime, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, jsonify, send_file)
from flask_login import login_required, current_user

from app import db
from app.models import (User, AttendanceRecord, AuditLog, WorkLocation,
                        PunchRecord, Notice, NoticeAck, Notification,
                        CelebrationPost, CelebrationWish, REACTIONS,
                        TrainingCourse, QuizQuestion, TrainingAssignment,
                        TrainingAttempt, JobDescription, JDAcknowledgement,
                        AppSetting, RosterAssignment, ShiftPattern,
                        Certification, LeaveBalance)
from app.files import (store_file, read_file, remove_file, upload_root,
                       manager_chain_ids)
from app.audit import record as audit_record
import config as app_config

engagement_bp = Blueprint("engagement", __name__)


@engagement_bp.app_template_filter("gst_time")
def gst_time_filter(dt):
    """Render a UTC datetime as Asia/Dubai HH:MM."""
    if not dt:
        return "-"
    return (dt + timedelta(hours=4)).strftime("%H:%M")


GST_OFFSET = timedelta(hours=4)  # Asia/Dubai
PUNCH_PHOTO_DAYS = 45
PUNCH_DIR = "punches"
TRAINING_DIR = "training"
SIG_DIR = "signatures"
TRAINING_MAX_BYTES = 25 * 1024 * 1024


# ---------------- shared helpers ----------------

def gst_now():
    """Current time in Asia/Dubai (fixed +4 offset, like the rest of the app)."""
    return datetime.utcnow() + GST_OFFSET


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def admin_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            abort(403)
        return fn(*a, **kw)
    return wrapper


def notify_user(user_id, kind, title, body=None, link=None, ref_id=None,
                category="important"):
    """Queue one notification row. Committed by the caller.

    category: 'important' (default — notices, approvals, JD, training,
    payroll, expenses, letters, documents) or 'event' (celebration
    alerts: birthday/anniversary broadcasts and wishes).
    """
    db.session.add(Notification(user_id=user_id, kind=kind, title=title,
                                body=body, link=link, ref_id=ref_id,
                                category=category))


CELEBRATION_KINDS = ("birthday", "anniversary", "wish")


def backfill_notification_categories():
    """v3.3.1: give every notification a category. Rows that pre-date the
    column (or have a NULL category) default to 'important'; rows whose
    kind is celebration-related become 'event'. Idempotent."""
    from sqlalchemy import text
    db.session.execute(text(
        "UPDATE notifications SET category='important' "
        "WHERE category IS NULL"))
    db.session.execute(text(
        "UPDATE notifications SET category='event' "
        "WHERE kind IN ('birthday', 'anniversary', 'wish')"))
    db.session.commit()


def active_users(exclude_id=None):
    q = User.query.filter_by(is_active=True)
    if exclude_id:
        q = q.filter(User.id != exclude_id)
    return q.all()


def setting_today_guard(key, today):
    """True if the once-a-day job `key` still needs to run for `today`."""
    last = AppSetting.get(key)
    if last == today.isoformat():
        return False
    AppSetting.set(key, today.isoformat())
    return True


def image_from_upload(file_storage, max_width, quality=72):
    """Validate an uploaded image and return resized JPEG bytes.

    Accepts PNG/JPEG by magic bytes; returns (bytes, error).
    Requires Pillow.
    """
    try:
        from PIL import Image
    except ImportError:
        return None, "Image processing is not available on the server."
    data = file_storage.read() if hasattr(file_storage, "read") else file_storage
    if not data:
        return None, "No image received."
    sig = data[:8]
    if sig[:3] != b"\xff\xd8\xff" and sig[:8] != b"\x89PNG\r\n\x1a\n":
        return None, "Image must be a valid PNG or JPEG."
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None, "Could not read the image."
    if img.width > max_width:
        h = int(img.height * max_width / img.width)
        img = img.resize((max_width, h), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return out.getvalue(), None


def ordinal(n):
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _link(endpoint, **kw):
    """url_for that also works in app-only context (demo seed at startup).

    Outside a request context Flask's url_for raises; fall back to the
    static route paths, which are stable in this app.
    """
    try:
        return url_for(endpoint, **kw)
    except RuntimeError:
        static = {
            "engagement.celebrations": "/celebrations",
            "engagement.notifications": "/notifications",
            "engagement.my_training": "/my-training",
            "engagement.my_jd": "/my-jd",
        }
        if endpoint == "engagement.notice_view":
            return f"/notices/{kw.get('nid', '')}"
        return static.get(endpoint, "/")


# ================= 1) GEO + SELFIE CHECK-IN/OUT =================

def match_location(lat, lng):
    """Nearest active WorkLocation and distance, or (None, None)."""
    locs = WorkLocation.query.filter_by(is_active=True).all()
    best, best_d = None, None
    for loc in locs:
        d = haversine_m(lat, lng, loc.latitude, loc.longitude)
        if best_d is None or d < best_d:
            best, best_d = loc, d
    return best, best_d


@engagement_bp.route("/hrms/locations", methods=["GET", "POST"])
@login_required
@admin_required
def locations():
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "purge":
            n = purge_punch_photos(PUNCH_PHOTO_DAYS)
            audit_record("punch_photos_purged", f"deleted {n} old punch photos",
                         entity="punch", actor_id=current_user.id)
            db.session.commit()
            flash(f"Purged {n} punch photo(s) older than {PUNCH_PHOTO_DAYS} days.",
                  "success")
            return redirect(url_for("engagement.locations"))
        name = (request.form.get("name") or "").strip()[:120]
        try:
            lat = float(request.form.get("latitude", ""))
            lng = float(request.form.get("longitude", ""))
            radius = int(request.form.get("radius_m", "200") or 200)
        except (TypeError, ValueError):
            lat = lng = None
        lid = request.form.get("id") or None
        if action == "delete" and lid:
            loc = WorkLocation.query.get(int(lid))
            if loc:
                db.session.delete(loc)
                audit_record("location_deleted", loc.name, entity="location",
                             entity_id=loc.id, actor_id=current_user.id)
                db.session.commit()
                flash("Location deleted.", "success")
            return redirect(url_for("engagement.locations"))
        if not name or lat is None or lng is None:
            flash("Name, latitude and longitude are required.", "error")
        elif not (-90 <= lat <= 90 and -180 <= lng <= 180):
            flash("Coordinates out of range.", "error")
        else:
            if lid:
                loc = WorkLocation.query.get(int(lid))
                loc.name, loc.latitude, loc.longitude = name, lat, lng
                loc.radius_m = max(10, min(radius, 100000))
                loc.is_active = request.form.get("is_active") == "on"
                audit_record("location_updated", name, entity="location",
                             entity_id=loc.id, actor_id=current_user.id)
            else:
                loc = WorkLocation(
                    name=name, latitude=lat, longitude=lng,
                    radius_m=max(10, min(radius, 100000)),
                    is_active=request.form.get("is_active", "on") == "on",
                    created_by=current_user.id)
                db.session.add(loc)
                audit_record("location_created", name, entity="location",
                             actor_id=current_user.id)
            db.session.commit()
            flash("Location saved.", "success")
            return redirect(url_for("engagement.locations"))
    locs = WorkLocation.query.order_by(WorkLocation.name).all()
    photo_count = PunchRecord.query.filter(
        PunchRecord.photo_path.isnot(None)).count()
    return render_template("hr_locations.html", locations=locs,
                           photo_count=photo_count, purge_days=PUNCH_PHOTO_DAYS)


@engagement_bp.route("/attendance/punch")
@login_required
def punch_page():
    """Mobile-first check-in/out page. Runs the 45-day purge at most daily."""
    maybe_purge_daily()
    today = gst_now().date()
    rec = AttendanceRecord.query.filter_by(
        user_id=current_user.id, date=today).first()
    return render_template("punch.html", today=today, record=rec,
                           locations=WorkLocation.query.filter_by(
                               is_active=True).all())


def _record_punch(user, ptype, lat, lng, accuracy, photo_path, loc, dist,
                  low_accuracy=False):
    """Create the punch row and update today's AttendanceRecord."""
    now = gst_now()
    punch = PunchRecord(
        user_id=user.id, punch_type=ptype, punched_at=datetime.utcnow(),
        local_date=now.date(), latitude=lat, longitude=lng,
        accuracy_m=accuracy, location_id=loc.id if loc else None,
        distance_m=dist, photo_path=photo_path,
        low_accuracy=low_accuracy)
    db.session.add(punch)
    hhmm = now.strftime("%H:%M")
    rec = AttendanceRecord.query.filter_by(user_id=user.id,
                                           date=now.date()).first()
    if not rec:
        rec = AttendanceRecord(user_id=user.id, date=now.date(),
                               status="present")
        db.session.add(rec)
    if ptype == "in":
        if not rec.check_in or hhmm < rec.check_in:
            rec.check_in = hhmm
        # Late if a rostered shift exists and punch is past start + grace.
        ra = RosterAssignment.query.filter_by(user_id=user.id,
                                              date=now.date()).first()
        if ra and ra.shift:
            try:
                sh, sm = map(int, ra.shift.start_time.split(":"))
                grace = ra.shift.grace_minutes or 0
                limit = sh * 60 + sm + grace
                got = now.hour * 60 + now.minute
                if got > limit:
                    rec.status = "late"
            except (ValueError, AttributeError):
                pass
    else:
        if not rec.check_out or hhmm > rec.check_out:
            rec.check_out = hhmm
    return punch


@engagement_bp.route("/attendance/punch/submit", methods=["POST"])
@login_required
def punch_submit():
    """Multipart POST from punch.js: punch_type, latitude, longitude,
    accuracy, photo (File). Returns JSON."""
    ptype = (request.form.get("punch_type") or "").strip()
    if ptype not in ("in", "out"):
        return jsonify(ok=False, error="Invalid punch type."), 400
    try:
        lat = float(request.form.get("latitude", ""))
        lng = float(request.form.get("longitude", ""))
    except (TypeError, ValueError):
        return jsonify(ok=False,
                       error="Location is required. Please enable GPS and retry."), 400
    try:
        accuracy = float(request.form.get("accuracy", "") or 0) or None
    except (TypeError, ValueError):
        accuracy = None
    photo = request.files.get("photo")
    if not photo:
        return jsonify(ok=False,
                       error="A selfie photo is required for check-in/out."), 400
    jpg, err = image_from_upload(photo, 800, 72)
    if err:
        return jsonify(ok=False, error=err), 400

    loc, dist = match_location(lat, lng)
    active_count = WorkLocation.query.filter_by(is_active=True).count()
    if active_count > 0 and (loc is None or dist > loc.radius_m):
        # v3.4.1: no location/distance detail when far away — the generic
        # message discloses nothing about the site's position.
        detail = (dist is not None and dist <= 2000)
        nearest = (f" (nearest: {loc.name}, {dist:,.0f} m away)"
                   if loc and detail else "")
        audit_record("punch_rejected",
                     f"{current_user.id} {ptype} at ({lat:.5f},{lng:.5f}){nearest}",
                     entity="punch", actor_id=current_user.id)
        db.session.commit()
        err = ("You are outside every authorised work location."
               if not detail
               else f"You are outside every work location{nearest}. "
                    f"Move inside the site boundary and retry.")
        return jsonify(ok=False, error=err), 403

    stored = store_file(jpg, PUNCH_DIR, "jpg")
    # v3.4.1: poor GPS accuracy is flagged, not rejected — the punch still
    # counts if it is inside a geofence; HR sees the flag in the list.
    low_acc = accuracy is not None and accuracy > 150
    punch = _record_punch(current_user, ptype, lat, lng, accuracy,
                          stored, loc, dist, low_accuracy=low_acc)
    if low_acc:
        audit_record("punch_low_accuracy",
                     f"{current_user.id} {ptype} with {accuracy:.0f} m GPS "
                     f"accuracy", entity="punch", entity_id=punch.id,
                     actor_id=current_user.id)
    audit_record("punch_accepted",
                 f"{current_user.id} {ptype} @ {loc.name if loc else 'unrestricted'}",
                 entity="punch", entity_id=punch.id, actor_id=current_user.id)
    db.session.commit()
    return jsonify(ok=True, punch_id=punch.id, type=ptype,
                   time=gst_now().strftime("%H:%M"),
                   location=loc.name if loc else "Unrestricted")


@engagement_bp.route("/attendance/punch-photo/<int:pid>")
@login_required
def punch_photo(pid):
    """Private punch photo: owner, admin, or direct reporting manager only."""
    p = PunchRecord.query.get_or_404(pid)
    target = User.query.get(p.user_id)
    allowed = (current_user.id == p.user_id or current_user.is_admin
               or (target and target.reporting_manager == current_user.id))
    if not allowed:
        abort(403)
    if current_user.id != p.user_id:
        audit_record("punch_photo_viewed", f"punch #{p.id} of {p.user_id}",
                     entity="punch", entity_id=p.id, actor_id=current_user.id)
        db.session.commit()
    data = read_file(PUNCH_DIR, p.photo_path) if p.photo_path else None
    if not data:
        abort(404)
    return send_file(io.BytesIO(data), mimetype="image/jpeg",
                     download_name=f"punch-{p.id}.jpg")


@engagement_bp.route("/hrms/punches")
@login_required
def hr_punches():
    """HR punch list with date filter. Managers see direct reports only."""
    try:
        d = datetime.strptime(request.args.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        d = gst_now().date()
    q = PunchRecord.query.filter_by(local_date=d)
    if not current_user.is_admin:
        if not current_user.is_manager:
            abort(403)
        report_ids = [u.id for u in User.query.filter_by(
            reporting_manager=current_user.id).all()]
        q = q.filter(PunchRecord.user_id.in_(report_ids))
    punches = q.order_by(PunchRecord.punched_at.desc()).all()
    users = {u.id: u for u in User.query.all()}
    return render_template("hr_punches.html", punches=punches, users=users,
                           view_date=d)


@engagement_bp.route("/hrms/punches/<int:pid>/hold", methods=["POST"])
@login_required
def punch_hold(pid):
    """Admin-only: put a punch photo on legal hold (pauses the 45-day
    purge) or release it. Audited."""
    if not current_user.is_admin:
        abort(403)
    p = PunchRecord.query.get_or_404(pid)
    hold = request.form.get("action") == "hold"
    p.legal_hold = hold
    audit_record("punch_legal_hold" if hold else "punch_legal_hold_released",
                 f"punch #{p.id} of {p.user_id} "
                 f"{'held' if hold else 'released'}",
                 entity="punch", entity_id=p.id, actor_id=current_user.id)
    db.session.commit()
    flash(f"Punch photo {'placed on legal hold' if hold else 'released from legal hold'}.",
          "success")
    return redirect(url_for("engagement.hr_punches",
                            date=p.local_date.isoformat()))


# ---------------- 45-day photo retention ----------------

def purge_punch_photos(days=PUNCH_PHOTO_DAYS):
    """Delete punch photo files older than `days`; keep the records.

    v3.4.1: records on legal hold are skipped (their photos are kept).
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    old = PunchRecord.query.filter(PunchRecord.photo_path.isnot(None),
                                   PunchRecord.legal_hold.isnot(True),
                                   PunchRecord.created_at < cutoff).all()
    n = 0
    for p in old:
        remove_file(PUNCH_DIR, p.photo_path)
        p.photo_path = None
        n += 1
    db.session.commit()
    return n


def maybe_purge_daily():
    """Run the retention purge at most once per day (lazy housekeeping)."""
    today = gst_now().date()
    if AppSetting.get("punch_purge_last_run") != today.isoformat():
        purge_punch_photos(PUNCH_PHOTO_DAYS)
        AppSetting.set("punch_purge_last_run", today.isoformat())
        db.session.commit()


# ================= 2) NOTICES / NOTIFICATIONS / CELEBRATIONS =================

def fanout_notice(notice):
    """Create a Notification for every active employee in the audience."""
    q = User.query.filter_by(is_active=True)
    if notice.audience and notice.audience != "all":
        q = q.filter_by(department=notice.audience)
    n = 0
    for u in q.all():
        notify_user(u.id, "notice", notice.title, notice.body,
                    link=_link("engagement.notice_view", nid=notice.id),
                    ref_id=notice.id)
        n += 1
    return n


def notice_audience_users(notice):
    """Active users the notice was addressed to."""
    q = User.query.filter_by(is_active=True)
    if notice.audience and notice.audience != "all":
        q = q.filter_by(department=notice.audience)
    return q.all()


def notice_can_view(user, notice):
    if user.is_admin:
        return True
    return notice.audience in ("all", user.department or "")


@engagement_bp.route("/hrms/notices", methods=["GET", "POST"])
@login_required
def notices():
    """Admin creates notices; managers can read them."""
    if request.method == "POST" and not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "delete":
            n = Notice.query.get_or_404(int(request.form.get("id", 0)))
            for a in NoticeAck.query.filter_by(notice_id=n.id).all():
                db.session.delete(a)
            db.session.delete(n)
            audit_record("notice_deleted", n.title, entity="notice",
                         entity_id=n.id, actor_id=current_user.id)
            db.session.commit()
            flash("Notice deleted.", "success")
            return redirect(url_for("engagement.notices"))
        title = (request.form.get("title") or "").strip()[:200]
        body = (request.form.get("body") or "").strip()[:5000]
        audience = (request.form.get("audience") or "all").strip()[:80]
        expires = (request.form.get("expires_at") or "").strip()
        requires_ack = request.form.get("requires_ack") == "on"
        if not title or not body:
            flash("Title and message are required.", "error")
        else:
            exp = None
            if expires:
                try:
                    exp = datetime.strptime(expires, "%Y-%m-%d")
                except ValueError:
                    exp = None
            n = Notice(title=title, body=body, audience=audience,
                       requires_ack=requires_ack,
                       created_by=current_user.id, expires_at=exp)
            db.session.add(n)
            db.session.flush()
            sent = fanout_notice(n)
            audit_record("notice_created",
                         f"{title} -> {audience} ({sent})"
                         f"{' [mandatory read]' if requires_ack else ''}",
                         entity="notice", entity_id=n.id,
                         actor_id=current_user.id)
            db.session.commit()
            flash(f"Notice published to {sent} employee(s).", "success")
            return redirect(url_for("engagement.notices"))
    items = Notice.query.order_by(Notice.created_at.desc()).all()
    departments = sorted({u.department for u in User.query.all()
                          if u.department})
    # Acknowledgement tracking for mandatory-read notices.
    ack_info = {}
    for n in items:
        if n.requires_ack:
            aud = notice_audience_users(n)
            acked_ids = {a.user_id for a in NoticeAck.query.filter_by(
                notice_id=n.id).all()}
            ack_info[n.id] = {
                "total": len(aud),
                "acknowledged": len([u for u in aud if u.id in acked_ids]),
                "pending": [u for u in aud if u.id not in acked_ids],
            }
    return render_template("hr_notices.html", notices=items,
                           departments=departments, ack_info=ack_info)


@engagement_bp.route("/notices/<int:nid>", methods=["GET", "POST"])
@login_required
def notice_view(nid):
    """Employee notice reading + mandatory acknowledgement."""
    n = Notice.query.get_or_404(nid)
    if not notice_can_view(current_user, n):
        abort(403)
    ack = NoticeAck.query.filter_by(notice_id=nid,
                                    user_id=current_user.id).first()
    if request.method == "POST":
        if not n.requires_ack:
            flash("This notice does not require acknowledgement.", "error")
        elif ack:
            flash("Already acknowledged.", "success")
        else:
            db.session.add(NoticeAck(notice_id=nid,
                                     user_id=current_user.id))
            audit_record("notice_acknowledged", n.title, entity="notice",
                         entity_id=nid, actor_id=current_user.id)
            db.session.commit()
            flash("Thank you — acknowledgement recorded.", "success")
            return redirect(url_for("engagement.notice_view", nid=nid))
    return render_template("notice_view.html", notice=n, ack=ack)


def pending_mandatory_notices(user):
    """Mandatory-read notices the user has not acknowledged yet."""
    now = datetime.utcnow()
    q = Notice.query.filter_by(requires_ack=True).filter(
        (Notice.expires_at.is_(None)) | (Notice.expires_at > now))
    if not user.is_admin:
        q = q.filter((Notice.audience == "all")
                     | (Notice.audience == (user.department or "")))
    acked = {a.notice_id for a in NoticeAck.query.filter_by(
        user_id=user.id).all()}
    return [n for n in q.order_by(Notice.created_at.desc()).all()
            if n.id not in acked]


def visible_notices(user, limit=3):
    now = datetime.utcnow()
    q = Notice.query.filter(
        (Notice.expires_at.is_(None)) | (Notice.expires_at > now))
    if not user.is_admin:
        q = q.filter((Notice.audience == "all")
                     | (Notice.audience == (user.department or "")))
    return q.order_by(Notice.created_at.desc()).limit(limit).all()


@engagement_bp.route("/notifications")
@login_required
def notifications():
    tab = (request.args.get("tab") or "important").strip()
    if tab not in ("important", "event"):
        tab = "important"
    base = Notification.query.filter_by(user_id=current_user.id)
    items = base.filter_by(category=tab)\
        .order_by(Notification.created_at.desc()).limit(50).all()
    imp_unread = base.filter_by(category="important",
                                is_read=False).count()
    ev_unread = base.filter_by(category="event", is_read=False).count()
    return render_template("notifications.html", items=items, tab=tab,
                           imp_unread=imp_unread, ev_unread=ev_unread)


@engagement_bp.route("/notifications/read/<int:nid>", methods=["POST"])
@login_required
def notification_read(nid):
    n = Notification.query.get_or_404(nid)
    if n.user_id != current_user.id:
        abort(403)
    n.is_read = True
    db.session.commit()
    return redirect(n.link or url_for("engagement.notifications"))


@engagement_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    # v3.3.1: mark-all-read acts per tab; without a valid tab it clears all.
    tab = (request.form.get("tab") or "").strip()
    q = Notification.query.filter_by(user_id=current_user.id,
                                     is_read=False)
    if tab in ("important", "event"):
        q = q.filter_by(category=tab)
    q.update({"is_read": True})
    db.session.commit()
    return redirect(url_for("engagement.notifications",
                            tab=tab if tab in ("important", "event")
                            else None))


# ---------------- celebrations ----------------

def ensure_todays_celebrations():
    """Create today's birthday/anniversary posts + wish notifications.

    Safe to call repeatedly (unique constraint); runs once per day per
    app instance via the AppSetting guard.
    """
    today = gst_now().date()
    if not setting_today_guard("celebrations_last_run", today):
        return []
    made = []
    for u in active_users():
        # v3.4.1: employees who opted out get no post (they still receive
        # notifications and can wish others).
        if getattr(u, "share_celebrations", True) is False:
            continue
        kinds = []
        if (u.date_of_birth and u.date_of_birth.month == today.month
                and u.date_of_birth.day == today.day):
            kinds.append(("birthday", None))
        if u.joining_date and u.joining_date.month == today.month \
                and u.joining_date.day == today.day:
            yrs = today.year - u.joining_date.year
            if yrs >= 1:
                kinds.append(("anniversary", yrs))
        for kind, yrs in kinds:
            post = CelebrationPost.query.filter_by(
                user_id=u.id, kind=kind, post_date=today).first()
            if post is None:
                post = CelebrationPost(user_id=u.id, kind=kind,
                                       post_date=today, years=yrs)
                db.session.add(post)
                db.session.flush()
            label = ("birthday" if kind == "birthday"
                     else f"{ordinal(yrs)} work anniversary")
            for other in active_users(exclude_id=u.id):
                notify_user(other.id, kind,
                            f"It's {u.name}'s {label} today!",
                            "Send your wishes and reactions.",
                            link=_link("engagement.celebrations"), ref_id=post.id,
                            category="event")
            made.append(post)
    db.session.commit()
    return made


def celebration_rows(days_back=7):
    """Posts from today back `days_back` days, with wishes/users/reactions."""
    today = gst_now().date()
    start = today - timedelta(days=days_back)
    posts = CelebrationPost.query.filter(
        CelebrationPost.post_date >= start).order_by(
        CelebrationPost.post_date.desc()).all()
    user_ids = {p.user_id for p in posts}
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} \
        if user_ids else {}
    wish_rows = {}
    for p in posts:
        ws = CelebrationWish.query.filter_by(post_id=p.id)\
            .order_by(CelebrationWish.created_at).all()
        counts = {r: 0 for r in REACTIONS}
        mine = None
        messages = []
        for w in ws:
            if w.reaction in counts:
                counts[w.reaction] += 1
            if w.user_id == current_user.id:
                mine = w
            if w.message:
                who = users_wisher(w.user_id)
                messages.append({"user": who, "message": w.message,
                                 "at": w.created_at})
        wish_rows[p.id] = {"wishes": ws, "counts": counts, "mine": mine,
                           "messages": messages}
    return posts, users, wish_rows


def users_wisher(uid):
    return User.query.get(uid)


@engagement_bp.route("/celebrations", methods=["GET", "POST"])
@login_required
def celebrations():
    ensure_todays_celebrations()
    if request.method == "POST":
        pid = int(request.form.get("post_id", 0))
        post = CelebrationPost.query.get_or_404(pid)
        target = User.query.get(post.user_id)
        reaction = (request.form.get("reaction") or "").strip()
        message = (request.form.get("message") or "").strip()[:200]
        if reaction and reaction not in REACTIONS:
            flash("Invalid reaction.", "error")
            return redirect(url_for("engagement.celebrations"))
        own = (post.user_id == current_user.id)
        if own and message:
            flash("You can't send yourself a written wish — pick a reaction "
                  "instead.", "error")
            return redirect(url_for("engagement.celebrations"))
        row = CelebrationWish.query.filter_by(post_id=pid,
                                              user_id=current_user.id).first()
        new_message = False
        if row is None:
            row = CelebrationWish(post_id=pid, user_id=current_user.id,
                                  message=message or None,
                                  reaction=reaction or None)
            db.session.add(row)
            new_message = bool(message)
        else:
            if message:
                new_message = (row.message != message)
                row.message = message
            if reaction:
                row.reaction = reaction
        if new_message and target:
            notify_user(target.id, "wish",
                        f"{current_user.name} wished you: {message}",
                        link=_link("engagement.celebrations"), ref_id=pid,
                        category="event")
        audit_record("celebration_wish",
                     f"post #{pid} by {current_user.id}",
                     entity="celebration", entity_id=pid,
                     actor_id=current_user.id)
        db.session.commit()
        flash("Wish saved.", "success")
        return redirect(url_for("engagement.celebrations"))
    posts, users, wish_rows = celebration_rows()
    return render_template("celebrations.html", posts=posts, users=users,
                           wish_rows=wish_rows, reactions=REACTIONS,
                           today=gst_now().date())


# ================= 3) TRAINING (LMS-lite) =================

TRAINING_CATEGORIES = ["Induction", "Orientation", "Basic", "Technical",
                       "Safety", "Other"]

MAGIC_EXTRA = {
    "mp4": (4, b"ftyp"),  # 'ftyp' box at offset 4
}


def valid_training_material(data, filename):
    """PDF/PNG/JPG/MP4 up to 25 MB. Returns (suffix, error)."""
    if not data or len(data) > TRAINING_MAX_BYTES:
        return None, "Material must be under 25 MB."
    from app.files import kind_from_bytes
    kind = kind_from_bytes(data)
    if kind in ("pdf", "png", "jpg"):
        return kind, None
    if len(data) > 8 and data[4:8] == b"ftyp":
        ext = (filename or "").rsplit(".", 1)[-1].lower()
        if ext in ("mp4", "m4v", "mov"):
            return "mp4", None
    return None, "Material must be a valid PDF, PNG, JPG or MP4 file."


def best_attempt(user_id, course_id):
    atts = TrainingAttempt.query.filter_by(user_id=user_id,
                                           course_id=course_id).all()
    return max(atts, key=lambda a: a.score_percent) if atts else None


def issue_training_cert(user_id, course):
    """Create a Certification on passing, unless an unexpired one exists."""
    if not course.issue_cert_name:
        return None
    name = course.issue_cert_name
    today = gst_now().date()
    existing = Certification.query.filter_by(user_id=user_id,
                                             name=name).all()
    for c in existing:
        if not c.expiry_date or c.expiry_date >= today:
            return None  # already certified and current
    expiry = None
    if course.cert_valid_months:
        expiry = today + timedelta(days=30 * course.cert_valid_months)
    cert = Certification(user_id=user_id, name=name,
                         issuer="Internal Training",
                         issue_date=today, expiry_date=expiry,
                         created_by="system")
    db.session.add(cert)
    return cert


@engagement_bp.route("/hrms/training", methods=["GET", "POST"])
@login_required
@admin_required
def hr_training():
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "delete":
            c = TrainingCourse.query.get_or_404(int(request.form.get("id", 0)))
            for q in QuizQuestion.query.filter_by(course_id=c.id).all():
                db.session.delete(q)
            for a in TrainingAssignment.query.filter_by(course_id=c.id).all():
                db.session.delete(a)
            for t in TrainingAttempt.query.filter_by(course_id=c.id).all():
                db.session.delete(t)
            if c.material_path:
                remove_file(TRAINING_DIR, c.material_path)
            db.session.delete(c)
            audit_record("training_deleted", c.title, entity="training",
                         entity_id=c.id, actor_id=current_user.id)
            db.session.commit()
            flash("Course deleted.", "success")
            return redirect(url_for("engagement.hr_training"))
        title = (request.form.get("title") or "").strip()[:160]
        category = (request.form.get("category") or "Basic").strip()
        description = (request.form.get("description") or "").strip()[:4000]
        try:
            pass_percent = int(request.form.get("pass_percent", "70") or 70)
        except ValueError:
            pass_percent = 70
        cert_name = (request.form.get("issue_cert_name") or "").strip()[:120] or None
        try:
            cert_months = int(request.form.get("cert_valid_months", "") or 0) or None
        except ValueError:
            cert_months = None
        mat_kind, mat_path, mat_url = "none", None, None
        mfile = request.files.get("material_file")
        murl = (request.form.get("material_url") or "").strip()[:400]
        if mfile and mfile.filename:
            data = mfile.read()
            suffix, err = valid_training_material(data, mfile.filename)
            if err:
                flash(err, "error")
                return redirect(url_for("engagement.hr_training"))
            mat_path = store_file(data, TRAINING_DIR, suffix)
            mat_kind = "file"
        elif murl:
            mat_kind, mat_url = "link", murl
        if not title:
            flash("Course title is required.", "error")
        elif category not in TRAINING_CATEGORIES:
            flash("Invalid category.", "error")
        else:
            c = TrainingCourse(
                title=title, category=category, description=description,
                material_kind=mat_kind, material_path=mat_path,
                material_url=mat_url,
                pass_percent=max(0, min(pass_percent, 100)),
                issue_cert_name=cert_name, cert_valid_months=cert_months,
                created_by=current_user.id)
            db.session.add(c)
            audit_record("training_created", title, entity="training",
                         actor_id=current_user.id)
            db.session.commit()
            flash("Course created.", "success")
            return redirect(url_for("engagement.hr_training_detail",
                                    cid=c.id))
    courses = TrainingCourse.query.order_by(TrainingCourse.title).all()
    return render_template("hr_training.html", courses=courses,
                           categories=TRAINING_CATEGORIES)


@engagement_bp.route("/hrms/training/<int:cid>", methods=["GET", "POST"])
@login_required
@admin_required
def hr_training_detail(cid):
    c = TrainingCourse.query.get_or_404(cid)
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "add_question":
            q_text = (request.form.get("question") or "").strip()[:2000]
            opts = [(request.form.get(f"opt_{i}") or "").strip()[:500]
                    for i in range(4)]
            try:
                correct = int(request.form.get("correct", "0"))
            except ValueError:
                correct = 0
            if not q_text or any(not o for o in opts) or not (0 <= correct <= 3):
                flash("Question needs text, 4 options and a correct answer.",
                      "error")
            else:
                n = QuizQuestion.query.filter_by(course_id=cid).count()
                db.session.add(QuizQuestion(
                    course_id=cid, question=q_text,
                    options_json=json.dumps(opts), correct_index=correct,
                    sort=n))
                audit_record("training_question_added", c.title,
                             entity="training", entity_id=cid,
                             actor_id=current_user.id)
                db.session.commit()
                flash("Question added.", "success")
            return redirect(url_for("engagement.hr_training_detail", cid=cid))
        if action == "del_question":
            q = QuizQuestion.query.get_or_404(int(request.form.get("qid", 0)))
            if q.course_id != cid:
                abort(400)
            db.session.delete(q)
            db.session.commit()
            flash("Question deleted.", "success")
            return redirect(url_for("engagement.hr_training_detail", cid=cid))
        if action == "assign":
            scope = request.form.get("scope", "")
            due = (request.form.get("due_date") or "").strip()
            due_date = None
            if due:
                try:
                    due_date = datetime.strptime(due, "%Y-%m-%d").date()
                except ValueError:
                    due_date = None
            targets = []
            single = (request.form.get("single_user") or "").strip()
            if single:
                u = User.query.get(single)
                targets = [u.id] if u and u.is_active else []
            elif scope == "all":
                targets = [u.id for u in active_users()]
            elif scope.startswith("dept:"):
                dept = scope[5:]
                targets = [u.id for u in User.query.filter_by(
                    department=dept, is_active=True).all()]
            elif scope.startswith("user:"):
                targets = [scope[5:]]
            added = 0
            for uid in targets:
                if not TrainingAssignment.query.filter_by(
                        course_id=cid, user_id=uid).first():
                    db.session.add(TrainingAssignment(
                        course_id=cid, user_id=uid, due_date=due_date,
                        assigned_by=current_user.id))
                    notify_user(uid, "training",
                                f"New training assigned: {c.title}",
                                f"Due: {due_date}" if due_date else None,
                                link=_link("engagement.my_training"))
                    added += 1
            audit_record("training_assigned",
                         f"{c.title} -> {added} employee(s)",
                         entity="training", entity_id=cid,
                         actor_id=current_user.id)
            db.session.commit()
            flash(f"Assigned to {added} employee(s).", "success")
            return redirect(url_for("engagement.hr_training_detail", cid=cid))
    questions = QuizQuestion.query.filter_by(course_id=cid)\
        .order_by(QuizQuestion.sort).all()
    assigns = TrainingAssignment.query.filter_by(course_id=cid).all()
    users = {u.id: u for u in User.query.all()}
    attempts = TrainingAttempt.query.filter_by(course_id=cid)\
        .order_by(TrainingAttempt.completed_at.desc()).all()
    departments = sorted({u.department for u in User.query.all()
                          if u.department})
    return render_template("hr_training_detail.html", course=c,
                           questions=questions, assigns=assigns, users=users,
                           attempts=attempts, departments=departments)


@engagement_bp.route("/my-training")
@login_required
def my_training():
    assigns = TrainingAssignment.query.filter_by(
        user_id=current_user.id).all()
    courses = {c.id: c for c in TrainingCourse.query.filter(
        TrainingCourse.id.in_([a.course_id for a in assigns])).all()} \
        if assigns else {}
    rows = []
    for a in assigns:
        c = courses.get(a.course_id)
        if not c or not c.is_active:
            continue
        b = best_attempt(current_user.id, a.course_id)
        rows.append({"assign": a, "course": c, "best": b})
    return render_template("my_training.html", rows=rows)


@engagement_bp.route("/my-training/<int:cid>")
@login_required
def training_course_page(cid):
    c = TrainingCourse.query.get_or_404(cid)
    assign = TrainingAssignment.query.filter_by(
        course_id=cid, user_id=current_user.id).first()
    if not assign and not current_user.is_admin:
        abort(403)
    b = best_attempt(current_user.id, cid)
    questions = QuizQuestion.query.filter_by(course_id=cid)\
        .order_by(QuizQuestion.sort).all()
    return render_template("my_training_course.html", course=c,
                           assign=assign, best=b,
                           n_questions=len(questions))


@engagement_bp.route("/my-training/<int:cid>/quiz", methods=["GET", "POST"])
@login_required
def training_quiz(cid):
    c = TrainingCourse.query.get_or_404(cid)
    assign = TrainingAssignment.query.filter_by(
        course_id=cid, user_id=current_user.id).first()
    if not assign and not current_user.is_admin:
        abort(403)
    questions = QuizQuestion.query.filter_by(course_id=cid)\
        .order_by(QuizQuestion.sort).all()
    if not questions:
        flash("This course has no quiz questions yet.", "error")
        return redirect(url_for("engagement.training_course_page", cid=cid))
    if request.method == "POST":
        answers, correct = [], 0
        for q in questions:
            try:
                picked = int(request.form.get(f"q_{q.id}", "-1"))
            except ValueError:
                picked = -1
            answers.append(picked)
            if picked == q.correct_index:
                correct += 1
        score = round(correct * 100.0 / len(questions), 1)
        passed = score >= (c.pass_percent or 70)
        att = TrainingAttempt(course_id=cid, user_id=current_user.id,
                              score_percent=score, passed=passed,
                              answers_json=json.dumps(answers))
        db.session.add(att)
        cert_made = None
        if passed:
            cert_made = issue_training_cert(current_user.id, c)
            notify_user(current_user.id, "training",
                        f"Course completed: {c.title}",
                        f"Score {score}% — {'Passed' if passed else 'Failed'}.",
                        link=_link("engagement.my_training"))
        audit_record("training_quiz", f"{c.title}: {score}% "
                     f"({'pass' if passed else 'fail'})",
                     entity="training", entity_id=cid,
                     actor_id=current_user.id)
        db.session.commit()
        flash(f"Score: {score}% — {'Passed!' if passed else 'Not passed. You can retake.'}"
              + (" A certification was issued." if cert_made else ""),
              "success" if passed else "error")
        return redirect(url_for("engagement.training_course_page", cid=cid))
    # v3.4.1: shuffle question order and each question's option order
    # server-side on every render. Options are submitted by their ORIGINAL
    # index, so grading (which compares against q.correct_index) is
    # unaffected by the shuffle.
    import random
    quiz_questions = []
    for q in questions:
        opts = list(enumerate(q.options))
        random.shuffle(opts)
        quiz_questions.append({"q": q, "opts": opts})
    random.shuffle(quiz_questions)
    return render_template("my_training_quiz.html", course=c,
                           quiz_questions=quiz_questions)


@engagement_bp.route("/training/material/<int:cid>")
@login_required
def training_material(cid):
    c = TrainingCourse.query.get_or_404(cid)
    if c.material_kind != "file" or not c.material_path:
        abort(404)
    assign = TrainingAssignment.query.filter_by(
        course_id=cid, user_id=current_user.id).first()
    if not assign and not current_user.is_admin:
        abort(403)
    audit_record("training_material_viewed", c.title, entity="training",
                 entity_id=cid, actor_id=current_user.id)
    db.session.commit()
    data = read_file(TRAINING_DIR, c.material_path)
    if not data:
        abort(404)
    suffix = c.material_path.rsplit(".", 1)[-1].lower()
    mime = {"pdf": "application/pdf", "png": "image/png",
            "jpg": "image/jpeg", "mp4": "video/mp4"}.get(suffix,
                                                         "application/octet-stream")
    return send_file(io.BytesIO(data), mimetype=mime,
                     download_name=f"training-{cid}.{suffix}")


# ================= 4) JOB DESCRIPTIONS + DIGITAL-SIGNATURE ACK =================

def jd_dashboard_rows():
    """Per-JD assigned/viewed/acknowledged counts + stale count."""
    rows = []
    for jd in JobDescription.query.filter_by(is_active=True)\
            .order_by(JobDescription.title).all():
        acks = JDAcknowledgement.query.filter_by(jd_id=jd.id).all()
        assigned = sum(1 for a in acks if a.status == "assigned")
        viewed = sum(1 for a in acks if a.status == "viewed")
        acked = sum(1 for a in acks if a.status == "acknowledged"
                    and a.jd_version == jd.version)
        stale = sum(1 for a in acks if a.status == "acknowledged"
                    and a.jd_version < jd.version)
        rows.append({"jd": jd, "assigned": assigned, "viewed": viewed,
                     "acknowledged": acked, "stale": stale,
                     "total": len(acks)})
    return rows


@engagement_bp.route("/hrms/jds", methods=["GET", "POST"])
@login_required
@admin_required
def jds():
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "delete":
            jd = JobDescription.query.get_or_404(int(request.form.get("id", 0)))
            jd.is_active = False
            audit_record("jd_deleted", jd.title, entity="jd",
                         entity_id=jd.id, actor_id=current_user.id)
            db.session.commit()
            flash("Job description deactivated.", "success")
            return redirect(url_for("engagement.jds"))
        title = (request.form.get("title") or "").strip()[:120]
        department = (request.form.get("department") or "").strip()[:80] or None
        reports_to = (request.form.get("reports_to") or "").strip()[:120] or None
        body = (request.form.get("body") or "").strip()
        mat_path = None
        mfile = request.files.get("material_file")
        if mfile and mfile.filename:
            data = mfile.read()
            from app.files import valid_document
            kind, err = valid_document(data, mfile.filename)
            if err or kind != "pdf":
                flash("JD attachment must be a valid PDF.", "error")
                return redirect(url_for("engagement.jds"))
            mat_path = store_file(data, "docs", "pdf")
        if not title or not body:
            flash("Title and body are required.", "error")
        else:
            jd = JobDescription(title=title, department=department,
                                reports_to=reports_to, body=body,
                                material_path=mat_path,
                                created_by=current_user.id)
            db.session.add(jd)
            audit_record("jd_created", title, entity="jd",
                         actor_id=current_user.id)
            db.session.commit()
            flash("Job description created.", "success")
            return redirect(url_for("engagement.jd_detail", jid=jd.id))
    return render_template("hr_jds.html", rows=jd_dashboard_rows())


@engagement_bp.route("/hrms/jds/<int:jid>", methods=["GET", "POST"])
@login_required
@admin_required
def jd_detail(jid):
    jd = JobDescription.query.get_or_404(jid)
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "edit":
            title = (request.form.get("title") or "").strip()[:120]
            department = (request.form.get("department") or "").strip()[:80] or None
            reports_to = (request.form.get("reports_to") or "").strip()[:120] or None
            body = (request.form.get("body") or "").strip()
            if not title or not body:
                flash("Title and body are required.", "error")
            else:
                bumped = body != jd.body
                jd.title, jd.department, jd.reports_to, jd.body = \
                    title, department, reports_to, body
                if bumped:
                    jd.version += 1
                audit_record("jd_edited", f"{title} v{jd.version}",
                             entity="jd", entity_id=jd.id,
                             actor_id=current_user.id)
                db.session.commit()
                flash(f"Saved (version {jd.version})."
                      + (" Acknowledgements from earlier versions now need "
                         "re-acknowledgement." if bumped else ""),
                      "success")
            return redirect(url_for("engagement.jd_detail", jid=jid))
        if action == "assign":
            scope = request.form.get("scope", "")
            targets = []
            if scope == "all":
                targets = [u.id for u in active_users()]
            elif scope.startswith("dept:"):
                targets = [u.id for u in User.query.filter_by(
                    department=scope[5:], is_active=True).all()]
            elif scope == "by_designation":
                targets = [u.id for u in User.query.filter(
                    User.designation == jd.title, User.is_active.is_(True)).all()]
            added = 0
            for uid in targets:
                if not JDAcknowledgement.query.filter_by(
                        jd_id=jid, user_id=uid).first():
                    db.session.add(JDAcknowledgement(
                        jd_id=jid, user_id=uid, jd_version=jd.version,
                        assigned_by=current_user.id))
                    notify_user(uid, "jd",
                                f"Your Job Description is ready: {jd.title}",
                                "Please read and acknowledge with your "
                                "signature.",
                                link=_link("engagement.my_jd"))
                    added += 1
            audit_record("jd_assigned", f"{jd.title} -> {added} employee(s)",
                         entity="jd", entity_id=jid,
                         actor_id=current_user.id)
            db.session.commit()
            flash(f"Assigned to {added} employee(s).", "success")
            return redirect(url_for("engagement.jd_detail", jid=jid))
        if action == "remind":
            uid = request.form.get("user_id", "")
            ack = JDAcknowledgement.query.filter_by(jd_id=jid,
                                                    user_id=uid).first()
            if ack:
                notify_user(uid, "jd",
                            f"Reminder: acknowledge your Job Description "
                            f"({jd.title})",
                            "Please read and sign it in ESS.",
                            link=url_for("engagement.my_jd"))
                audit_record("jd_reminded", f"{jd.title} -> {uid}",
                             entity="jd", entity_id=jid,
                             actor_id=current_user.id)
                db.session.commit()
                flash(f"Reminder sent to {uid}.", "success")
            return redirect(url_for("engagement.jd_detail", jid=jid))
    acks = JDAcknowledgement.query.filter_by(jd_id=jid).all()
    users = {u.id: u for u in User.query.all()}
    pending = [a for a in acks if a.status != "acknowledged"
               or a.jd_version < jd.version]
    departments = sorted({u.department for u in User.query.all()
                          if u.department})
    return render_template("hr_jd_detail.html", jd=jd, acks=acks, users=users,
                           pending=pending, departments=departments)


@engagement_bp.route("/hrms/jds/<int:jid>/material")
@login_required
@admin_required
def jd_material(jid):
    jd = JobDescription.query.get_or_404(jid)
    if not jd.material_path:
        abort(404)
    data = read_file("docs", jd.material_path)
    if not data:
        abort(404)
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     download_name=f"jd-{jid}.pdf")


@engagement_bp.route("/my-jd")
@login_required
def my_jd():
    acks = JDAcknowledgement.query.filter_by(
        user_id=current_user.id).order_by(
        JDAcknowledgement.assigned_at.desc()).all()
    jds = {j.id: j for j in JobDescription.query.filter(
        JobDescription.id.in_([a.jd_id for a in acks])).all()} if acks else {}
    return render_template("my_jd.html", acks=acks, jds=jds)


@engagement_bp.route("/my-jd/<int:aid>", methods=["GET", "POST"])
@login_required
def my_jd_detail(aid):
    ack = JDAcknowledgement.query.get_or_404(aid)
    if ack.user_id != current_user.id:
        abort(403)
    jd = JobDescription.query.get_or_404(ack.jd_id)
    if request.method == "POST":
        typed = (request.form.get("typed_name") or "").strip()[:120]
        sig_data = (request.form.get("signature") or "").strip()
        agreed = request.form.get("agree") == "on"
        if not agreed:
            flash("Please tick the confirmation checkbox.", "error")
        elif not typed:
            flash("Please type your full name.", "error")
        elif not sig_data.startswith("data:image/png;base64,"):
            flash("Please draw your signature on the pad.", "error")
        else:
            import base64
            try:
                raw = base64.b64decode(sig_data.split(",", 1)[1])
            except Exception:
                raw = b""
            jpg, err = image_from_upload(raw, 600, 80)
            if err:
                flash(err, "error")
            else:
                stored = store_file(jpg, SIG_DIR, "jpg")
                ack.signature_path = stored
                ack.typed_name = typed
                ack.signer_ip = request.remote_addr
                ack.status = "acknowledged"
                ack.jd_version = jd.version
                ack.acknowledged_at = datetime.utcnow()
                # v3.4.1: tamper evidence — hash of the exact text
                # acknowledged, plus the request user agent.
                import hashlib
                ack.jd_text_hash = hashlib.sha256(
                    (jd.title + "\n" + (jd.body or "") + "\n"
                     + str(jd.version)).encode("utf-8")).hexdigest()
                ack.user_agent = request.headers.get("User-Agent", "")[:255]
                audit_record("jd_acknowledged",
                             f"{jd.title} v{jd.version} by {typed} "
                             f"(hash {ack.jd_text_hash[:12]}...)",
                             entity="jd", entity_id=jd.id,
                             actor_id=current_user.id,
                             old_value=ack.status, new_value="acknowledged")
                db.session.commit()
                flash("Job description acknowledged. Thank you.", "success")
                return redirect(url_for("engagement.my_jd_detail", aid=aid))
        return redirect(url_for("engagement.my_jd_detail", aid=aid))
    if ack.status == "assigned":
        ack.status = "viewed"
        ack.viewed_at = datetime.utcnow()
        audit_record("jd_viewed", jd.title, entity="jd", entity_id=jd.id,
                     actor_id=current_user.id)
        db.session.commit()
    return render_template("my_jd_detail.html", ack=ack, jd=jd)


@engagement_bp.route("/jd-signature/<int:aid>")
@login_required
def jd_signature(aid):
    """Private signature image: owner or admin only."""
    ack = JDAcknowledgement.query.get_or_404(aid)
    if current_user.id != ack.user_id and not current_user.is_admin:
        abort(403)
    if current_user.id != ack.user_id:
        audit_record("jd_signature_viewed", f"ack #{aid} of {ack.user_id}",
                     entity="jd", entity_id=ack.jd_id,
                     actor_id=current_user.id)
        db.session.commit()
    data = read_file(SIG_DIR, ack.signature_path) if ack.signature_path else None
    if not data:
        abort(404)
    return send_file(io.BytesIO(data), mimetype="image/jpeg",
                     download_name=f"signature-{aid}.jpg")
