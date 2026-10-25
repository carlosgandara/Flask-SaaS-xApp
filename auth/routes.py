"""HTTP boundary for authentication. Transactions and validation live in services."""

import hashlib
from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, make_response, redirect, render_template, request
from sqlalchemy import text
from werkzeug.exceptions import RequestEntityTooLarge

from .db import SessionLocal
from .errors import AuthError
from .extensions import limiter
from .mail_service import send_email
from .security import (authenticated_user, check_browser_request, clear_auth_cookies, csrf_token,
                       generate_jwt, is_authenticated, page_session_required, set_auth_cookies, token_required)
from .service import (login_account, logout_session, register_account, request_action,
                      reset_account_password, rotate_session, verify_account)
from .validation import normalize_email, password_error

auth_bp = Blueprint("auth", __name__, template_folder="templates", static_folder="static",
                    static_url_path="/auth/static")


def login_key():
    data = request.get_json(silent=True)
    email = data.get("email", "") if isinstance(data, dict) else ""
    value = str(email).strip().lower()[:254]
    return hashlib.sha256(((request.remote_addr or "unknown") + ":" + value).encode()).hexdigest()


@auth_bp.before_request
def protect_auth():
    if request.method == "POST":
        if request.content_length is not None and request.content_length > 4096:
            raise RequestEntityTooLarge()
        if len(request.get_data(cache=True)) > 4096:
            raise RequestEntityTooLarge()
        check_browser_request()


@auth_bp.app_errorhandler(AuthError)
def auth_error(error):
    current_app.logger.info("auth.rejected endpoint=%s status=%s request_id=%s",
                            request.endpoint, error.status_code, getattr(g, "request_id", ""))
    return jsonify(error=str(error), reference=getattr(g, "request_id", "")), error.status_code


@auth_bp.app_errorhandler(429)
def ratelimit_handler(error):
    return jsonify(error="Too many requests. Please try again later.", reference=getattr(g, "request_id", "")), 429


def json_data():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise AuthError("Send a JSON object")
    return data


def email_from(data):
    try:
        return normalize_email(data.get("email"))
    except ValueError as exc:
        raise AuthError(str(exc)) from exc


def notify(email, raw, purpose):
    if raw is None:
        return
    endpoint = "/verify-email" if purpose == "verify" else "/reset-password"
    label = "Verify your email" if purpose == "verify" else "Reset your password"
    link = current_app.config["APP_BASE_URL"] + endpoint + "?token=" + raw
    try:
        send_email(email, label, label + ": " + link,
                   html=render_template("auth/emails/action.html", label=label, link=link,
                                        expiry="24 hours" if purpose == "verify" else "15 minutes"))
    except Exception as exc:
        current_app.logger.error("auth.email_failed purpose=%s type=%s request_id=%s",
                                 purpose, type(exc).__name__, getattr(g, "request_id", ""))


@auth_bp.get("/auth/csrf")
def csrf():
    return jsonify(csrf_token=csrf_token())


@auth_bp.get("/auth/session")
@token_required
def session_status():
    return jsonify(authenticated=True)


@auth_bp.get("/health")
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return jsonify(status="healthy", database="connected", timestamp=datetime.utcnow().isoformat())
    except Exception:
        return jsonify(status="unhealthy", database="unavailable"), 503


@auth_bp.get("/")
def home():
    return render_template("site/index.html")


@auth_bp.get("/login")
def login_page():
    return render_template("auth/login.html")


@auth_bp.get("/register")
def register_page():
    return render_template("auth/register.html")


@auth_bp.get("/forgot-password")
def forgot_page():
    return render_template("auth/forgot_password.html")


@auth_bp.get("/resend-verification")
def resend_page():
    return render_template("auth/resend_verification.html")


@auth_bp.get("/reset-password")
def reset_page():
    return render_template("auth/reset_password.html", token=request.args.get("token"))


@auth_bp.get("/dashboard")
@page_session_required
def dashboard_page():
    return render_template("auth/dashboard.html")


@auth_bp.get("/profile")
@page_session_required
def profile_page():
    return render_template("auth/profile.html")


@auth_bp.post("/register")
@limiter.limit("5 per minute")
def register():
    data = json_data()
    email = email_from(data)
    notify(email, register_account(email, data.get("password")), "verify")
    return jsonify(message="If this email is eligible, a verification link was sent.")


@auth_bp.get("/verify-email")
@limiter.limit("10 per minute")
def verify_email():
    try:
        verify_account(request.args.get("token"))
    except AuthError:
        return render_template("auth/verify_error.html", error="Invalid or expired link. Request a new verification email."), 400
    return render_template("auth/verify_success.html")


@auth_bp.post("/resend-verification")
@limiter.limit("3 per minute")
def resend_verification():
    email = email_from(json_data())
    notify(email, request_action(email, "verify"), "verify")
    return jsonify(message="If this account needs verification, a new link was sent.")


@auth_bp.post("/login")
@limiter.limit("5 per minute")
@limiter.limit("5 per 5 minutes", key_func=login_key)
def login():
    data = json_data()
    access, refresh = login_account(email_from(data), data.get("password"))
    current_app.logger.info("auth.login_success request_id=%s", getattr(g, "request_id", ""))
    return set_auth_cookies(make_response(jsonify(message="Login successful")), access, refresh)


@auth_bp.post("/refresh")
@limiter.limit("30 per minute")
def refresh():
    access, raw = rotate_session(request.cookies.get("refresh_token"))
    return set_auth_cookies(make_response(jsonify(message="Session refreshed")), access, raw)


@auth_bp.post("/forgot-password")
@limiter.limit("5 per minute")
def forgot_password():
    email = email_from(json_data())
    notify(email, request_action(email, "reset"), "reset")
    return jsonify(message="If that account exists, a reset link was sent.")


@auth_bp.post("/reset-password")
@limiter.limit("5 per minute")
def reset_password():
    data = json_data()
    reset_account_password(data.get("token"), data.get("new_password"))
    return clear_auth_cookies(make_response(jsonify(message="Password updated. All sessions have been signed out.")))


@auth_bp.get("/protected")
@token_required
def protected():
    return jsonify(message=f"Hello {request.user_email}!")


@auth_bp.post("/logout")
def logout():
    user = authenticated_user()
    logout_session(session_id=getattr(g, "auth_session_id", None) if user else None,
                   raw_refresh=request.cookies.get("refresh_token"))
    return clear_auth_cookies(make_response(jsonify(message="Logged out successfully")))


@auth_bp.post("/logout-all")
@token_required
def logout_all():
    logout_session(all_user_id=g.auth_user.id)
    return clear_auth_cookies(make_response(jsonify(message="All sessions have been signed out.")))
