"""Flask application factory with security hardening."""
import os
from flask import Flask, request, redirect, url_for, g
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import config as app_config

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)


@login_manager.user_loader
def load_user(user_id):
    from app.models import User
    return User.query.get(user_id)


login_manager.login_view = "auth.login"
login_manager.session_protection = "strong"


def create_app():
    app = Flask(__name__)
    app.config.update(app_config.CONFIG)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Behind a reverse proxy (e.g. Render): trust X-Forwarded-For/Proto for
    # correct client IPs in the audit trail and HTTPS detection. Opt-in only.
    if os.environ.get("ESS_BEHIND_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    from app.auth import auth_bp
    from app.views import views_bp
    from app.admin import admin_bp
    from app.hrms import hrms_bp
    from app.documents import docs_bp
    from app.payroll_views import payroll_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(hrms_bp)
    app.register_blueprint(docs_bp)
    app.register_blueprint(payroll_bp)
    from app import reports  # noqa: E402  (v3.1 reports & export hub)
    app.register_blueprint(reports.reports_bp)
    from app import retail  # noqa: E402  (v3.1 retail-fixture registers)
    app.register_blueprint(retail.retail_bp)
    from app import roster  # noqa: E402  (v3.2 shift roster)
    app.register_blueprint(roster.roster_bp)
    from app import settlement  # noqa: E402  (v3.2 full & final settlement)
    app.register_blueprint(settlement.settlement_bp)
    from app import engagement  # noqa: E402  (v3.3 employee experience)
    app.register_blueprint(engagement.engagement_bp)
    from app import approvals  # noqa: E402  (v3.4 approval matrix)
    app.register_blueprint(approvals.approvals_bp)
    from app import ess_extras  # noqa: E402  (v3.3.1 ESS extras)
    app.register_blueprint(ess_extras.extras_bp)
    from app import helpdesk  # noqa: E402  (v3.5 helpdesk + shift swaps)
    app.register_blueprint(helpdesk.helpdesk_bp)
    from app import desk  # noqa: E402  (v3.6 Frappe shell: /apps + /api/awesome)
    app.register_blueprint(desk.desk_bp)
    from app import workspaces  # noqa: E402  (v3.6 workspace homes)
    app.register_blueprint(workspaces.workspaces_bp)
    from app import settings  # noqa: E402  (v3.6 HR Settings)
    app.register_blueprint(settings.settings_bp)

    @app.before_request
    def enforce_password_change():
        # Force first-login password change for every authenticated user.
        if current_user.is_authenticated:
            allowed = {"auth.change_password", "auth.logout", "static"}
            if current_user.must_change_password and request.endpoint not in allowed:
                return redirect(url_for("auth.change_password"))
            g.user = current_user

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # v3.3: the selfie check-in page needs camera + geolocation; everything
        # else keeps them locked down. (Browsers also require HTTPS or
        # localhost for these APIs — see README-V3.3.)
        if request.path == "/attendance/punch":
            response.headers["Permissions-Policy"] = \
                "camera=(self), geolocation=(self), microphone=()"
        else:
            response.headers["Permissions-Policy"] = \
                "camera=(), microphone=(), geolocation=()"
        # Conservative CSP: same-origin only, no inline scripts needed by templates.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; frame-ancestors 'none'"
        )
        # v3.1: long-lived caching for static assets (fingerprinted app.js /
        # style.css change with each deploy; repeat navigations stay fast).
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.context_processor
    def inject_globals():
        can_view = False
        unread = 0
        try:
            if current_user.is_authenticated:
                from app.files import salary_visible
                can_view = salary_visible(current_user)
                # v3.3: notification-centre bell badge.
                from app.models import Notification
                unread = Notification.query.filter_by(
                    user_id=current_user.id, is_read=False).count()
        except Exception:
            can_view = False
        return {
            "company_name": app_config.COMPANY_NAME,
            "trade_name": app_config.TRADE_NAME,
            "can_view_salary": can_view,
            "unread_count": unread,
            # v3.6 Frappe shell: workspace rail/sidebar data.
            "workspaces": _ws_list(),
            "ws_groups": _ws_groups(),
            "ws_current": _ws_current(),
        }

    # --- v3.6 workspace globals (imported lazily; degrade gracefully) ---
    def _ws_list():
        try:
            if not current_user.is_authenticated:
                return []
            from app.workspaces import visible_workspaces
            return visible_workspaces(current_user)
        except Exception:
            return []

    def _ws_groups():
        try:
            if not current_user.is_authenticated:
                return []
            from app.workspaces import workspace_nav, ws_key_for_endpoint
            from flask import g
            key = getattr(g, "ws_key", None) or ws_key_for_endpoint(request.endpoint or "")
            if not key:
                return []
            return workspace_nav(current_user, key)
        except Exception:
            return []

    def _ws_current():
        try:
            if not current_user.is_authenticated:
                return None
            from app.workspaces import visible_workspaces, ws_key_for_endpoint
            from flask import g
            key = getattr(g, "ws_key", None) or ws_key_for_endpoint(request.endpoint or "")
            for ws in visible_workspaces(current_user):
                if ws["key"] == key:
                    return ws
            return None
        except Exception:
            return None

    with app.app_context():
        db.create_all()
        # Lightweight idempotent migration for columns added after the
        # original schema was created (v2.1 UAE fields, v3 payroll/photo).
        # create_all() does not add columns to existing tables.
        try:
            from sqlalchemy import inspect, text

            def add_column(table, col, ddl):
                cols = {c["name"]
                        for c in inspect(db.engine).get_columns(table)}
                if col not in cols:
                    db.session.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

            for col, ddl in [
                ("emirates_id_expiry_date", "DATE"),
                ("iban", "VARCHAR(60)"),
                ("bank_name", "VARCHAR(80)"),
                ("visa_company_id", "INTEGER"),
                ("photo_path", "VARCHAR(120)"),
                ("can_view_salary", "BOOLEAN"),
                ("phone", "VARCHAR(30)"),
                ("passport_no", "VARCHAR(40)"),
                ("exit_date", "DATE"),
                ("emirates_id_no", "VARCHAR(40)"),
                ("separation_date", "DATE"),      # v3.1 separation register
                ("separation_type", "VARCHAR(30)"),
                ("date_of_birth", "DATE"),       # v3.3 birthday celebrations
                ("is_hr_staff", "BOOLEAN"),      # v3.4 approval matrix
                ("is_hr_manager", "BOOLEAN"),
                ("pending_status", "VARCHAR(20)"),
                ("share_celebrations", "BOOLEAN"),  # v3.4.1 celebration opt-out
                ("ui_theme", "VARCHAR(16)"),        # v3.9 theme preference key
            ]:
                add_column("users", col, ddl)
            add_column("punch_records", "low_accuracy", "BOOLEAN")  # v3.4.1
            add_column("punch_records", "legal_hold", "BOOLEAN")    # v3.4.1
            add_column("jd_acknowledgements", "jd_text_hash",
                       "VARCHAR(64)")                             # v3.4.1
            add_column("jd_acknowledgements", "user_agent",
                       "VARCHAR(255)")                            # v3.4.1
            add_column("approval_instances", "halted", "BOOLEAN")  # v3.4.1
            add_column("final_settlements", "payment_doc_path",
                       "VARCHAR(120)")                            # v3.4.1
            add_column("final_settlements", "payment_ref", "VARCHAR(80)")
            add_column("final_settlements", "disbursed_at", "DATETIME")
            add_column("final_settlements", "disbursed_by_id", "VARCHAR(20)")
            add_column("generated_letters", "has_salary_data", "BOOLEAN")
            # v3.2.1: settlement due-date/receipt/ticket fields + outdoor flag
            add_column("final_settlements", "unpaid_days_used", "FLOAT")
            add_column("final_settlements", "cancellation_doc_path",
                       "VARCHAR(120)")
            add_column("final_settlements", "ticket_option", "VARCHAR(20)")
            add_column("final_settlements", "ticket_amount", "FLOAT")
            add_column("shift_patterns", "is_outdoor", "BOOLEAN")
            # v3.3.1: notification centre tabs (Important vs Events).
            add_column("notifications", "category", "VARCHAR(20)")
            # New table for idempotent loan recovery (checkfirst = no-op if present).
            from app.models import PayrollLoanRecovery
            PayrollLoanRecovery.__table__.create(db.engine, checkfirst=True)
            # v3.4.1: approval delegations (checkfirst = no-op if present).
            from app.models import ApprovalDelegation
            ApprovalDelegation.__table__.create(db.engine, checkfirst=True)
            db.session.commit()
        except Exception:
            app.logger.exception("Column migration failed")
            db.session.rollback()
        # v3.4: default approval matrices (idempotent; existing chains are
        # never overwritten, so HR Admin customisations survive upgrades).
        try:
            from app.approvals import ensure_default_matrices
            ensure_default_matrices()
        except Exception:
            app.logger.exception("Approval matrix defaults failed")
        # v3.3.1: backfill notification categories (idempotent).
        try:
            from app.engagement import backfill_notification_categories
            backfill_notification_categories()
        except Exception:
            app.logger.exception("Notification category backfill failed")
        # v3.3.1: letter templates added after the initial seed (idempotent).
        try:
            from app.ess_extras import ensure_new_letter_templates
            ensure_new_letter_templates()
        except Exception:
            app.logger.exception("Letter template ensure failed")
        # Private upload folders (never served directly; files go through
        # authenticated routes only). v3.3 adds punches / training / signatures.
        try:
            from app.files import upload_root
            import os as _os
            root = upload_root()
            for sub in ("punches", "training", "signatures",
                        "settlement_receipts", "settlement_payments",
                        "expenses", "letter_uploads"):
                _os.makedirs(_os.path.join(root, sub), exist_ok=True)
        except Exception:
            app.logger.exception("Upload folder setup failed")
        # v3.3: run the 45-day punch-photo retention once at startup
        # (also runs lazily on the punch page at most once a day).
        try:
            from app.engagement import purge_punch_photos, PUNCH_PHOTO_DAYS
            purge_punch_photos(PUNCH_PHOTO_DAYS)
        except Exception:
            app.logger.exception("Punch photo purge failed")
        # Free-sample mode only: auto-create fake demo users on startup
        # (hosts like Render free have no shell to run seed_demo.py).
        if os.environ.get("ESS_SEED_DEMO") == "1":
            try:
                from app.demo_seed import ensure_demo_users
                ensure_demo_users()
            except Exception:
                app.logger.exception("Demo seed failed")

    return app
