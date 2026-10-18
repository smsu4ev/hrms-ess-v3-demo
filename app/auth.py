"""Authentication: login, logout, forced first-login password change."""
import re
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from app import db, limiter
from app.models import User, AuditLog
import config as app_config

auth_bp = Blueprint("auth", __name__)


def log_action(actor_id, action, detail=""):
    db.session.add(AuditLog(
        actor_id=actor_id, action=action, detail=detail,
        ip=request.remote_addr,
    ))
    # committed by caller


def password_policy_errors(password, current_password=None):
    errors = []
    if len(password) < app_config.CONFIG["PASSWORD_MIN_LENGTH"]:
        errors.append(f"Password must be at least {app_config.CONFIG['PASSWORD_MIN_LENGTH']} characters.")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain an uppercase letter.")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain a lowercase letter.")
    if not re.search(r"[0-9]", password):
        errors.append("Password must contain a digit.")
    if current_password and password == current_password:
        errors.append("New password must be different from the current password.")
    return errors


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("views.dashboard"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.get(username) if username else None
        now = datetime.utcnow()

        if user and user.locked_until and user.locked_until > now:
            error = "Account temporarily locked due to failed attempts. Please try again later."
        elif user and user.is_active and user.check_password(password):
            user.failed_attempts = 0
            user.locked_until = None
            log_action(user.id, "login_success")
            db.session.commit()
            login_user(user)
            if user.must_change_password:
                flash("This is your first login. Please set a new password.", "info")
                return redirect(url_for("auth.change_password"))
            return redirect(url_for("views.dashboard"))
        else:
            # Generic message: do not reveal whether the username exists.
            error = "Invalid employee ID or password."
            if user and user.is_active:
                user.failed_attempts += 1
                if user.failed_attempts >= app_config.CONFIG["MAX_FAILED_ATTEMPTS"]:
                    user.locked_until = now + timedelta(minutes=app_config.CONFIG["LOCKOUT_MINUTES"])
                    log_action(user.id, "account_locked",
                               f"{user.failed_attempts} failed attempts")
                    error = "Account temporarily locked due to too many failed attempts."
                db.session.commit()
            elif user and not user.is_active:
                log_action(username, "login_blocked_inactive")
                db.session.commit()
    return render_template("login.html", error=error)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_action(current_user.id, "logout")
    db.session.commit()
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per minute")
def change_password():
    error = None
    if request.method == "POST":
        current_pw = request.form.get("current_password") or ""
        new_pw = request.form.get("new_password") or ""
        confirm_pw = request.form.get("confirm_password") or ""

        if not current_user.check_password(current_pw):
            error = "Current password is incorrect."
        elif new_pw != confirm_pw:
            error = "New passwords do not match."
        else:
            policy_errors = password_policy_errors(new_pw, current_pw)
            if policy_errors:
                error = " ".join(policy_errors)
            else:
                current_user.set_password(new_pw)
                current_user.must_change_password = False
                current_user.failed_attempts = 0
                current_user.locked_until = None
                log_action(current_user.id, "password_changed")
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("views.dashboard"))
    return render_template("change_password.html", error=error,
                           first_login=current_user.must_change_password)
