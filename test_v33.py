"""HRMS+ESS v3.3 tests: employee experience.

1. Sick-leave balance hidden from employees (HR views keep it).
2. Mobile geo + selfie check-in/out (locations, punch accept/reject,
   photo resize, 45-day retention purge, photo permissions).
3. Notices (mandatory-read acks), notifications, celebrations (birthday /
   anniversary generation, wishes, reactions).
4. Training LMS-lite (courses, questions, assignments, quiz, cert issuance).
5. Job descriptions with digital-signature acknowledgement + versioning.
6. Regression: payroll.basic_salary() with SalaryAssignment (hot-fix).

Uses an isolated temp SQLite DB and test fixtures only (no real employee data).
Run: rm -f /tmp/test_v33.db && ESS_SECRET_KEY=testsecret12345 \\
     ESS_DATABASE_URL=sqlite:////tmp/test_v33.db python test_v33.py
"""
import base64
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app, db  # noqa: E402
from app import engagement  # noqa: E402
from app.models import (User, LeaveBalance, AttendanceRecord, AuditLog,
                        WorkLocation, PunchRecord, Notice, NoticeAck,
                        Notification, CelebrationPost, CelebrationWish,
                        TrainingCourse, QuizQuestion, TrainingAssignment,
                        TrainingAttempt, JobDescription, JDAcknowledgement,
                        SalaryComponent, SalaryAssignment, Certification,
                        ShiftPattern, RosterAssignment)  # noqa: E402
from app import payroll  # noqa: E402
import config as app_config  # noqa: E402

app = create_app()
app.config["WTF_CSRF_ENABLED"] = True
from app import limiter  # noqa: E402
limiter.enabled = False  # many logins in this suite; rate limiting is live-tested separately
client = app.test_client()

passed, failed = [], []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra else ""))


def csrf_token(resp):
    m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    return m.group(1).decode() if m else ""


def get_token(path):
    return csrf_token(client.get(path))


def login(uid, pw):
    r = client.get("/login")
    t = csrf_token(r)
    return client.post("/login", data={"username": uid, "password": pw,
                                       "csrf_token": t},
                       follow_redirects=False)


def logout():
    t = csrf_token(client.get("/change-password"))
    return client.post("/logout", data={"csrf_token": t},
                       follow_redirects=False)


def mk(uid, name, role, dept, **kw):
    if not User.query.get(uid):
        kw.setdefault("joining_date", date(2020, 1, 15))
        u = User(id=uid, name=name, role=role, department=dept,
                 employment_status="active", is_active=True, **kw)
        u.set_password("pw-" + uid)
        u.must_change_password = False
        db.session.add(u)
    return User.query.get(uid)


def png_bytes(w=100, h=100, color="red"):
    from PIL import Image
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


gst_today = engagement.gst_now().date()

with app.app_context():
    admin = mk("T33-ADM", "T33 Admin", "admin", "Human Resources",
               basic_salary=9000, monthly_salary=14000)
    mgr = mk("T33-MGR", "T33 Manager", "manager", "Production",
             basic_salary=6000, monthly_salary=9000)
    emp = mk("T33-E1", "T33 Employee One", "employee", "Production",
             designation="Welder", basic_salary=3000, monthly_salary=4500,
             reporting_manager="T33-MGR")
    emp2 = mk("T33-E2", "T33 Employee Two", "employee", "Production",
              designation="Driver", basic_salary=1400, monthly_salary=2400,
              reporting_manager="T33-MGR")
    db.session.add(LeaveBalance(user_id="T33-E1", leave_type="annual",
                                entitled=30, used=5))
    db.session.add(LeaveBalance(user_id="T33-E1", leave_type="sick",
                                entitled=90, used=2))
    db.session.commit()

# ================= 1) sick balance hidden from employees =================
login("T33-E1", "pw-T33-E1")
r = client.get("/")
check("dashboard 200", r.status_code == 200)
check("dashboard leave balances show annual",
      b">Annual<" in r.data)
check("dashboard does NOT show a sick balance row",
      b">Sick<" not in r.data)
r = client.get("/leave")
check("leave page 200", r.status_code == 200)
check("leave page still lists sick leave type for applications",
      b"Sick Leave" in r.data)
# sick leave application still works
t = get_token("/leave")
sick_start = (gst_today + timedelta(days=5)).isoformat()
sick_end = (gst_today + timedelta(days=6)).isoformat()
r = client.post("/leave", data={
    "csrf_token": t, "leave_type": "sick",
    "start_date": sick_start, "end_date": sick_end,
    "reason": "Medical certificate attached"}, follow_redirects=False)
check("sick leave application accepted", r.status_code in (301, 302, 303))
logout()
login("T33-ADM", "pw-T33-ADM")
r = client.get("/hrms/reports/leave")
check("HR leave report 200", r.status_code == 200)
check("HR leave report still shows sick balances", b"Sick" in r.data)

# ================= 2) geo + selfie punch =================
t = get_token("/hrms/locations")
r = client.post("/hrms/locations", data={
    "csrf_token": t, "action": "save", "name": "Test Factory",
    "latitude": "25.00000", "longitude": "55.00000", "radius_m": "500",
    "is_active": "on"}, follow_redirects=False)
check("location created (redirect)", r.status_code in (301, 302, 303))
r = client.get("/hrms/locations")
check("locations page shows the site", b"Test Factory" in r.data)
logout()
login("T33-E1", "pw-T33-E1")
r = client.get("/attendance/punch")
check("punch page 200", r.status_code == 200)
check("punch page mentions camera/https guidance", b"HTTPS" in r.data)

photo = png_bytes(1600, 1200)
r = client.post("/attendance/punch/submit", data={
    "csrf_token": get_token("/attendance/punch"),
    "punch_type": "in", "latitude": "25.00000", "longitude": "55.00000",
    "accuracy": "12",
    "photo": (io.BytesIO(photo), "selfie.png")},
    content_type="multipart/form-data")
j = r.get_json()
check("punch inside geo-fence accepted", r.status_code == 200 and j["ok"])
check("punch reports matched location", j.get("location") == "Test Factory")
with app.app_context():
    pr = PunchRecord.query.filter_by(user_id="T33-E1",
                                     punch_type="in").first()
    check("punch record stored", pr is not None)
    check("punch photo stored", bool(pr and pr.photo_path))
    check("punch distance small", pr is not None and pr.distance_m < 50)
    rec = AttendanceRecord.query.filter_by(
        user_id="T33-E1", date=gst_today).first()
    check("attendance record created with check-in",
          rec is not None and rec.check_in is not None)
    # stored photo is resized (uploaded 1600px -> <= 800px) and is JPEG
    from app.files import read_file
    data = read_file("punches", pr.photo_path)
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    check("stored punch photo resized to <= 800px wide", img.width <= 800,
          f"got {img.width}")
    check("stored punch photo is JPEG", data[:3] == b"\xff\xd8\xff")
    in_id = pr.id

# reject: 5 km away
r = client.post("/attendance/punch/submit", data={
    "csrf_token": get_token("/attendance/punch"),
    "punch_type": "out", "latitude": "25.04500", "longitude": "55.04500",
    "accuracy": "12",
    "photo": (io.BytesIO(photo), "selfie.png")},
    content_type="multipart/form-data")
j = r.get_json()
check("punch outside geo-fence rejected", r.status_code == 403 and not j["ok"])
check("rejection mentions distance", "away" in j.get("error", ""))
with app.app_context():
    rej = AuditLog.query.filter_by(action="punch_rejected").first()
    check("rejected punch audit-logged", rej is not None)

# late marking via roster: shift starting at 00:00, grace 0 -> always late
logout()
login("T33-ADM", "pw-T33-ADM")
with app.app_context():
    sp = ShiftPattern(code="T33D", name="Dawn", start_time="00:00",
                      end_time="08:00")
    db.session.add(sp)
    db.session.flush()
    db.session.add(RosterAssignment(user_id="T33-E2", date=gst_today,
                                   shift_id=sp.id, created_by="T33-ADM"))
    db.session.commit()
logout()
login("T33-E2", "pw-T33-E2")
r = client.post("/attendance/punch/submit", data={
    "csrf_token": get_token("/attendance/punch"),
    "punch_type": "in", "latitude": "25.00000", "longitude": "55.00000",
    "accuracy": "8",
    "photo": (io.BytesIO(photo), "selfie.png")},
    content_type="multipart/form-data")
with app.app_context():
    rec2 = AttendanceRecord.query.filter_by(
        user_id="T33-E2", date=gst_today).first()
    check("late status from roster grace", rec2 is not None
          and rec2.status == "late", rec2.status if rec2 else "no rec")

# photo permissions
logout()
login("T33-E2", "pw-T33-E2")
r = client.get(f"/attendance/punch-photo/{in_id}")
check("punch photo 403 for unrelated employee", r.status_code == 403)
logout()
login("T33-E1", "pw-T33-E1")
r = client.get(f"/attendance/punch-photo/{in_id}")
check("punch photo 200 for owner", r.status_code == 200)
logout()
login("T33-ADM", "pw-T33-ADM")
r = client.get(f"/attendance/punch-photo/{in_id}")
check("punch photo 200 for admin", r.status_code == 200)
r = client.get("/hrms/punches")
check("HR punch list 200", r.status_code == 200)
check("HR punch list shows punch", b"T33-E1" in r.data)

# retention purge: 46-day photo deleted, 44-day kept, record kept
with app.app_context():
    from app.files import store_file as _store
    old_data = png_bytes(50, 50)
    old_name = _store(old_data, "punches", "jpg")
    old = PunchRecord(user_id="T33-E1", punch_type="in",
                      punched_at=datetime.utcnow() - timedelta(days=46),
                      local_date=gst_today - timedelta(days=46),
                      photo_path=old_name,
                      created_at=datetime.utcnow() - timedelta(days=46))
    db.session.add(old)
    new_name = _store(old_data, "punches", "jpg")
    newp = PunchRecord(user_id="T33-E1", punch_type="in",
                       punched_at=datetime.utcnow() - timedelta(days=44),
                       local_date=gst_today - timedelta(days=44),
                       photo_path=new_name,
                       created_at=datetime.utcnow() - timedelta(days=44))
    db.session.add(newp)
    db.session.commit()
    old_id, new_id = old.id, newp.id
with app.app_context():
    n = engagement.purge_punch_photos(45)
check("purge deleted the 46-day photo", n == 1, f"got {n}")
with app.app_context():
    from app.files import read_file as _read
    o = PunchRecord.query.get(old_id)
    nw = PunchRecord.query.get(new_id)
    check("46-day record kept but photo cleared",
          o is not None and o.photo_path is None)
    check("44-day photo survives purge",
          nw is not None and nw.photo_path is not None
          and _read("punches", nw.photo_path) is not None)

# ================= 3) notices / notifications / celebrations =================
# notice with mandatory ack
t = get_token("/hrms/notices")
r = client.post("/hrms/notices", data={
    "csrf_token": t, "action": "create", "title": "T33 Mandatory Notice",
    "body": "Please read and acknowledge.", "audience": "all",
    "requires_ack": "on"}, follow_redirects=False)
check("notice created (redirect)", r.status_code in (301, 302, 303))
with app.app_context():
    n = Notice.query.filter_by(title="T33 Mandatory Notice").first()
    nid = n.id
    total_users = User.query.filter_by(is_active=True).count()
    fanout = Notification.query.filter_by(ref_id=nid).count()
    check("notice fan-out == active employees", fanout == total_users,
          f"{fanout}/{total_users}")
r = client.get("/hrms/notices")
check("HR notices page 200", r.status_code == 200)
check("HR page shows ack counts header", b"Acknowledged:" in r.data)
logout()
login("T33-E1", "pw-T33-E1")
r = client.get("/")
check("dashboard flags unacknowledged mandatory notice",
      b"T33 Mandatory Notice" in r.data and b"Action needed" in r.data)
r = client.get(f"/notices/{nid}")
check("notice view 200", r.status_code == 200)
check("notice view shows Acknowledge action",
      b"Acknowledge" in r.data)
t = get_token(f"/notices/{nid}")
r = client.post(f"/notices/{nid}", data={"csrf_token": t},
                follow_redirects=False)
check("ack recorded (redirect)", r.status_code in (301, 302, 303))
r = client.post(f"/notices/{nid}", data={
    "csrf_token": get_token(f"/notices/{nid}")}, follow_redirects=False)
with app.app_context():
    acks = NoticeAck.query.filter_by(notice_id=nid,
                                     user_id="T33-E1").count()
    check("ack recorded exactly once", acks == 1, f"got {acks}")
r = client.get("/")
check("dashboard no longer flags after ack",
      b"Action needed" not in r.data)
logout()
login("T33-ADM", "pw-T33-ADM")
r = client.get("/hrms/notices")
check("HR page shows acked count 1", b"1 /" in r.data)
check("HR page lists non-acknowledger (T33-E2)", b"T33-E2" in r.data)

# celebrations: birthday + anniversary generation
with app.app_context():
    bday = mk("T33-BD", "T33 Birthday", "employee", "Production",
              date_of_birth=gst_today, joining_date=gst_today - timedelta(days=400))
    ann = mk("T33-AN", "T33 Anniversary", "employee", "Production",
             date_of_birth=date(1990, 1, 2),
             joining_date=date(gst_today.year - 3, gst_today.month,
                               gst_today.day))
    db.session.commit()
    before = CelebrationPost.query.count()
    # reset the once-a-day guard (the dashboard run above already set it)
    from app.models import AppSetting
    AppSetting.set("celebrations_last_run", "")
    db.session.commit()
with app.app_context():
    posts = engagement.ensure_todays_celebrations()
with app.app_context():
    after = CelebrationPost.query.count()
    bp = CelebrationPost.query.filter_by(user_id="T33-BD",
                                         kind="birthday",
                                         post_date=gst_today).first()
    ap = CelebrationPost.query.filter_by(user_id="T33-AN",
                                         kind="anniversary",
                                         post_date=gst_today).first()
    check("birthday post created", bp is not None)
    check("anniversary post created with years=3",
          ap is not None and ap.years == 3)
    total_now = User.query.filter_by(is_active=True).count()
    to_bd = Notification.query.filter_by(
        kind="birthday", ref_id=bp.id).count()
    check("birthday notifications to every other employee",
          to_bd == total_now - 1, f"{to_bd}/{total_now - 1}")
    pid = bp.id
with app.app_context():
    posts2 = engagement.ensure_todays_celebrations()
with app.app_context():
    dup = CelebrationPost.query.filter_by(user_id="T33-BD", kind="birthday",
                                          post_date=gst_today).count()
    check("generation idempotent (still 1 birthday post)", dup == 1)

# wishes + reactions
logout()
login("T33-E1", "pw-T33-E1")
r = client.get("/celebrations")
check("celebrations page 200", r.status_code == 200)
check("celebrations page shows birthday", b"T33 Birthday" in r.data)
t = get_token("/celebrations")
r = client.post("/celebrations", data={
    "csrf_token": t, "post_id": str(pid), "message": "Happy birthday!" ,
    "reaction": ""}, follow_redirects=False)
check("wish saved (redirect)", r.status_code in (301, 302, 303))
with app.app_context():
    w = CelebrationWish.query.filter_by(post_id=pid,
                                         user_id="T33-E1").first()
    check("wish row stored", w is not None and w.message == "Happy birthday!")
    notif = Notification.query.filter_by(kind="wish", user_id="T33-BD").first()
    check("celebrated employee notified of wish",
          notif is not None and "Happy birthday" in (notif.title or ""))
r = client.post("/celebrations", data={
    "csrf_token": get_token("/celebrations"), "post_id": str(pid),
    "reaction": "\U0001f44d", "message": ""}, follow_redirects=False)
with app.app_context():
    w = CelebrationWish.query.filter_by(post_id=pid,
                                         user_id="T33-E1").first()
    check("reaction stored on same row", w.reaction == "\U0001f44d"
          and w.message == "Happy birthday!")
r = client.post("/celebrations", data={
    "csrf_token": get_token("/celebrations"), "post_id": str(pid),
    "reaction": "\U0001f389", "message": ""}, follow_redirects=False)
with app.app_context():
    rows = CelebrationWish.query.filter_by(post_id=pid,
                                            user_id="T33-E1").count()
    w = CelebrationWish.query.filter_by(post_id=pid,
                                         user_id="T33-E1").first()
    check("re-react replaces (still 1 row)", rows == 1 and w.reaction == "\U0001f389")
# own post: message rejected, reaction allowed
logout()
login("T33-BD", "pw-T33-BD")
r = client.post("/celebrations", data={
    "csrf_token": get_token("/celebrations"), "post_id": str(pid),
    "message": "to myself"}, follow_redirects=True)
with app.app_context():
    mine = CelebrationWish.query.filter_by(post_id=pid,
                                            user_id="T33-BD").first()
    check("own-post written wish rejected", mine is None)
check("own-post rejection flash shown", b"yourself" in r.data)
r = client.post("/celebrations", data={
    "csrf_token": get_token("/celebrations"), "post_id": str(pid),
    "reaction": "\U0001f44d"}, follow_redirects=False)
with app.app_context():
    mine = CelebrationWish.query.filter_by(post_id=pid,
                                            user_id="T33-BD").first()
    check("own-post reaction allowed",
          mine is not None and mine.reaction == "\U0001f44d")

# notifications list + mark read
logout()
login("T33-BD", "pw-T33-BD")
r = client.get("/notifications")
check("notifications list 200", r.status_code == 200)
check("notifications list shows wish", b"wished you" in r.data)
with app.app_context():
    nn = Notification.query.filter_by(kind="wish",
                                      user_id="T33-BD").first()
    nnid = nn.id
    unread_before = Notification.query.filter_by(user_id="T33-BD",
                                                 is_read=False).count()
r = client.post(f"/notifications/read/{nnid}",
                data={"csrf_token": get_token("/notifications")},
                follow_redirects=False)
with app.app_context():
    check("mark read works",
          Notification.query.get(nnid).is_read is True)
r = client.post("/notifications/read-all",
                data={"csrf_token": get_token("/notifications")},
                follow_redirects=False)
with app.app_context():
    left = Notification.query.filter_by(user_id="T33-BD",
                                        is_read=False).count()
    check("read-all clears unread", left == 0, f"{left} left")
logout()
login("T33-ADM", "pw-T33-ADM")

# ================= 4) training LMS-lite =================
t = get_token("/hrms/training")
r = client.post("/hrms/training", data={
    "csrf_token": t, "action": "create", "title": "T33 Safety Basics",
    "category": "Safety", "description": "Test course",
    "pass_percent": "70", "issue_cert_name": "T33 Safety Cert",
    "cert_valid_months": "12"}, follow_redirects=False)
check("course created (redirect)", r.status_code in (301, 302, 303))
with app.app_context():
    c = TrainingCourse.query.filter_by(title="T33 Safety Basics").first()
    cid = c.id
    check("course row stored", c is not None and c.pass_percent == 70)
r = client.post(f"/hrms/training/{cid}", data={
    "csrf_token": get_token(f"/hrms/training/{cid}"),
    "action": "add_question", "question": "What is PPE?",
    "opt_0": "Personal protective equipment", "opt_1": "A printer",
    "opt_2": "A policy", "opt_3": "A phone", "correct": "0"},
    follow_redirects=False)
with app.app_context():
    q = QuizQuestion.query.filter_by(course_id=cid).first()
    qid = q.id
    check("question added", q is not None and q.correct_index == 0)
r = client.post(f"/hrms/training/{cid}", data={
    "csrf_token": get_token(f"/hrms/training/{cid}"),
    "action": "assign", "scope": "dept:Production",
    "single_user": ""}, follow_redirects=False)
with app.app_context():
    a1 = TrainingAssignment.query.filter_by(course_id=cid,
                                             user_id="T33-E1").first()
    a2 = TrainingAssignment.query.filter_by(course_id=cid,
                                             user_id="T33-E2").first()
    aX = TrainingAssignment.query.filter_by(course_id=cid,
                                             user_id="T33-ADM").first()
    check("department assignment reaches dept employees",
          a1 is not None and a2 is not None)
    check("department assignment skips other departments", aX is None)
    notif = Notification.query.filter_by(kind="training",
                                         user_id="T33-E1").first()
    check("assignee notified", notif is not None)
logout()
login("T33-E2", "pw-T33-E2")  # assignee but will fail the quiz
r = client.get("/my-training")
check("my-training 200", r.status_code == 200)
check("my-training shows assigned course", b"T33 Safety Basics" in r.data)
r = client.get(f"/my-training/{cid}")
check("course page 200 for assignee", r.status_code == 200)
r = client.get(f"/my-training/{cid}/quiz")
check("quiz page 200", r.status_code == 200)
check("quiz shows the question", b"What is PPE?" in r.data)
r = client.post(f"/my-training/{cid}/quiz", data={
    "csrf_token": get_token(f"/my-training/{cid}/quiz"),
    f"q_{qid}": "3"}, follow_redirects=False)
with app.app_context():
    att = TrainingAttempt.query.filter_by(course_id=cid,
                                           user_id="T33-E2").first()
    check("wrong answers -> 0% fail",
          att is not None and att.score_percent == 0.0
          and att.passed is False)
logout()
login("T33-E1", "pw-T33-E1")
r = client.post(f"/my-training/{cid}/quiz", data={
    "csrf_token": get_token(f"/my-training/{cid}/quiz"),
    f"q_{qid}": "0"}, follow_redirects=False)
with app.app_context():
    att = TrainingAttempt.query.filter_by(course_id=cid,
                                           user_id="T33-E1").first()
    check("all correct -> 100% pass",
          att is not None and att.score_percent == 100.0
          and att.passed is True)
    cert = Certification.query.filter_by(user_id="T33-E1",
                                         name="T33 Safety Cert").first()
    check("pass issues certification when configured", cert is not None)
    check("cert expiry ~12 months",
          cert is not None and cert.expiry_date is not None
          and (cert.expiry_date - gst_today).days in (359, 360, 361))
# material file 403 for a non-assignee
with app.app_context():
    from app.files import store_file as _sf
    cc = TrainingCourse.query.get(cid)
    cc.material_kind = "file"
    cc.material_path = _sf(b"%PDF-1.4 test", "training", "pdf")
    db.session.commit()
logout()
login("T33-ADM", "pw-T33-ADM")
with app.app_context():
    mk("T33-E3", "T33 Employee Three", "employee", "Finance",
       basic_salary=2000, monthly_salary=3000)
    db.session.commit()
logout()
login("T33-E3", "pw-T33-E3")
r = client.get(f"/training/material/{cid}")
check("material 403 for non-assignee", r.status_code == 403)
logout()
login("T33-E1", "pw-T33-E1")
r = client.get(f"/training/material/{cid}")
check("material 200 for assignee", r.status_code == 200)
logout()
login("T33-ADM", "pw-T33-ADM")

# ================= 5) job descriptions =================
with app.app_context():
    mk("T33-W1", "T33 Welder One", "employee", "Production",
       designation="Welder", basic_salary=2500, monthly_salary=4000)
    mk("T33-W2", "T33 Welder Two", "employee", "Production",
       designation="Welder", basic_salary=2500, monthly_salary=4000)
    mk("T33-D1", "T33 Driver One", "employee", "Logistics",
       designation="Driver", basic_salary=1400, monthly_salary=2400)
    db.session.commit()
t = get_token("/hrms/jds")
r = client.post("/hrms/jds", data={
    "csrf_token": t, "action": "create", "title": "Welder",
    "department": "Production", "reports_to": "Production Manager",
    "body": "JOB PURPOSE\nWeld fixtures.\n\nACCOUNTABILITIES\n1. Weld.\n2. Grind."},
    follow_redirects=False)
check("JD created (redirect)", r.status_code in (301, 302, 303))
with app.app_context():
    jd = JobDescription.query.filter_by(title="Welder").first()
    jid = jd.id
r = client.post(f"/hrms/jds/{jid}", data={
    "csrf_token": get_token(f"/hrms/jds/{jid}"),
    "action": "assign", "scope": "by_designation"}, follow_redirects=False)
with app.app_context():
    acked_uids = {a.user_id for a in JDAcknowledgement.query.filter_by(
        jd_id=jid).all()}
    check("assign-by-designation hits welders only",
          {"T33-W1", "T33-W2"} <= acked_uids and "T33-D1" not in acked_uids,
          str(sorted(acked_uids)))
    notif = Notification.query.filter_by(kind="jd",
                                         user_id="T33-W1").first()
    check("assignment notifies employees", notif is not None)
r = client.get(f"/hrms/jds/{jid}")
check("HR pending list shows unacknowledged welder", b"T33-W1" in r.data)
# employee views -> status viewed
logout()
login("T33-W1", "pw-T33-W1")
r = client.get("/my-jd")
check("my-jd 200", r.status_code == 200)
check("my-jd lists the JD", b"Welder" in r.data)
with app.app_context():
    ack = JDAcknowledgement.query.filter_by(jd_id=jid,
                                             user_id="T33-W1").first()
    aid = ack.id
r = client.get(f"/my-jd/{aid}")
check("my-jd detail 200", r.status_code == 200)
check("detail shows JD body", b"Weld fixtures." in r.data)
with app.app_context():
    ack = JDAcknowledgement.query.get(aid)
    check("opening JD marks status viewed",
          ack.status == "viewed" and ack.viewed_at is not None)
# acknowledge without signature -> error, still not acknowledged
r = client.post(f"/my-jd/{aid}", data={
    "csrf_token": get_token(f"/my-jd/{aid}"),
    "typed_name": "T33 Welder One", "agree": "on",
    "signature": ""}, follow_redirects=False)
with app.app_context():
    ack = JDAcknowledgement.query.get(aid)
    check("ack without signature rejected",
          ack.status == "viewed" and ack.signature_path is None)
# acknowledge with a real PNG signature payload
sig_png = png_bytes(400, 160, "white")
sig_b64 = base64.b64encode(sig_png).decode()
r = client.post(f"/my-jd/{aid}", data={
    "csrf_token": get_token(f"/my-jd/{aid}"),
    "typed_name": "T33 Welder One", "agree": "on",
    "signature": "data:image/png;base64," + sig_b64},
    follow_redirects=False)
with app.app_context():
    ack = JDAcknowledgement.query.get(aid)
    check("ack saved with signature", ack.status == "acknowledged"
          and ack.signature_path is not None
          and ack.acknowledged_at is not None)
    check("typed name recorded", ack.typed_name == "T33 Welder One")
    check("signer IP recorded", bool(ack.signer_ip))
    from app.files import read_file as _rf
    data = _rf("signatures", ack.signature_path)
    from PIL import Image as _Img
    im = _Img.open(io.BytesIO(data))
    check("stored signature resized <= 600px", im.width <= 600,
          f"got {im.width}")
    audit = AuditLog.query.filter_by(action="jd_acknowledged").first()
    check("acknowledgement audit-logged", audit is not None)
r = client.get(f"/jd-signature/{aid}")
check("signature 200 for owner", r.status_code == 200)
logout()
login("T33-D1", "pw-T33-D1")
r = client.get(f"/jd-signature/{aid}")
check("signature 403 for another employee", r.status_code == 403)
logout()
login("T33-ADM", "pw-T33-ADM")
r = client.get(f"/jd-signature/{aid}")
check("signature 200 for admin", r.status_code == 200)
# version bump -> re-acknowledgement needed
t = get_token(f"/hrms/jds/{jid}")
r = client.post(f"/hrms/jds/{jid}", data={
    "csrf_token": t, "action": "edit", "title": "Welder",
    "department": "Production", "reports_to": "Production Manager",
    "body": "JOB PURPOSE\nWeld fixtures.\n\nACCOUNTABILITIES\n1. Weld.\n2. Grind.\n3. Inspect."},
    follow_redirects=False)
with app.app_context():
    jd = JobDescription.query.get(jid)
    check("body edit bumps version", jd.version == 2, f"v{jd.version}")
logout()
login("T33-W1", "pw-T33-W1")
r = client.get("/my-jd")
check("re-acknowledgement flagged on /my-jd",
      b"Re-acknowledgement needed" in r.data)
# remind sends another notification
logout()
login("T33-ADM", "pw-T33-ADM")
with app.app_context():
    before_n = Notification.query.filter_by(kind="jd",
                                            user_id="T33-W2").count()
r = client.post(f"/hrms/jds/{jid}", data={
    "csrf_token": get_token(f"/hrms/jds/{jid}"),
    "action": "remind", "user_id": "T33-W2"}, follow_redirects=False)
with app.app_context():
    after_n = Notification.query.filter_by(kind="jd",
                                           user_id="T33-W2").count()
    check("remind sends another notification", after_n == before_n + 1,
          f"{before_n}->{after_n}")

# ================= 6) REGRESSION: basic_salary() with assignments =========
# Hot-fix (2026-10-19): payroll.basic_salary() used a[1][1] on the
# {code: (assignment, amount)} dict from assignments_as_of(); fixed to
# a["BASIC"][1]. The bug only fired for users WITH salary assignments.
with app.app_context():
    comp = SalaryComponent.query.filter_by(code="BASIC").first()
    if comp is None:
        comp = SalaryComponent(code="BASIC", name="Basic Salary",
                               kind="earning", is_fixed=True, prorate=True,
                               wps_bucket="basic")
        db.session.add(comp)
        db.session.flush()
    mk("T33-S1", "T33 Salaried", "employee", "Production",
       designation="Welder", basic_salary=1000.0, monthly_salary=1500.0,
       joining_date=date(2018, 1, 15))
    db.session.add(SalaryAssignment(
        user_id="T33-S1", component_id=comp.id, amount=5000.0,
        effective_from=date(2024, 1, 1), created_by="T33-ADM"))
    db.session.commit()
    got = payroll.basic_salary("T33-S1", gst_today)
    check("basic_salary() returns assignment amount for assigned user",
          got == 5000.0, f"got {got}")
    got2 = payroll.basic_salary("T33-E2", gst_today)
    check("basic_salary() falls back to User.basic_salary",
          got2 == 1400.0, f"got {got2}")

# settlement Calculate succeeds for a user WITH salary assignments
logout()
login("T33-ADM", "pw-T33-ADM")
t = get_token("/hrms/settlements/new")
r = client.post("/hrms/settlements/new", data={
    "csrf_token": t, "user_id": "T33-S1",
    "last_working_day": gst_today.isoformat(),
    "separation_type": "Termination", "notice_days": "0",
    "notice_direction": "none", "reason": "regression test"},
    follow_redirects=False)
check("settlement draft created for assigned user",
      r.status_code in (301, 302, 303))
with app.app_context():
    from app.models import FinalSettlement
    st = FinalSettlement.query.filter_by(
        user_id="T33-S1").order_by(FinalSettlement.id.desc()).first()
    sid = st.id
t = get_token(f"/hrms/settlements/{sid}")
r = client.post(f"/hrms/settlements/{sid}/calculate",
                data={"csrf_token": t}, follow_redirects=False)
check("settlement calculate redirects (no 500)",
      r.status_code in (301, 302, 303), f"got {r.status_code}")
with app.app_context():
    st = FinalSettlement.query.get(sid)
    check("settlement calculated", st.status == "calculated", st.status)
    check("settlement lines frozen", len(st.lines) > 0)
    labels = [l.get("label", "") for l in st.lines]
    check("gratuity line present",
          any("ratuity" in lbl or "EOS" in lbl for lbl in labels),
          str(labels[:4]))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
