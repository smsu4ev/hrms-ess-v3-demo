"""Al-Ameen Industries LLC - Employee Self-Service (ESS) Portal."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    # !!! MUST be overridden in production via environment variable !!!
    "SECRET_KEY": os.environ.get("ESS_SECRET_KEY", "dev-only-change-me-immediately"),
    "SQLALCHEMY_DATABASE_URI": os.environ.get(
        "ESS_DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "ess.db")
    ),
    "SQLALCHEMY_TRACK_MODIFICATIONS": False,
    # Secure cookie flags - SESSION_COOKIE_SECURE requires HTTPS in production.
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
    "SESSION_COOKIE_SECURE": os.environ.get("ESS_COOKIE_SECURE", "0") == "1",
    "REMEMBER_COOKIE_HTTPONLY": True,
    "REMEMBER_COOKIE_DURATION": 28800,  # 8 hours
    "PERMANENT_SESSION_LIFETIME": 28800,
    # Password policy
    "PASSWORD_MIN_LENGTH": 8,
    # Account lockout
    "MAX_FAILED_ATTEMPTS": 5,
    "LOCKOUT_MINUTES": 15,
}

COMPANY_NAME = "Al-Ameen Industries LLC"
TRADE_NAME = "800 Retail - Retail Solutions"

# UAE Labour Law (Federal Decree-Law 33/2021) leave entitlements.
# The "note" is shown on the leave page; "entitled" drives the balance checks.
LEAVE_TYPES = {
    "annual": {
        "label": "Annual Leave", "entitled": 30,
        "note": "30 days per year after 1 year of service (Art. 29)."},
    "sick": {
        "label": "Sick Leave", "entitled": 90,
        "note": "90 days per year (once probation passed): first 15 days full pay, "
                "next 30 days half pay, remaining 45 days unpaid (Art. 31)."},
    "maternity": {
        "label": "Maternity Leave", "entitled": 60,
        "note": "60 days: first 45 days full pay, next 15 days half pay (Art. 30)."},
    "parental": {
        "label": "Parental Leave", "entitled": 5,
        "note": "5 working days for either parent, within 6 months of the child's birth (Art. 32)."},
    "bereavement": {
        "label": "Bereavement Leave", "entitled": 5,
        "note": "5 days for death of a spouse; 3 days for death of a parent, child, "
                "sibling, grandparent or grandchild (Cabinet Res. 1/2022, Art. 19)."},
    "study": {
        "label": "Study Leave", "entitled": 10,
        "note": "10 days per year after 2 years' service, for study at a UAE-accredited "
                "institution (Cabinet Res. 1/2022, Art. 18)."},
    "hajj": {
        "label": "Hajj Leave", "entitled": 30,
        "note": "30 days unpaid, granted once during service, with proof of acceptance "
                "from the Hajj authority (Cabinet Res. 1/2022, Art. 17)."},
    "unpaid": {
        "label": "Unpaid Leave", "entitled": 0,
        "note": "Deducted from salary; granted at the company's discretion."},
}

GRIEVANCE_CATEGORIES = [
    "Workplace Conduct",
    "Salary & Benefits",
    "Working Hours & Leave",
    "Health & Safety",
    "Harassment / Discrimination",
    "Facilities & Accommodation",
    "Other",
]

EVAL_CRITERIA = [
    "Job Knowledge & Skills",
    "Quality of Work",
    "Productivity & Efficiency",
    "Teamwork & Collaboration",
    "Attendance & Punctuality",
    "Communication Skills",
    "Initiative & Problem Solving",
    "Safety & Policy Compliance",
]

EVAL_COMMENT_OPTIONS = [
    "Exceeds Expectations",
    "Meets Expectations",
    "Needs Improvement",
    "Unsatisfactory",
    "Not Applicable",
]


# ===================== HRMS+ESS v3 settings =====================

# WhatsApp integration: queued only until the WhatsApp Business connection
# is configured. Live sending activates later; the outbox records every
# message first (integration-ready design).
WHATSAPP_ENABLED = os.environ.get("ESS_WHATSAPP_ENABLED", "0") == "1"

# File upload limits
PHOTO_MAX_MB = 2
DOCUMENT_MAX_MB = 10

# Default salary components seeded by the demo seed (admin can add more).
DEFAULT_SALARY_COMPONENTS = [
    # code, name, kind, is_fixed, prorate, wps_bucket, is_statutory
    ("BASIC", "Basic Salary", "earning", True, True, "basic", False),
    ("HRA", "Housing Allowance", "earning", True, True, "variable", False),
    ("TRANSPORT", "Transport Allowance", "earning", True, True, "variable", False),
    ("OTHER_ALLOW", "Other Allowance", "earning", True, True, "variable", False),
    ("LOAN_RECOVERY", "Loan Recovery", "deduction", True, False, "variable", False),
    ("ADVANCE_RECOVERY", "Advance Recovery", "deduction", True, False, "variable", False),
    ("FINE", "Fine / Penalty", "deduction", True, False, "variable", False),
    ("OTHER_DED", "Other Deduction", "deduction", True, False, "variable", False),
]

# Seed letter templates (mail merge). Placeholders:
# {{employee_name}} {{employee_id}} {{designation}} {{department}} {{company}}
# {{joining_date}} {{basic_salary}} {{monthly_salary}} {{date}}
DEFAULT_LETTER_TEMPLATES = {
    "Offer Letter": (
        "OFFER OF EMPLOYMENT\n\n"
        "Date: {{date}}\n\n"
        "Dear {{employee_name}},\n\n"
        "We are pleased to offer you the position of {{designation}} in the "
        "{{department}} department at {{company}}.\n\n"
        "Your monthly salary will be AED {{monthly_salary}} and your joining "
        "date is {{joining_date}}.\n\n"
        "This offer is subject to successful completion of pre-employment "
        "formalities.\n\n"
        "Yours sincerely,\nHR Department, {{company}}"
    ),
    "Salary Transfer Letter": (
        "SALARY TRANSFER LETTER\n\n"
        "Date: {{date}}\n\n"
        "To Whom It May Concern,\n\n"
        "This is to confirm that {{employee_name}} (Employee ID: "
        "{{employee_id}}) is employed with {{company}} as {{designation}}.\n\n"
        "Their current monthly salary is AED {{monthly_salary}} (Basic: "
        "AED {{basic_salary}}).\n\n"
        "We request you to process salary transfers to their registered bank "
        "account.\n\n"
        "Yours sincerely,\nHR Department, {{company}}"
    ),
    "Salary Certificate": (
        "SALARY CERTIFICATE\n\n"
        "Date: {{date}}\n\n"
        "To Whom It May Concern,\n\n"
        "This is to certify that {{employee_name}} (Employee ID: "
        "{{employee_id}}) has been employed with {{company}} as "
        "{{designation}} since {{joining_date}}.\n\n"
        "Salary details:\n"
        "- Basic Salary: AED {{basic_salary}}\n"
        "- Monthly Salary: AED {{monthly_salary}}\n\n"
        "This certificate is issued upon the employee's request.\n\n"
        "Yours sincerely,\nHR Department, {{company}}"
    ),
}

# Demo visa companies (fake). Production values go into Company records.
DEFAULT_COMPANIES = [
    ("ALA", "Al-Ameen Industries LLC"),
    ("R800", "800 Retail LLC"),
    ("AAS", "Al-Ameen Services LLC"),
]
