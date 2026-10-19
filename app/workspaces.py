"""V3.6 workspace registry + workspace home pages (workstream B).

Owns:
  - WORKSPACES: the registry consumed by the desk shell (workstream A).
  - ws_has(user, roles), visible_workspaces(user), workspace_nav(user, key),
    ws_key_for_endpoint(endpoint): the exact interface contract for A.
  - Blueprint ``workspaces`` with GET /ws/<key> home pages.

Do NOT touch app/__init__.py, base.html, style.css or other modules from here.
Home pages extend templates/base.html and use only existing styles/icons.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, current_app, g, render_template, url_for
from flask_login import current_user, login_required
from werkzeug.routing import BuildError

from app import db
from app.approvals import inbox_for
from app.files import salary_visible
from app.models import (
    ApprovalMatrix,
    AttendanceRecord,
    Candidate,
    Company,
    EmployeeDocument,
    Evaluation,
    ExpenseClaim,
    FinalSettlement,
    GeneratedLetter,
    Grievance,
    LeaveBalance,
    LeaveRequest,
    LetterTemplate,
    Loan,
    Notice,
    Notification,
    OTRateRule,
    OnboardingCase,
    PayrollRun,
    PunchRecord,
    RegularisationRequest,
    RosterAssignment,
    SalaryComponent,
    SeparationRecord,
    ShiftPattern,
    TrainingCourse,
    User,
    WorkLocation,
)
from app.models_v35 import HelpdeskTicket, ShiftSwapRequest


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------

def ws_has(user, roles):
    """True if ``user`` may see something gated by ``roles``.

    Roles: "admin" -> user.is_admin; "hr" -> is_admin or is_hr_staff or
    is_hr_manager; "manager" -> user.is_manager; "salary" ->
    salary_visible(user) from app.files; "employee" -> True (everyone).
    """
    for r in roles or []:
        if r == "admin" and user.is_admin:
            return True
        if r == "hr" and (
            user.is_admin or user.is_hr_staff or user.is_hr_manager
        ):
            return True
        if r == "manager" and user.is_manager:
            return True
        if r == "salary" and salary_visible(user):
            return True
        if r == "employee":
            return True
    return False


def _href(endpoint, kwargs=None):
    """Resolve an endpoint to a URL.

    Primary path: url_for (works in a request context). Fallback: build the
    path from the URL map so workspace_nav() also works outside a request
    context (e.g. unit tests, CLI). The only registry endpoint that may not
    exist yet is ``settings.page`` (provided by workstream D); it resolves
    automatically once registered — until then the link degrades to "#".
    """
    import re

    kwargs = dict(kwargs or {})
    try:
        return url_for(endpoint, **kwargs)
    except (BuildError, RuntimeError):
        pass
    try:
        url_map = current_app.url_map
    except RuntimeError:
        return "#"
    for rule in url_map.iter_rules():
        if rule.endpoint != endpoint:
            continue
        if set(rule.arguments or ()) != set(kwargs):
            continue
        path = str(rule)
        for k, v in kwargs.items():
            path = re.sub(r"<(?:[^:<>]+:)?" + re.escape(k) + r">",
                          str(v), path)
        if "<" not in path:
            return path
    return "#"


def _home_link(key, roles):
    """Nav link back to this workspace's home page."""
    return {"label": "Home", "endpoint": "workspaces.home", "icon": "home",
            "roles": roles, "endpoint_kwargs": {"key": key}}


# ---------------------------------------------------------------------------
# Registry — the interface contract workstream A consumes.
# ---------------------------------------------------------------------------
#
# Each entry: {"key", "title", "icon", "route", "roles", "desc", "groups"}.
# Each group:  {"name", "links"}.
# Each link:   {"label", "endpoint", "icon", "roles"}; "endpoint_kwargs" is
#              optional and is passed to url_for (used for workspaces.home).
# Icon names must exist in app/templates/_icons.html.
# Every verified page endpoint appears in at least one workspace; detail
# pages that need URL args (grievance_detail, payslip, etc.) are reached
# from their list pages and intentionally have no nav link.

WORKSPACES = [
    {
        "key": "hr-setup", "title": "HR Setup", "icon": "building",
        "route": "/ws/hr-setup", "roles": ["admin", "hr"],
        "desc": "Masters, HR settings, approval matrix, audit and admin tools.",
        "groups": [
            {"name": "Masters", "links": [
                _home_link("hr-setup", ["admin", "hr"]),
                {"label": "Employee Central", "endpoint": "hrms.employees",
                 "icon": "users", "roles": ["admin", "hr"]},
                {"label": "New Employee", "endpoint": "hrms.employee_new",
                 "icon": "usercheck", "roles": ["admin", "hr"]},
                {"label": "Organizational Chart", "endpoint": "extras.org_chart",
                 "icon": "chart", "roles": ["admin", "hr"]},
                {"label": "Visa Companies", "endpoint": "payroll.companies",
                 "icon": "building", "roles": ["admin", "hr"]},
                {"label": "Work Locations", "endpoint": "engagement.locations",
                 "icon": "building", "roles": ["admin", "hr"]},
                {"label": "Shift Patterns", "endpoint": "roster.shifts",
                 "icon": "clock", "roles": ["admin", "hr"]},
                {"label": "Salary Components", "endpoint": "payroll.components",
                 "icon": "card", "roles": ["admin", "hr"]},
                {"label": "OT Rate Rules", "endpoint": "payroll.ot_rules",
                 "icon": "stopwatch", "roles": ["admin", "hr"]},
            ]},
            {"name": "Settings & Audit", "links": [
                {"label": "HR Settings", "endpoint": "settings.page",
                 "icon": "menu", "roles": ["admin", "hr"]},
                {"label": "Admin Users", "endpoint": "admin.users",
                 "icon": "user", "roles": ["admin", "hr"]},
                {"label": "Approval Matrix", "endpoint": "approvals.matrix_admin",
                 "icon": "check", "roles": ["admin", "hr"]},
                {"label": "Audit Trail", "endpoint": "admin.audit",
                 "icon": "file", "roles": ["admin", "hr"]},
                {"label": "Export Audit (CSV)", "endpoint": "admin.audit_export",
                 "icon": "download", "roles": ["admin", "hr"]},
                {"label": "Reports & Exports", "endpoint": "reports.hub",
                 "icon": "report", "roles": ["admin", "hr"]},
                {"label": "Letter Requests", "endpoint": "extras.hr_letter_requests",
                 "icon": "mail", "roles": ["admin", "hr"]},
                {"label": "Notice Board", "endpoint": "engagement.notices",
                 "icon": "bell", "roles": ["admin", "hr"]},
                {"label": "WhatsApp Outbox", "endpoint": "admin.outbox",
                 "icon": "chat", "roles": ["admin", "hr"]},
                {"label": "Import Attendance", "endpoint": "admin.attendance_import",
                 "icon": "download", "roles": ["admin", "hr"]},
            ]},
        ],
    },
    {
        "key": "shift-attendance", "title": "Shift & Attendance", "icon": "clock",
        "route": "/ws/shift-attendance",
        "roles": ["admin", "hr", "manager", "employee"],
        "desc": "Check-in, attendance, rosters, regularisation and shift setup.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("shift-attendance",
                           ["admin", "hr", "manager", "employee"]),
                {"label": "Dashboard", "endpoint": "views.dashboard",
                 "icon": "home", "roles": ["admin", "hr", "manager", "employee"]},
            ]},
            {"name": "Attendance", "links": [
                {"label": "Check-in", "endpoint": "engagement.punch_page",
                 "icon": "stopwatch", "roles": ["admin", "hr", "manager", "employee"]},
                {"label": "Attendance", "endpoint": "views.attendance",
                 "icon": "clock", "roles": ["admin", "hr", "manager", "employee"]},
                {"label": "Punch Records", "endpoint": "engagement.hr_punches",
                 "icon": "clipboard", "roles": ["admin", "hr", "manager"]},
                {"label": "Regularisation", "endpoint": "views.regularisation",
                 "icon": "pencil", "roles": ["admin", "hr", "manager", "employee"]},
                {"label": "Regularisation Approvals",
                 "endpoint": "views.regularisation_approvals",
                 "icon": "check", "roles": ["admin", "hr", "manager"]},
                {"label": "My Roster", "endpoint": "views.my_roster",
                 "icon": "calendar", "roles": ["admin", "hr", "manager", "employee"]},
            ]},
            {"name": "Shift Setup", "links": [
                {"label": "Shift Patterns", "endpoint": "roster.shifts",
                 "icon": "clock", "roles": ["admin", "hr"]},
                {"label": "Work Locations", "endpoint": "engagement.locations",
                 "icon": "building", "roles": ["admin", "hr"]},
                {"label": "Roster Settings", "endpoint": "roster.roster_settings",
                 "icon": "menu", "roles": ["admin", "hr"]},
            ]},
            {"name": "Roster", "links": [
                {"label": "Roster Grid", "endpoint": "roster.roster",
                 "icon": "calendar", "roles": ["admin", "hr", "manager"]},
            ]},
            {"name": "Reports", "links": [
                {"label": "Monthly Attendance", "endpoint": "reports.absence",
                 "icon": "report", "roles": ["admin", "hr", "manager"]},
                {"label": "Roster Variance", "endpoint": "roster.roster_variance",
                 "icon": "chart", "roles": ["admin", "hr", "manager"]},
            ]},
        ],
    },
    {
        "key": "leaves", "title": "Leaves", "icon": "calendar",
        "route": "/ws/leaves", "roles": ["admin", "hr", "manager", "employee"],
        "desc": "Leave applications, approvals inbox, leave reports and config.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("leaves", ["admin", "hr", "manager", "employee"]),
                {"label": "Leave Application", "endpoint": "views.leave",
                 "icon": "calendar", "roles": ["admin", "hr", "manager", "employee"]},
            ]},
            {"name": "Approvals", "links": [
                {"label": "Approvals Inbox", "endpoint": "views.approvals",
                 "icon": "check", "roles": ["admin", "hr", "manager"]},
            ]},
            {"name": "Reports", "links": [
                {"label": "Leave Report", "endpoint": "reports.leave",
                 "icon": "report", "roles": ["admin", "hr", "manager"]},
            ]},
            {"name": "Setup", "links": [
                {"label": "Holiday & Leave Config", "endpoint": "hrms.compliance",
                 "icon": "menu", "roles": ["admin", "hr"]},
            ]},
        ],
    },
    {
        "key": "expenses", "title": "Expenses", "icon": "money",
        "route": "/ws/expenses", "roles": ["admin", "hr", "manager", "employee"],
        "desc": "Expense claims, employee advances and HR expense review.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("expenses", ["admin", "hr", "manager", "employee"]),
                {"label": "Expense Claims", "endpoint": "extras.my_expenses",
                 "icon": "money", "roles": ["admin", "hr", "manager", "employee"]},
            ]},
            {"name": "HR Review", "links": [
                {"label": "HR Expenses", "endpoint": "extras.hr_expenses",
                 "icon": "clipboard", "roles": ["admin", "hr"]},
            ]},
            {"name": "Advances", "links": [
                {"label": "Employee Advances", "endpoint": "payroll.loans",
                 "icon": "card", "roles": ["admin", "salary"]},
            ]},
        ],
    },
    {
        "key": "payroll", "title": "Payroll", "icon": "card",
        "route": "/ws/payroll", "roles": ["admin", "salary"],
        "desc": "Payroll runs, salary inputs, WPS files and payroll reports.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("payroll", ["admin", "salary"]),
                {"label": "Payroll Runs", "endpoint": "payroll.runs",
                 "icon": "money", "roles": ["admin", "salary"]},
                {"label": "Salary Assignments", "endpoint": "hrms.employees",
                 "icon": "users", "roles": ["admin", "salary"]},
                {"label": "Salary Slips", "endpoint": "views.payslips",
                 "icon": "card", "roles": ["admin", "salary"]},
            ]},
            {"name": "Payroll Inputs", "links": [
                {"label": "Additions & Deductions", "endpoint": "reports.addded",
                 "icon": "pencil", "roles": ["admin", "salary"]},
                {"label": "Overtime Summary", "endpoint": "reports.ot_summary",
                 "icon": "stopwatch", "roles": ["admin", "salary"]},
                {"label": "Loans & Advances", "endpoint": "payroll.loans",
                 "icon": "card", "roles": ["admin", "salary"]},
            ]},
            {"name": "WPS", "links": [
                {"label": "WPS Files", "endpoint": "payroll.wps",
                 "icon": "building", "roles": ["admin", "salary"]},
                {"label": "WPS Export", "endpoint": "hrms.wps_export",
                 "icon": "download", "roles": ["admin", "salary"]},
                {"label": "WPS Pre-flight", "endpoint": "reports.wps_preflight",
                 "icon": "check", "roles": ["admin", "salary"]},
            ]},
            {"name": "Setup", "links": [
                {"label": "Salary Components", "endpoint": "payroll.components",
                 "icon": "card", "roles": ["admin", "salary"]},
                {"label": "OT Rate Rules", "endpoint": "payroll.ot_rules",
                 "icon": "stopwatch", "roles": ["admin", "salary"]},
            ]},
            {"name": "Reports", "links": [
                {"label": "Salary Register", "endpoint": "reports.master",
                 "icon": "report", "roles": ["admin", "salary"]},
                {"label": "Salary Variance", "endpoint": "reports.variance",
                 "icon": "chart", "roles": ["admin", "salary"]},
                {"label": "Loans Report", "endpoint": "reports.loans",
                 "icon": "file", "roles": ["admin", "salary"]},
            ]},
        ],
    },
    {
        "key": "performance", "title": "Performance", "icon": "medal",
        "route": "/ws/performance", "roles": ["manager", "hr"],
        "desc": "Evaluations, appraisals and goals for managers and HR.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("performance", ["manager", "hr"]),
                {"label": "Evaluations", "endpoint": "views.evaluations_manage",
                 "icon": "medal", "roles": ["manager", "hr"]},
                {"label": "My Evaluations", "endpoint": "views.evaluations",
                 "icon": "star", "roles": ["manager", "hr"]},
                {"label": "Goals", "endpoint": "views.evaluations",
                 "icon": "check", "roles": ["manager", "hr"]},
            ]},
        ],
    },
    {
        "key": "tenure", "title": "Tenure", "icon": "briefcase",
        "route": "/ws/tenure", "roles": ["hr", "admin"],
        "desc": "Onboarding, separations, settlements, grievances and training.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("tenure", ["hr", "admin"]),
            ]},
            {"name": "Lifecycle", "links": [
                {"label": "Onboarding", "endpoint": "hrms.onboarding",
                 "icon": "usercheck", "roles": ["hr", "admin"]},
                {"label": "Separations", "endpoint": "retail.separations",
                 "icon": "logout", "roles": ["hr", "admin"]},
                {"label": "Final Settlements", "endpoint": "settlement.settlements",
                 "icon": "scales", "roles": ["hr", "admin"]},
                {"label": "New Settlement", "endpoint": "settlement.new",
                 "icon": "pencil", "roles": ["hr", "admin"]},
            ]},
            {"name": "Grievance", "links": [
                {"label": "Grievances", "endpoint": "views.grievance_inbox",
                 "icon": "chat", "roles": ["hr", "admin"]},
            ]},
            {"name": "Training", "links": [
                {"label": "Training Courses", "endpoint": "engagement.hr_training",
                 "icon": "graduation", "roles": ["hr", "admin"]},
                {"label": "Training Matrix", "endpoint": "retail.training_matrix",
                 "icon": "chart", "roles": ["hr", "admin"]},
                {"label": "Job Descriptions", "endpoint": "engagement.jds",
                 "icon": "file", "roles": ["hr", "admin"]},
            ]},
            {"name": "Reports", "links": [
                {"label": "Separation Report", "endpoint": "reports.sep_report",
                 "icon": "report", "roles": ["hr", "admin"]},
                {"label": "Turnover Report", "endpoint": "reports.turnover",
                 "icon": "chart", "roles": ["hr", "admin"]},
            ]},
            {"name": "Workplace & Safety", "links": [
                {"label": "Certifications", "endpoint": "retail.certifications",
                 "icon": "medal", "roles": ["hr", "admin"]},
                {"label": "Projects", "endpoint": "retail.projects",
                 "icon": "briefcase", "roles": ["hr", "admin"]},
                {"label": "Facilities", "endpoint": "retail.facilities",
                 "icon": "building", "roles": ["hr", "admin"]},
                {"label": "PPE", "endpoint": "retail.ppe",
                 "icon": "shirt", "roles": ["hr", "admin"]},
                {"label": "Assets", "endpoint": "retail.assets",
                 "icon": "clipboard", "roles": ["hr", "admin"]},
                {"label": "HSE", "endpoint": "retail.hse",
                 "icon": "alert", "roles": ["hr", "admin"]},
                {"label": "Contractors", "endpoint": "retail.contractors",
                 "icon": "truck", "roles": ["hr", "admin"]},
            ]},
        ],
    },
    {
        "key": "recruitment", "title": "Recruitment", "icon": "usercheck",
        "route": "/ws/recruitment", "roles": ["hr", "admin"],
        "desc": "Recruitment tracker, offer letters and letter templates.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("recruitment", ["hr", "admin"]),
                {"label": "Recruitment Tracker", "endpoint": "hrms.recruitment",
                 "icon": "usercheck", "roles": ["hr", "admin"]},
            ]},
            {"name": "Letters", "links": [
                {"label": "Offer Letters", "endpoint": "payroll.letters",
                 "icon": "mail", "roles": ["hr", "admin"]},
                {"label": "New Letter", "endpoint": "payroll.letter_new",
                 "icon": "pencil", "roles": ["hr", "admin"]},
                {"label": "Letter Templates", "endpoint": "payroll.letter_templates",
                 "icon": "file", "roles": ["hr", "admin"]},
            ]},
        ],
    },
    {
        "key": "eos-benefits", "title": "EOS & Benefits", "icon": "scales",
        "route": "/ws/eos-benefits", "roles": ["hr", "admin", "salary"],
        "desc": "Gratuity, end-of-service compliance, liability and letters.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("eos-benefits", ["hr", "admin", "salary"]),
                {"label": "Gratuity Calculator", "endpoint": "hrms.gratuity_calc",
                 "icon": "scales", "roles": ["hr", "admin"]},
                {"label": "Compliance", "endpoint": "hrms.compliance",
                 "icon": "check", "roles": ["hr", "admin"]},
                {"label": "Leave & EOSB Liability",
                 "endpoint": "reports.liability",
                 "icon": "report", "roles": ["admin", "salary"]},
                {"label": "My EOS Gratuity", "endpoint": "views.my_gratuity",
                 "icon": "money", "roles": ["hr", "admin", "salary", "employee"]},
                {"label": "Letters", "endpoint": "payroll.letters",
                 "icon": "mail", "roles": ["hr", "admin"]},
            ]},
        ],
    },
    {
        "key": "me", "title": "Me", "icon": "user",
        "route": "/ws/me",
        "roles": ["admin", "hr", "manager", "salary", "employee"],
        "desc": "Your employee self-service home: profile, leave, pay and more.",
        "groups": [
            {"name": "Overview", "links": [
                _home_link("me", ["admin", "hr", "manager", "salary", "employee"]),
                {"label": "My Profile", "endpoint": "views.profile",
                 "icon": "user", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My Documents", "endpoint": "docs.my_documents",
                 "icon": "file", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My Letters", "endpoint": "extras.my_letters",
                 "icon": "mail", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My Expenses", "endpoint": "extras.my_expenses",
                 "icon": "money", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My EOS Gratuity", "endpoint": "views.my_gratuity",
                 "icon": "scales", "roles": ["admin", "hr", "manager", "salary", "employee"]},
            ]},
            {"name": "Time & Leave", "links": [
                {"label": "My Attendance", "endpoint": "views.attendance",
                 "icon": "clock", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "Check-in", "endpoint": "engagement.punch_page",
                 "icon": "stopwatch", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My Roster", "endpoint": "views.my_roster",
                 "icon": "calendar", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My Leave", "endpoint": "views.leave",
                 "icon": "calendar", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "Regularisation", "endpoint": "views.regularisation",
                 "icon": "pencil", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "Team Calendar", "endpoint": "extras.team_calendar",
                 "icon": "calendar", "roles": ["admin", "hr", "manager", "salary", "employee"]},
            ]},
            {"name": "Pay", "links": [
                {"label": "My Payslips", "endpoint": "views.payslips",
                 "icon": "card", "roles": ["admin", "hr", "manager", "salary", "employee"]},
            ]},
            {"name": "Growth", "links": [
                {"label": "My Training", "endpoint": "engagement.my_training",
                 "icon": "graduation", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My JD", "endpoint": "engagement.my_jd",
                 "icon": "file", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My Evaluations", "endpoint": "views.evaluations",
                 "icon": "medal", "roles": ["admin", "hr", "manager", "salary", "employee"]},
            ]},
            {"name": "Support", "links": [
                {"label": "Inbox", "endpoint": "extras.inbox",
                 "icon": "mail", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "Notifications", "endpoint": "engagement.notifications",
                 "icon": "bell", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My Tickets", "endpoint": "helpdesk.my_tickets",
                 "icon": "chat", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "My Shift Swaps", "endpoint": "helpdesk.my_swaps",
                 "icon": "clock", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "Grievances", "endpoint": "views.grievances",
                 "icon": "alert", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "Celebrations", "endpoint": "engagement.celebrations",
                 "icon": "star", "roles": ["admin", "hr", "manager", "salary", "employee"]},
            ]},
            {"name": "Company", "links": [
                {"label": "Directory", "endpoint": "extras.directory",
                 "icon": "users", "roles": ["admin", "hr", "manager", "salary", "employee"]},
                {"label": "Org Chart", "endpoint": "extras.org_chart",
                 "icon": "chart", "roles": ["admin", "hr", "manager", "salary", "employee"]},
            ]},
        ],
    },
    {
        "key": "my-team", "title": "My Team", "icon": "users",
        "route": "/ws/my-team", "roles": ["manager"],
        "desc": "Your team's approvals, calendar, roster and support queue.",
        "groups": [
            {"name": "Overview", "links": [
                {"label": "Home", "endpoint": "extras.team_overview",
                 "icon": "home", "roles": ["manager"]},
            ]},
            {"name": "Manage", "links": [
                {"label": "Approvals", "endpoint": "views.approvals",
                 "icon": "check", "roles": ["manager"]},
                {"label": "Team Calendar", "endpoint": "extras.team_calendar",
                 "icon": "calendar", "roles": ["manager"]},
                {"label": "Roster", "endpoint": "roster.roster",
                 "icon": "calendar", "roles": ["manager"]},
                {"label": "Punch Records", "endpoint": "engagement.hr_punches",
                 "icon": "clipboard", "roles": ["manager"]},
                {"label": "Helpdesk Queue", "endpoint": "helpdesk.hr_tickets",
                 "icon": "chat", "roles": ["manager"]},
                {"label": "Team Absence Today",
                 "endpoint": "extras.team_overview_absent",
                 "icon": "absent", "roles": ["manager"]},
            ]},
        ],
    },
]

_WS_BY_KEY = {w["key"]: w for w in WORKSPACES}


# ---------------------------------------------------------------------------
# Workspace home pages: real counts from the models (cheap queries only)
# ---------------------------------------------------------------------------

def _shared_counts(user):
    """Cheap shared counters used by the home-page number cards."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_prefix = today.strftime("%Y-%m")
    c = {}
    c["today"] = today
    c["active_employees"] = User.query.filter(
        User.employment_status.in_(["active", "probation"]),
        User.is_active.is_(True)).count()
    c["pending_approval_users"] = User.query.filter_by(
        employment_status="pending_approval").count()
    c["companies"] = Company.query.count()
    c["shift_patterns"] = ShiftPattern.query.count()
    c["locations"] = WorkLocation.query.count()
    c["salary_components"] = SalaryComponent.query.count()
    c["ot_rules"] = OTRateRule.query.count()
    c["matrix_processes"] = ApprovalMatrix.query.count()
    try:
        c["pending_approvals"] = len(inbox_for(user))
    except Exception:
        c["pending_approvals"] = 0
    c["unread_notifications"] = Notification.query.filter_by(
        user_id=user.id, is_read=False).count()
    c["my_pending_leave"] = LeaveRequest.query.filter_by(
        user_id=user.id, status="pending").count()
    c["my_leave_requests"] = LeaveRequest.query.filter_by(
        user_id=user.id).count()
    c["my_leave_balance"] = round(sum(
        max(0.0, (b.entitled or 0) - (b.used or 0))
        for b in LeaveBalance.query.filter_by(user_id=user.id).all()), 1)
    c["leave_this_month"] = LeaveRequest.query.filter(
        LeaveRequest.created_at >= datetime(today.year, today.month, 1)).count()
    c["today_punches"] = PunchRecord.query.filter_by(
        local_date=today).count()
    c["i_punched_today"] = PunchRecord.query.filter_by(
        user_id=user.id, local_date=today, punch_type="in").count() > 0
    c["my_shifts_week"] = RosterAssignment.query.filter_by(
        user_id=user.id).filter(RosterAssignment.date >= week_start).count()
    c["roster_week"] = RosterAssignment.query.filter(
        RosterAssignment.date >= week_start).count()
    c["pending_regularisations"] = RegularisationRequest.query.filter_by(
        status="pending").count()
    c["my_pending_expenses"] = ExpenseClaim.query.filter_by(
        user_id=user.id, status="pending").count()
    c["my_expenses_year"] = ExpenseClaim.query.filter(
        ExpenseClaim.user_id == user.id,
        ExpenseClaim.claim_date >= date(today.year, 1, 1)).count()
    c["pending_expenses"] = ExpenseClaim.query.filter_by(
        status="pending").count()
    c["active_loans"] = Loan.query.filter_by(status="active").count()
    c["payroll_runs_year"] = PayrollRun.query.filter(
        PayrollRun.period.like(f"{today.year}%")).count()
    c["draft_runs"] = PayrollRun.query.filter_by(status="draft").count()
    c["wps_ready_runs"] = PayrollRun.query.filter(
        PayrollRun.status.in_(["locked", "approved"])).count()
    c["my_open_tickets"] = HelpdeskTicket.query.filter_by(
        requester_id=user.id).filter(
            HelpdeskTicket.status.in_(["open", "in_progress"])).count()
    c["open_tickets"] = HelpdeskTicket.query.filter(
        HelpdeskTicket.status.in_(["open", "in_progress"])).count()
    c["my_documents"] = EmployeeDocument.query.filter_by(
        user_id=user.id).count()
    c["open_onboarding"] = OnboardingCase.query.count()
    c["open_separations"] = SeparationRecord.query.filter(
        SeparationRecord.final_settlement_status != "Settled").count()
    c["open_grievances"] = Grievance.query.filter(
        Grievance.status.in_(["submitted", "under_review", "in_progress"])
    ).count()
    c["open_settlements"] = FinalSettlement.query.filter(
        FinalSettlement.status != "paid").count()
    c["letters_year"] = GeneratedLetter.query.filter(
        GeneratedLetter.created_at >= datetime(today.year, 1, 1)).count()
    c["candidates_active"] = Candidate.query.filter(
        ~Candidate.stage.in_(["Joined", "Rejected"])).count()
    c["candidates_interview"] = Candidate.query.filter_by(
        stage="Interview").count()
    c["candidates_offer"] = Candidate.query.filter_by(stage="Offer").count()
    c["letter_templates"] = LetterTemplate.query.count()
    c["notices"] = Notice.query.count()
    c["active_courses"] = TrainingCourse.query.filter_by(
        is_active=True).count()
    c["team_size"] = User.query.filter_by(
        reporting_manager=user.id, is_active=True).count()
    team_ids = [u.id for u in User.query.filter_by(
        reporting_manager=user.id, is_active=True).all()]
    c["team_ids"] = team_ids
    c["team_absent_today"] = (
        AttendanceRecord.query.filter(
            AttendanceRecord.user_id.in_(team_ids),
            AttendanceRecord.date == today,
            AttendanceRecord.status == "absent").count() if team_ids else 0)
    # visa expiries within 20 days (active staff with a known date)
    soon = today + timedelta(days=20)
    c["visa_expiring"] = User.query.filter(
        User.visa_expiry_date.isnot(None),
        User.visa_expiry_date >= today,
        User.visa_expiry_date <= soon,
        User.is_active.is_(True)).count()
    # attendance this week, company-wide
    wk = AttendanceRecord.query.filter(AttendanceRecord.date >= week_start)
    c["present_week"] = wk.filter_by(status="present").count()
    c["absent_week"] = wk.filter_by(status="absent").count()
    c["leave_week"] = wk.filter(
        AttendanceRecord.status.in_(["leave", "holiday"])).count()
    my_wk = wk.filter_by(user_id=user.id)
    c["my_present_month"] = AttendanceRecord.query.filter_by(
        user_id=user.id).filter(
            AttendanceRecord.date >= date(today.year, today.month, 1),
            AttendanceRecord.status == "present").count()
    c["_my_wk"] = my_wk  # kept for builders that need detail
    c["my_evals"] = Evaluation.query.filter_by(user_id=user.id).count()
    c["my_evals_unack"] = Evaluation.query.filter_by(
        user_id=user.id, acknowledged=False).count()
    c["evals_unack"] = Evaluation.query.filter_by(
        acknowledged=False).count()
    return c


def _greeting(user):
    hour = datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    first = (user.name or user.id).split()[0]
    return f"Good {part}, {first}"


def _bars(pairs):
    """Normalise [(label, value)] for the inline SVG bar chart."""
    mx = max([v for _, v in pairs] + [1])
    return [{"label": k, "value": v,
             "pct": round(100.0 * v / mx) if mx else 0} for k, v in pairs]


def _home_hr_setup(u, c):
    return {
        "subtitle": "Company masters, settings, approvals and audit — in one place.",
        "checklist": {"title": "Get to know HR Setup", "steps": [
            {"label": "Define visa companies", "endpoint": "payroll.companies",
             "done": c["companies"] > 0},
            {"label": "Add work locations", "endpoint": "engagement.locations",
             "done": c["locations"] > 0},
            {"label": "Create shift patterns", "endpoint": "roster.shifts",
             "done": c["shift_patterns"] > 0},
            {"label": "Configure salary components",
             "endpoint": "payroll.components", "done": c["salary_components"] > 0},
            {"label": "Set up OT rate rules", "endpoint": "payroll.ot_rules",
             "done": c["ot_rules"] > 0},
            {"label": "Review the approval matrix",
             "endpoint": "approvals.matrix_admin", "done": c["matrix_processes"] > 0},
        ]},
        "numbers": [
            {"label": "Active employees", "value": c["active_employees"],
             "endpoint": "hrms.employees", "icon": "users"},
            {"label": "Pending approvals", "value": c["pending_approvals"],
             "endpoint": "views.approvals", "icon": "check"},
            {"label": "Visa companies", "value": c["companies"],
             "endpoint": "payroll.companies", "icon": "building"},
            {"label": "Shift patterns", "value": c["shift_patterns"],
             "endpoint": "roster.shifts", "icon": "clock"},
            {"label": "Open tickets", "value": c["open_tickets"],
             "endpoint": "helpdesk.hr_tickets", "icon": "chat"},
            {"label": "Visa expiries (20d)", "value": c["visa_expiring"],
             "endpoint": "hrms.employees", "icon": "alert"},
        ],
        "chart": {"title": "Employees by status",
                  "bars": _bars([
                      ("Active", c["active_employees"]),
                      ("Pending approval", c["pending_approval_users"]),
                  ])},
        "link_counts": {"views.approvals": c["pending_approvals"],
                        "helpdesk.hr_tickets": c["open_tickets"],
                        "hrms.employees": c["active_employees"],
                        "payroll.companies": c["companies"],
                        "roster.shifts": c["shift_patterns"],
                        "engagement.locations": c["locations"]},
    }


def _home_shift_attendance(u, c):
    return {
        "subtitle": "Check in, track attendance and manage the roster.",
        "checklist": {"title": "Get to know Shift & Attendance", "steps": [
            {"label": "Create shift patterns", "endpoint": "roster.shifts",
             "done": c["shift_patterns"] > 0},
            {"label": "Add work locations", "endpoint": "engagement.locations",
             "done": c["locations"] > 0},
            {"label": "Assign this week's roster", "endpoint": "roster.roster",
             "done": c["roster_week"] > 0},
            {"label": "Check in today", "endpoint": "engagement.punch_page",
             "done": c["i_punched_today"]},
        ]},
        "numbers": [
            {"label": "Punches today", "value": c["today_punches"],
             "endpoint": "engagement.hr_punches", "icon": "stopwatch"},
            {"label": "Present this week", "value": c["present_week"],
             "endpoint": "views.attendance", "icon": "check"},
            {"label": "Absent this week", "value": c["absent_week"],
             "endpoint": "views.attendance", "icon": "absent"},
            {"label": "My shifts this week", "value": c["my_shifts_week"],
             "endpoint": "views.my_roster", "icon": "calendar"},
            {"label": "Pending regularisations",
             "value": c["pending_regularisations"],
             "endpoint": "views.regularisation_approvals", "icon": "pencil"},
        ],
        "chart": {"title": "Attendance this week",
                  "bars": _bars([
                      ("Present", c["present_week"]),
                      ("Absent", c["absent_week"]),
                      ("Leave / holiday", c["leave_week"]),
                  ])},
        "link_counts": {"engagement.hr_punches": c["today_punches"],
                        "views.regularisation_approvals": c["pending_regularisations"],
                        "views.my_roster": c["my_shifts_week"]},
    }


def _home_leaves(u, c):
    return {
        "subtitle": "Apply for leave, clear approvals and review leave reports.",
        "checklist": {"title": "Get to know Leaves", "steps": [
            {"label": "Configure holidays & leave rules",
             "endpoint": "hrms.compliance", "done": None},
            {"label": "Allocate leave balances", "endpoint": "reports.leave",
             "done": None},
            {"label": "Submit your first leave request",
             "endpoint": "views.leave", "done": c["my_leave_requests"] > 0},
            {"label": "Clear the approvals inbox", "endpoint": "views.approvals",
             "done": c["pending_approvals"] == 0},
        ]},
        "numbers": [
            {"label": "My pending requests", "value": c["my_pending_leave"],
             "endpoint": "views.leave", "icon": "calendar"},
            {"label": "My leave balance (days)", "value": c["my_leave_balance"],
             "endpoint": "views.leave", "icon": "star"},
            {"label": "Awaiting my approval", "value": c["pending_approvals"],
             "endpoint": "views.approvals", "icon": "check"},
            {"label": "Requests this month", "value": c["leave_this_month"],
             "endpoint": "reports.leave", "icon": "report"},
        ],
        "chart": {"title": "Leave requests this month",
                  "bars": _bars([
                      ("Pending", LeaveRequest.query.filter(
                          LeaveRequest.status == "pending",
                          LeaveRequest.created_at >= datetime(
                              date.today().year, date.today().month, 1)
                      ).count()),
                      ("Approved", LeaveRequest.query.filter(
                          LeaveRequest.status == "approved",
                          LeaveRequest.created_at >= datetime(
                              date.today().year, date.today().month, 1)
                      ).count()),
                      ("Rejected", LeaveRequest.query.filter(
                          LeaveRequest.status == "rejected",
                          LeaveRequest.created_at >= datetime(
                              date.today().year, date.today().month, 1)
                      ).count()),
                  ])},
        "link_counts": {"views.approvals": c["pending_approvals"],
                        "views.leave": c["my_pending_leave"]},
    }


def _home_expenses(u, c):
    return {
        "subtitle": "Claim expenses, review the HR queue and manage advances.",
        "checklist": {"title": "Get to know Expenses", "steps": [
            {"label": "Review the approval matrix",
             "endpoint": "approvals.matrix_admin",
             "done": c["matrix_processes"] > 0},
            {"label": "Submit an expense claim", "endpoint": "extras.my_expenses",
             "done": c["my_expenses_year"] > 0},
            {"label": "Clear pending claims", "endpoint": "extras.hr_expenses",
             "done": c["pending_expenses"] == 0},
        ]},
        "numbers": [
            {"label": "My pending claims", "value": c["my_pending_expenses"],
             "endpoint": "extras.my_expenses", "icon": "money"},
            {"label": "My claims this year", "value": c["my_expenses_year"],
             "endpoint": "extras.my_expenses", "icon": "file"},
            {"label": "Claims awaiting HR", "value": c["pending_expenses"],
             "endpoint": "extras.hr_expenses", "icon": "clipboard"},
            {"label": "Active loans", "value": c["active_loans"],
             "endpoint": "payroll.loans", "icon": "card"},
        ],
        "chart": {"title": "Expense claims by status",
                  "bars": _bars([
                      ("Pending", c["pending_expenses"]),
                      ("Approved", ExpenseClaim.query.filter_by(
                          status="approved").count()),
                      ("Rejected", ExpenseClaim.query.filter_by(
                          status="rejected").count()),
                  ])},
        "link_counts": {"extras.my_expenses": c["my_pending_expenses"],
                        "extras.hr_expenses": c["pending_expenses"],
                        "payroll.loans": c["active_loans"]},
    }


def _home_payroll(u, c):
    return {
        "subtitle": "Payroll runs, salary inputs, WPS files and payroll reports.",
        "checklist": {"title": "Get to know Payroll", "steps": [
            {"label": "Define visa companies", "endpoint": "payroll.companies",
             "done": c["companies"] > 0},
            {"label": "Configure salary components",
             "endpoint": "payroll.components", "done": c["salary_components"] > 0},
            {"label": "Create your first payroll run",
             "endpoint": "payroll.runs", "done": c["payroll_runs_year"] > 0},
            {"label": "Run the WPS pre-flight check",
             "endpoint": "reports.wps_preflight", "done": None},
        ]},
        "numbers": [
            {"label": "Payroll runs this year", "value": c["payroll_runs_year"],
             "endpoint": "payroll.runs", "icon": "money"},
            {"label": "Draft runs", "value": c["draft_runs"],
             "endpoint": "payroll.runs", "icon": "pencil"},
            {"label": "WPS-ready runs", "value": c["wps_ready_runs"],
             "endpoint": "payroll.wps", "icon": "building"},
            {"label": "Active loans", "value": c["active_loans"],
             "endpoint": "payroll.loans", "icon": "card"},
            {"label": "Salary components", "value": c["salary_components"],
             "endpoint": "payroll.components", "icon": "card"},
        ],
        "chart": {"title": "Payroll runs by status",
                  "bars": _bars([
                      ("Draft", c["draft_runs"]),
                      ("In review", PayrollRun.query.filter_by(
                          status="in_review").count()),
                      ("Locked", PayrollRun.query.filter_by(
                          status="locked").count()),
                      ("Approved", PayrollRun.query.filter_by(
                          status="approved").count()),
                  ])},
        "link_counts": {"payroll.runs": c["draft_runs"],
                        "payroll.wps": c["wps_ready_runs"],
                        "payroll.loans": c["active_loans"]},
    }


def _home_performance(u, c):
    return {
        "subtitle": "Evaluations, appraisals and goals for your team.",
        "checklist": {"title": "Get to know Performance", "steps": [
            {"label": "Acknowledge your evaluations",
             "endpoint": "views.evaluations", "done": c["my_evals_unack"] == 0},
            {"label": "Review team evaluations",
             "endpoint": "views.evaluations_manage", "done": None},
            {"label": "Set goals for the next cycle",
             "endpoint": "views.evaluations", "done": None},
        ]},
        "numbers": [
            {"label": "Awaiting acknowledgement", "value": c["evals_unack"],
             "endpoint": "views.evaluations_manage", "icon": "medal"},
            {"label": "My evaluations", "value": c["my_evals"],
             "endpoint": "views.evaluations", "icon": "star"},
            {"label": "My unacknowledged", "value": c["my_evals_unack"],
             "endpoint": "views.evaluations", "icon": "alert"},
            {"label": "Pending approvals", "value": c["pending_approvals"],
             "endpoint": "views.approvals", "icon": "check"},
        ],
        "chart": {"title": "Evaluations",
                  "bars": _bars([
                      ("Acknowledged", c["my_evals"] - c["my_evals_unack"]),
                      ("Unacknowledged", c["my_evals_unack"]),
                  ])},
        "link_counts": {"views.evaluations_manage": c["evals_unack"],
                        "views.evaluations": c["my_evals_unack"],
                        "views.approvals": c["pending_approvals"]},
    }


def _home_tenure(u, c):
    return {
        "subtitle": "Onboarding, separations, settlements and workplace registers.",
        "checklist": {"title": "Get to know Tenure", "steps": [
            {"label": "Start an onboarding case", "endpoint": "hrms.onboarding",
             "done": c["open_onboarding"] > 0},
            {"label": "Record a separation", "endpoint": "retail.separations",
             "done": c["open_separations"] > 0},
            {"label": "Publish a notice", "endpoint": "engagement.notices",
             "done": c["notices"] > 0},
            {"label": "Run a training course", "endpoint": "engagement.hr_training",
             "done": c["active_courses"] > 0},
        ]},
        "numbers": [
            {"label": "Onboarding cases", "value": c["open_onboarding"],
             "endpoint": "hrms.onboarding", "icon": "usercheck"},
            {"label": "Unsettled separations", "value": c["open_separations"],
             "endpoint": "retail.separations", "icon": "logout"},
            {"label": "Open settlements", "value": c["open_settlements"],
             "endpoint": "settlement.settlements", "icon": "scales"},
            {"label": "Open grievances", "value": c["open_grievances"],
             "endpoint": "views.grievance_inbox", "icon": "chat"},
            {"label": "Active courses", "value": c["active_courses"],
             "endpoint": "engagement.hr_training", "icon": "graduation"},
        ],
        "chart": {"title": "Separations by type",
                  "bars": _bars([
                      ("Resignation", SeparationRecord.query.filter_by(
                          separation_type="Resignation").count()),
                      ("Termination", SeparationRecord.query.filter_by(
                          separation_type="Termination").count()),
                      ("Other", SeparationRecord.query.filter(
                          ~SeparationRecord.separation_type.in_(
                              ["Resignation", "Termination"])).count()),
                  ])},
        "link_counts": {"hrms.onboarding": c["open_onboarding"],
                        "retail.separations": c["open_separations"],
                        "settlement.settlements": c["open_settlements"],
                        "views.grievance_inbox": c["open_grievances"]},
    }


def _home_recruitment(u, c):
    return {
        "subtitle": "Track candidates through the hiring pipeline.",
        "checklist": {"title": "Get to know Recruitment", "steps": [
            {"label": "Add your first candidate", "endpoint": "hrms.recruitment",
             "done": c["candidates_active"] > 0},
            {"label": "Create letter templates",
             "endpoint": "payroll.letter_templates",
             "done": c["letter_templates"] > 0},
            {"label": "Issue an offer letter", "endpoint": "payroll.letter_new",
             "done": None},
        ]},
        "numbers": [
            {"label": "Active candidates", "value": c["candidates_active"],
             "endpoint": "hrms.recruitment", "icon": "usercheck"},
            {"label": "In interview", "value": c["candidates_interview"],
             "endpoint": "hrms.recruitment", "icon": "chat"},
            {"label": "Offers out", "value": c["candidates_offer"],
             "endpoint": "hrms.recruitment", "icon": "mail"},
            {"label": "Letters this year", "value": c["letters_year"],
             "endpoint": "payroll.letters", "icon": "file"},
        ],
        "chart": {"title": "Candidates by stage",
                  "bars": _bars([(s, Candidate.query.filter_by(stage=s).count())
                                 for s in Candidate.STAGES])},
        "link_counts": {"hrms.recruitment": c["candidates_active"],
                        "payroll.letters": c["letters_year"]},
    }


def _home_eos(u, c):
    return {
        "subtitle": "Gratuity, end-of-service compliance and benefit letters.",
        "checklist": {"title": "Get to know EOS & Benefits", "steps": [
            {"label": "Review gratuity & leave rules",
             "endpoint": "hrms.compliance", "done": None},
            {"label": "Try the gratuity calculator",
             "endpoint": "hrms.gratuity_calc", "done": None},
            {"label": "Check the EOSB liability report",
             "endpoint": "reports.liability", "done": None},
        ]},
        "numbers": [
            {"label": "Open settlements", "value": c["open_settlements"],
             "endpoint": "settlement.settlements", "icon": "scales"},
            {"label": "Unsettled separations", "value": c["open_separations"],
             "endpoint": "retail.separations", "icon": "logout"},
            {"label": "Letters this year", "value": c["letters_year"],
             "endpoint": "payroll.letters", "icon": "mail"},
        ],
        "chart": {"title": "Settlements by status",
                  "bars": _bars([
                      ("Draft", FinalSettlement.query.filter_by(
                          status="draft").count()),
                      ("Calculated", FinalSettlement.query.filter_by(
                          status="calculated").count()),
                      ("Approved", FinalSettlement.query.filter_by(
                          status="approved").count()),
                      ("Disbursed", FinalSettlement.query.filter_by(
                          status="disbursed").count()),
                      ("Paid", FinalSettlement.query.filter_by(
                          status="paid").count()),
                  ])},
        "link_counts": {"settlement.settlements": c["open_settlements"],
                        "payroll.letters": c["letters_year"]},
    }


def _home_me(u, c):
    return {
        "subtitle": "Everything about you at work — one tap away.",
        "checklist": {"title": "Complete your profile", "steps": [
            {"label": "Add your photo", "endpoint": "views.profile",
             "done": bool(u.photo_path)},
            {"label": "Add your mobile number", "endpoint": "views.profile",
             "done": bool(u.mobile)},
            {"label": "Upload a document", "endpoint": "docs.my_documents",
             "done": c["my_documents"] > 0},
            {"label": "Check in today", "endpoint": "engagement.punch_page",
             "done": c["i_punched_today"]},
        ]},
        "numbers": [
            {"label": "Unread notifications", "value": c["unread_notifications"],
             "endpoint": "engagement.notifications", "icon": "bell"},
            {"label": "My pending leave", "value": c["my_pending_leave"],
             "endpoint": "views.leave", "icon": "calendar"},
            {"label": "My open tickets", "value": c["my_open_tickets"],
             "endpoint": "helpdesk.my_tickets", "icon": "chat"},
            {"label": "My shifts this week", "value": c["my_shifts_week"],
             "endpoint": "views.my_roster", "icon": "clock"},
            {"label": "My documents", "value": c["my_documents"],
             "endpoint": "docs.my_documents", "icon": "file"},
            {"label": "Present this month", "value": c["my_present_month"],
             "endpoint": "views.attendance", "icon": "check"},
        ],
        "chart": {"title": "My attendance this month",
                  "bars": _bars([
                      ("Present", c["my_present_month"]),
                      ("Absent", AttendanceRecord.query.filter_by(
                          user_id=u.id).filter(
                              AttendanceRecord.date >= date(
                                  date.today().year, date.today().month, 1),
                              AttendanceRecord.status == "absent").count()),
                      ("Leave", AttendanceRecord.query.filter_by(
                          user_id=u.id).filter(
                              AttendanceRecord.date >= date(
                                  date.today().year, date.today().month, 1),
                              AttendanceRecord.status.in_(
                                  ["leave", "holiday"])).count()),
                  ])},
        "link_counts": {"engagement.notifications": c["unread_notifications"],
                        "views.leave": c["my_pending_leave"],
                        "helpdesk.my_tickets": c["my_open_tickets"],
                        "extras.inbox": c["unread_notifications"],
                        "views.my_roster": c["my_shifts_week"]},
    }


def _home_my_team(u, c):
    return {
        "subtitle": "Your team's day at a glance.",
        "checklist": {"title": "Get to know My Team", "steps": [
            {"label": "Clear your approvals inbox", "endpoint": "views.approvals",
             "done": c["pending_approvals"] == 0},
            {"label": "Check today's team attendance",
             "endpoint": "extras.team_overview", "done": None},
            {"label": "Open the team calendar",
             "endpoint": "extras.team_calendar", "done": None},
        ]},
        "numbers": [
            {"label": "Team size", "value": c["team_size"],
             "endpoint": "extras.team_overview", "icon": "users"},
            {"label": "Pending approvals", "value": c["pending_approvals"],
             "endpoint": "views.approvals", "icon": "check"},
            {"label": "Absent today (team)", "value": c["team_absent_today"],
             "endpoint": "extras.team_overview_absent", "icon": "absent"},
            {"label": "Open tickets", "value": c["open_tickets"],
             "endpoint": "helpdesk.hr_tickets", "icon": "chat"},
        ],
        "chart": {"title": "Team attendance today",
                  "bars": _bars([
                      ("Present", AttendanceRecord.query.filter(
                          AttendanceRecord.user_id.in_(c["team_ids"]),
                          AttendanceRecord.date == date.today(),
                          AttendanceRecord.status == "present").count()
                          if c["team_ids"] else 0),
                      ("Absent", c["team_absent_today"]),
                  ])},
        "link_counts": {"views.approvals": c["pending_approvals"],
                        "extras.team_overview": c["team_size"],
                        "extras.team_overview_absent": c["team_absent_today"],
                        "helpdesk.hr_tickets": c["open_tickets"]},
    }


_HOME_BUILDERS = {
    "hr-setup": _home_hr_setup,
    "shift-attendance": _home_shift_attendance,
    "leaves": _home_leaves,
    "expenses": _home_expenses,
    "payroll": _home_payroll,
    "performance": _home_performance,
    "tenure": _home_tenure,
    "recruitment": _home_recruitment,
    "eos-benefits": _home_eos,
    "me": _home_me,
    "my-team": _home_my_team,
}


def build_home_context(user, ws):
    """Context dict for templates/workspaces/home.html."""
    c = _shared_counts(user)
    builder = _HOME_BUILDERS.get(ws["key"])
    parts = builder(user, c) if builder else {}
    checklist = parts.get("checklist") or {"title": "", "steps": []}
    steps = checklist.get("steps", [])
    done = sum(1 for s in steps if s.get("done") is True)
    checklist["done_count"] = done
    checklist["total"] = len(steps)
    # resolve checklist hrefs
    for s in steps:
        s["href"] = _href(s.get("endpoint") or "#")
    # resolve number-card hrefs
    for n in parts.get("numbers", []):
        n["href"] = _href(n.get("endpoint") or "#")
    return {
        "greeting": _greeting(user),
        "subtitle": parts.get("subtitle", ""),
        "checklist": checklist,
        "numbers": parts.get("numbers", []),
        "chart": parts.get("chart"),
        "link_counts": parts.get("link_counts", {}),
    }


# ---------------------------------------------------------------------------
# Interface for workstream A
# ---------------------------------------------------------------------------

def visible_workspaces(user):
    """[{key, title, icon, route}] — role-filtered, in registry order."""
    out = []
    for w in WORKSPACES:
        if ws_has(user, w["roles"]):
            out.append({"key": w["key"], "title": w["title"],
                        "icon": w["icon"], "route": w["route"]})
    return out


def workspace_nav(user, key):
    """Groups with links role-filtered for ``user``; hrefs resolved via url_for.

    Returns [] when the key is unknown or the user may not see the workspace.
    Each link: {"label", "icon", "endpoint", "href"}.
    """
    ws = _WS_BY_KEY.get(key)
    if ws is None or not ws_has(user, ws["roles"]):
        return []
    groups = []
    for grp in ws["groups"]:
        links = []
        for link in grp["links"]:
            if not ws_has(user, link["roles"]):
                continue
            links.append({
                "label": link["label"],
                "icon": link["icon"],
                "endpoint": link["endpoint"],
                "href": _href(link["endpoint"], link.get("endpoint_kwargs")),
            })
        if links:
            groups.append({"name": grp["name"], "links": links})
    return groups


# Explicit endpoint -> workspace key, built from the registry (first listing
# wins when an endpoint appears in several workspaces).
_ENDPOINT_WS = {}
for _w in WORKSPACES:
    for _grp in _w["groups"]:
        for _link in _grp["links"]:
            _ENDPOINT_WS.setdefault(_link["endpoint"], _w["key"])

# Heuristic fallback for endpoints with no nav link (detail pages, actions).
_WS_PREFIX_FALLBACK = [
    ("payroll.", "payroll"),
    ("roster.", "shift-attendance"),
    ("reports.", "hr-setup"),
    ("hrms.", "hr-setup"),
    ("retail.", "tenure"),
    ("settlement.", "tenure"),
    ("admin.", "hr-setup"),
    ("approvals.", "hr-setup"),
    ("engagement.", "shift-attendance"),
    ("docs.", "me"),
    ("extras.", "me"),
    ("helpdesk.", "my-team"),
    ("views.", "me"),
]


def ws_key_for_endpoint(endpoint):
    """Workspace key for a Flask endpoint name.

    Used by workstream A's context processor to highlight the current
    workspace. Returns the registry key, or None when unknown. The special
    endpoint "workspaces.home" maps to many keys — callers should prefer
    ``g.ws_key`` (set by the /ws/<key> route) for home pages.
    """
    if not endpoint:
        return None
    if endpoint == "workspaces.home":
        return None
    if endpoint in _ENDPOINT_WS:
        return _ENDPOINT_WS[endpoint]
    for prefix, key in _WS_PREFIX_FALLBACK:
        if endpoint.startswith(prefix):
            return key
    return None


# ---------------------------------------------------------------------------
# Blueprint: GET /ws/<key>
# ---------------------------------------------------------------------------

workspaces_bp = Blueprint("workspaces", __name__, url_prefix="/ws")


@workspaces_bp.route("/<key>")
@login_required
def home(key):
    ws = _WS_BY_KEY.get(key)
    if ws is None:
        abort(404)
    if not ws_has(current_user, ws["roles"]):
        abort(403)
    g.ws_key = key  # consumed by workstream A's context processor
    ctx = build_home_context(current_user, ws)
    ctx["nav"] = workspace_nav(current_user, key)
    return render_template("workspaces/home.html", ws=ws, ws_key=key, **ctx)
