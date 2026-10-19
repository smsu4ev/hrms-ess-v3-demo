"""V3.6 Frappe-model desk shell: /apps page + Awesome Bar API.

Owns ONLY the desk-shell backend. Template globals consumed by
app/templates/base.html are:
  - ``workspaces``  : list of {key, title, icon, route} (role-filtered)
  - ``ws_groups``   : list of {name, links: [{label, href, icon}]} for the
                      current workspace (hrefs precomputed)
  - ``ws_current``  : the current workspace dict (at least .key and .title)
These three are wired by the app context processor (not this file); this
blueprint additionally injects ``notif_panel`` (latest ~15 notifications for
the slide-over) and ``desk_settings_url`` (settings.page URL when the
settings blueprint is registered, else None) via its own context processor.
"""
from flask import Blueprint, current_app, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app import db, limiter
from app.files import salary_visible
from app.models import EmployeeDocument, LeaveRequest, Notification, User

desk_bp = Blueprint("desk", __name__)

# ---------------- Awesome Bar page registry ----------------
# (label, endpoint, min-role). Roles:
#   all     -> any authenticated user
#   manager -> is_manager (manager or admin)
#   hr      -> admin, HR Manager or HR Staff (back-office HR)
#   admin   -> admin only
#   salary  -> admin or salary-visibility permission
# Endpoints are resolved with url_for() at request time so missing
# blueprints are skipped silently.
PAGE_REGISTRY = [
    # ESS (everyone)
    ("Dashboard", "views.dashboard", "all"),
    ("My Profile", "views.profile", "all"),
    ("Change Password", "auth.change_password", "all"),
    ("Inbox", "extras.inbox", "all"),
    ("My Leave", "views.leave", "all"),
    ("My Attendance", "views.attendance", "all"),
    ("Punch In / Out", "engagement.punch_page", "all"),
    ("My Roster", "views.my_roster", "all"),
    ("Attendance Regularisation", "views.regularisation", "all"),
    ("My Payslips", "views.payslips", "all"),
    ("My Documents", "docs.my_documents", "all"),
    ("My Letters", "extras.my_letters", "all"),
    ("My Expenses", "extras.my_expenses", "all"),
    ("My Gratuity (EOS)", "views.my_gratuity", "all"),
    ("My Training", "engagement.my_training", "all"),
    ("My Job Description", "engagement.my_jd", "all"),
    ("My Tickets", "helpdesk.my_tickets", "all"),
    ("My Shift Swaps", "helpdesk.my_swaps", "all"),
    ("My Evaluations", "views.evaluations", "all"),
    ("Grievances", "views.grievances", "all"),
    ("Directory", "extras.directory", "all"),
    ("Team Calendar", "extras.team_calendar", "all"),
    ("Notices", "engagement.notices", "all"),
    ("Celebrations", "engagement.celebrations", "all"),
    ("Notifications", "engagement.notifications", "all"),
    ("Org Chart", "extras.org_chart", "all"),
    # Manager
    ("Leave Approvals", "views.approvals", "manager"),
    ("Regularisation Approvals", "views.regularisation_approvals", "manager"),
    ("Team Overview", "extras.team_overview", "manager"),
    ("Evaluations (manage)", "views.evaluations_manage", "manager"),
    ("Grievance Inbox", "views.grievance_inbox", "manager"),
    ("Helpdesk Queue", "helpdesk.hr_tickets", "manager"),
    ("Punch Records", "engagement.hr_punches", "manager"),
    ("Shift Patterns", "roster.shifts", "manager"),
    ("Weekly Roster", "roster.roster", "manager"),
    ("Expense Claims", "extras.hr_expenses", "manager"),
    ("Letter Requests", "extras.hr_letter_requests", "manager"),
    ("Separations", "retail.separations", "manager"),
    ("Certifications", "retail.certifications", "manager"),
    ("Training Matrix", "retail.training_matrix", "manager"),
    ("Projects & Deployment", "retail.projects", "manager"),
    ("Accommodation & Transport", "retail.facilities", "manager"),
    ("PPE & Uniform", "retail.ppe", "manager"),
    ("Asset & Tool Custody", "retail.assets", "manager"),
    ("HSE Incidents", "retail.hse", "manager"),
    ("Contractors", "retail.contractors", "manager"),
    # HR back-office
    ("Employee Central", "hrms.employees", "hr"),
    ("Compliance", "hrms.compliance", "hr"),
    ("Gratuity Calculator", "hrms.gratuity_calc", "hr"),
    ("Work Locations", "engagement.locations", "hr"),
    ("Training Courses", "engagement.hr_training", "hr"),
    ("Job Descriptions", "engagement.jds", "hr"),
    ("Document Vault", "docs.hr_documents", "hr"),
    ("Onboarding", "hrms.onboarding", "hr"),
    ("Recruitment", "hrms.recruitment", "hr"),
    ("Approval Matrix", "approvals.matrix_admin", "hr"),
    # Payroll (salary permission)
    ("Payroll Runs", "payroll.runs", "salary"),
    ("Salary Components", "payroll.components", "salary"),
    ("OT Rate Rules", "payroll.ot_rules", "salary"),
    ("Loans & Advances", "payroll.loans", "salary"),
    ("Visa Companies", "payroll.companies", "salary"),
    ("WPS Files", "payroll.wps", "salary"),
    ("Letters", "payroll.letters", "salary"),
    ("Final Settlements", "settlement.settlements", "salary"),
    ("Salary Register Variance", "reports.variance", "salary"),
    ("EOS & Gratuity Liability", "reports.liability", "salary"),
    # Admin
    ("Manage Users", "admin.users", "admin"),
    ("Audit Log", "admin.audit", "admin"),
    ("Import Attendance", "admin.attendance_import", "admin"),
    ("WhatsApp Outbox", "admin.outbox", "admin"),
    ("Reports Hub", "reports.hub", "admin"),
    ("Leave Report", "reports.leave", "admin"),
    ("WPS Export (legacy)", "hrms.wps_export", "admin"),
    ("All Apps", "desk.apps", "all"),
]


def _role_ok(role):
    """Role gate for the page registry / employee search."""
    if role == "all":
        return True
    if role == "manager":
        return bool(current_user.is_manager)
    if role == "hr":
        return bool(current_user.is_admin
                    or getattr(current_user, "is_hr_manager", False)
                    or getattr(current_user, "is_hr_staff", False))
    if role == "admin":
        return bool(current_user.is_admin)
    if role == "salary":
        try:
            return bool(salary_visible(current_user))
        except Exception:
            return False
    return False


def _can_search_employees():
    """Managers + HR back-office may search the employee directory.
    Plain employees only ever see their own record."""
    return _role_ok("manager") or _role_ok("hr")


def _employee_href(u):
    """Where an employee search result should land for the current viewer."""
    try:
        if _role_ok("hr"):
            return url_for("hrms.employee_detail", uid=u.id)
        if current_user.is_manager:
            return url_for("extras.directory")
    except Exception:
        pass
    return url_for("views.profile")


# ---------------- /apps page ----------------
APPS_DESCRIPTIONS = {
    "hr": "People, masters, org chart and HR administration.",
    "hr_setup": "People, masters, org chart and HR administration.",
    "attendance": "Shifts, rosters, punches and attendance registers.",
    "leaves": "Leave applications, balances, holidays and reports.",
    "expenses": "Expense claims, advances and expense reports.",
    "payroll": "Payroll runs, salary slips, loans and WPS files.",
    "performance": "Goals, appraisals and feedback.",
    "tenure": "Onboarding, separations, grievances and training.",
    "recruitment": "Job openings, applicants, interviews and offers.",
    "eos": "Gratuity, EOSB liability and benefits letters.",
    "me": "Your self-service home: leave, attendance, payslips and more.",
    "ess": "Your self-service home: leave, attendance, payslips and more.",
    "my_team": "Team overview, approvals inbox and team calendar.",
}


@desk_bp.route("/apps")
@login_required
def apps():
    """All Services directory: every workspace with its role-filtered link
    groups, rendered as headed tile sections (V3.7 App-UI home rework)."""
    from app.workspaces import visible_workspaces, workspace_nav
    ws_sections = []
    for ws in visible_workspaces(current_user):
        ws_sections.append({"key": ws["key"], "title": ws["title"],
                            "icon": ws["icon"], "route": ws["route"],
                            "groups": workspace_nav(current_user,
                                                    ws["key"])})
    return render_template("apps.html", descriptions=APPS_DESCRIPTIONS,
                           ws_sections=ws_sections)


# ---------------- Awesome Bar API ----------------
@desk_bp.route("/api/awesome")
@login_required
@limiter.limit("120 per minute")
def api_awesome():
    """Role-filtered live search: pages, employees, own documents.

    Employees may search pages + their own record only; managers / HR
    back-office get directory matches on name/employee id (never salary
    data — the EmployeeDocument/User rows returned carry no pay fields).
    """
    q = (request.args.get("q") or "").strip()
    pages, employees, documents = [], [], []
    if q:
        ql = q.lower()
        for label, endpoint, role in PAGE_REGISTRY:
            if ql not in label.lower() or not _role_ok(role):
                continue
            try:
                pages.append({"label": label, "href": url_for(endpoint)})
            except Exception:
                continue  # blueprint not registered (yet) — skip quietly
        like = "%{}%".format(q.replace("%", "").replace("_", ""))
        if _can_search_employees():
            emp_rows = (User.query
                        .filter(User.is_active.is_(True))
                        .filter((User.name.ilike(like)) | (User.id.ilike(like)))
                        .order_by(User.name).limit(8).all())
        else:
            emp_rows = []
            me = current_user
            if ql in (me.name or "").lower() or ql in str(me.id).lower():
                emp_rows = [me]
        for u in emp_rows:
            employees.append({"id": u.id, "name": u.name,
                              "dept": u.department or "",
                              "href": _employee_href(u)})
        # Own documents only (title / category match).
        doc_rows = (EmployeeDocument.query
                    .filter_by(user_id=current_user.id)
                    .filter((EmployeeDocument.title.ilike(like))
                            | (EmployeeDocument.category.ilike(like)))
                    .order_by(EmployeeDocument.created_at.desc())
                    .limit(6).all())
        try:
            docs_href = url_for("docs.my_documents")
        except Exception:
            docs_href = None
        for d in doc_rows:
            documents.append({"id": d.id, "title": d.title,
                              "category": d.category or "",
                              "href": docs_href})
    return jsonify(pages=pages[:12], employees=employees[:8],
                   documents=documents[:6])


# ---------------- UI theme preference (v3.9) ----------------
THEME_KEYS = ("nixtio", "glass", "indigo", "night")


@desk_bp.route("/api/ui-theme", methods=["POST"])
@login_required
def api_ui_theme():
    """Persist the user's theme choice. Cosmetic preference — unaudited.

    Invalid keys are rejected with 400; valid keys update
    current_user.ui_theme and return {"ok": true, "theme": key}.
    """
    data = request.get_json(silent=True) or {}
    key = (data.get("theme") or "").strip().lower()
    if key not in THEME_KEYS:
        return jsonify({"ok": False, "error": "invalid theme"}), 400
    current_user.ui_theme = key
    db.session.commit()
    return jsonify({"ok": True, "theme": key})


# ---------------- desk-shell template globals ----------------
@desk_bp.app_context_processor
def inject_desk_globals():
    """Slide-over notifications (latest ~15) + optional Settings URL.

    ``workspaces`` / ``ws_groups`` / ``ws_current`` are wired by the app
    context processor elsewhere; this only adds desk-shell extras.
    v3.8: also injects ``approvals_pending`` (pending leave requests for
    manager+ users) for the pill-nav Approvals badge; the dashboard view
    passes its own value which takes precedence there.
    """
    if not getattr(current_user, "is_authenticated", False):
        return {"notif_panel": [], "desk_settings_url": None,
                "approvals_pending": 0}
    try:
        items = (Notification.query
                 .filter_by(user_id=current_user.id)
                 .order_by(Notification.created_at.desc())
                 .limit(15).all())
    except Exception:
        items = []
    settings_url = None
    try:
        if "settings.page" in current_app.view_functions:
            settings_url = url_for("settings.page")
    except Exception:
        settings_url = None
    approvals_pending = 0
    try:
        if current_user.is_manager:
            approvals_pending = LeaveRequest.query.filter_by(
                status="pending").count()
    except Exception:
        approvals_pending = 0
    return {"notif_panel": items, "desk_settings_url": settings_url,
            "approvals_pending": approvals_pending}
