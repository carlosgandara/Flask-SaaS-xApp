"""Public membership decorator; always checks server-side state."""

from functools import wraps

from flask import current_app, g, jsonify, render_template, request

from auth.security import authenticated_user, is_authenticated, token_required
from .service import has_membership


def recover_page_session(function):
    """Serve a data-free page so an expired cookie can be refreshed via POST.

    Apply outside verified_user_required on HTML views only. APIs still return
    401; no cookies or membership state are changed by the initial GET.
    """
    @wraps(function)
    def decorated(*args, **kwargs):
        if not is_authenticated():
            return render_template("billing/session_recovery.html", csrf="")
        return function(*args, **kwargs)
    return decorated


def verified_user_required(function):
    @wraps(function)
    @token_required
    def decorated(*args, **kwargs):
        user = authenticated_user()
        if user is None or not user.verified:
            return jsonify(error="A verified account is required"), 403
        g.billing_user = user
        return function(*args, **kwargs)
    return decorated


def membership_required(plan_key=None):
    def decorate(function):
        @wraps(function)
        @verified_user_required
        def decorated(*args, **kwargs):
            if not current_app.config.get("BILLING_ENABLED"):
                return jsonify(error="Membership is unavailable"), 503
            if not has_membership(g.billing_user.id, plan_key):
                return jsonify(error="An active membership is required"), 403
            return function(*args, **kwargs)
        return decorated
    return decorate
