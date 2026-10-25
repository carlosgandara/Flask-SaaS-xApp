"""Shared cookie authentication, request protection and safe error references."""

from datetime import datetime, timezone
from functools import wraps
import secrets

import jwt
from flask import current_app, g, jsonify, render_template, request, session
from sqlalchemy import select

from .config import JWT_EXPIRATION, JWT_SECRET
from .db import AuthSession, SessionLocal, User
from .errors import AuthError


def generate_jwt(email, session_id):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": email, "sid": session_id, "iat": now, "exp": now + JWT_EXPIRATION,
                       "iss": "flask-saas-starter", "aud": "app"}, JWT_SECRET, algorithm="HS256")


def authenticated_user():
    if hasattr(g, "auth_user"):
        return g.auth_user
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience="app",
                             issuer="flask-saas-starter", options={"require": ["sub", "sid", "iat", "exp"]})
        with SessionLocal() as db:
            row = db.execute(select(User).join(AuthSession, AuthSession.user_id == User.id).where(
                AuthSession.id == payload["sid"], AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > datetime.utcnow(), User.email == payload["sub"])).scalar_one_or_none()
            g.auth_user = row
            g.auth_session_id = payload["sid"]
        return row
    except jwt.InvalidTokenError:
        return None


def is_authenticated():
    return authenticated_user() is not None


def token_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        user = authenticated_user()
        if user is None:
            return jsonify(error="Login required or session expired"), 401
        request.user_email = user.email
        return function(*args, **kwargs)
    return decorated


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def check_browser_request():
    """All cookie-authenticated mutations require a session token and exact Origin."""
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token", "")
    if (not expected or not secrets.compare_digest(expected.encode(), supplied.encode())
            or request.headers.get("Origin") != current_app.config["APP_BASE_URL"]
            or request.headers.get("Sec-Fetch-Site") == "cross-site"):
        raise AuthError("Invalid request. Reload the page and try again.", 403)


def page_session_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        if not is_authenticated():
            return render_template("auth/session_recovery.html")
        return function(*args, **kwargs)
    return decorated


def set_auth_cookies(response, access_token, refresh_token):
    secure = current_app.config["SESSION_COOKIE_SECURE"]
    response.set_cookie("access_token", access_token, httponly=True, secure=secure, samesite="Lax",
                        max_age=int(JWT_EXPIRATION.total_seconds()), path="/")
    response.set_cookie("refresh_token", refresh_token, httponly=True, secure=secure, samesite="Lax",
                        max_age=2592000, path="/")
    return response


def clear_auth_cookies(response):
    for name in ("access_token", "refresh_token"):
        response.delete_cookie(name, httponly=True, secure=current_app.config["SESSION_COOKIE_SECURE"],
                               samesite="Lax", path="/")
    return response
