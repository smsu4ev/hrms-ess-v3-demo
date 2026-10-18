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
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
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
        try:
            if current_user.is_authenticated:
                from app.files import salary_visible
                can_view = salary_visible(current_user)
        except Exception:
            can_view = False
        return {
            "company_name": app_config.COMPANY_NAME,
            "trade_name": app_config.TRADE_NAME,
            "can_view_salary": can_view,
        }

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
            ]:
                add_column("users", col, ddl)
            add_column("generated_letters", "has_salary_data", "BOOLEAN")
            # New table for idempotent loan recovery (checkfirst = no-op if present).
            from app.models import PayrollLoanRecovery
            PayrollLoanRecovery.__table__.create(db.engine, checkfirst=True)
            db.session.commit()
        except Exception:
            app.logger.exception("Column migration failed")
            db.session.rollback()
        # Private upload folders (never served directly; files go through
        # authenticated routes only).
        try:
            from app.files import upload_root
            upload_root()
        except Exception:
            app.logger.exception("Upload folder setup failed")
        # Free-sample mode only: auto-create fake demo users on startup
        # (hosts like Render free have no shell to run seed_demo.py).
        if os.environ.get("ESS_SEED_DEMO") == "1":
            try:
                from app.demo_seed import ensure_demo_users
                ensure_demo_users()
            except Exception:
                app.logger.exception("Demo seed failed")

    return app
