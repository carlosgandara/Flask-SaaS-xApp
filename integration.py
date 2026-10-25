"""Compose learning with the host's accounts, billing, and shared database."""
from flask import current_app, jsonify, redirect, render_template, request, url_for

from auth.db import Base, engine
from auth.security import authenticated_user, check_browser_request
from billing.service import has_membership
from learning import init_learning
from learning.storage.contracts import StorageAccessError
from learning.storage.json_progress import ProgressStorageError
from learning.storage.sqlalchemy_store import SqlAlchemyProgressStore, define_tables

content_table, progress_table = define_tables(Base.metadata)


def learning_access():
    user = authenticated_user()
    html = request.method == "GET" and request.accept_mimetypes.best == "text/html"
    if user is None:
        if html:
            if not request.cookies.get("refresh_token"):
                return redirect(url_for("auth.login_page"))
            return render_template("auth/session_recovery.html")
        return jsonify(error="Please sign in to continue learning"), 401
    if not user.verified:
        return jsonify(error="Verify your email before learning"), 403
    if current_app.config["BILLING_ENABLED"] and not has_membership(user.id):
        if html:
            return redirect(url_for("billing.pricing"))
        return jsonify(error="An active membership is required", pricing_url=url_for("billing.pricing")), 403


def learner_identity():
    user = authenticated_user()
    return user.id if user and user.verified else None


def init_language_app(app):
    app.config.update(
        LEARNING_LEARNER_ID_RESOLVER=learner_identity,
        LEARNING_ACCESS_CHECK=learning_access,
        LEARNING_MUTATION_CHECK=check_browser_request,
        LEARNING_AUTH_SCRIPT_URL="/auth/static/js/app.js?v=session-recovery-2",
    )
    app.config.setdefault("LEARNING_PROGRESS_STORE", SqlAlchemyProgressStore(engine, progress_table))
    init_learning(app)

    # The existing learning pages use inline styles and one inline menu script.
    # Nonces authorize the script without permitting arbitrary inline JavaScript.
    security = app.extensions["security_headers"]
    for endpoint, view in list(app.view_functions.items()):
        if endpoint.startswith("learning.") and endpoint != "learning.static":
            policy = dict(security.content_security_policy)
            policy["style-src"] = ["'self'", "'unsafe-inline'"]
            app.view_functions[endpoint] = security(
                content_security_policy=policy,
                content_security_policy_nonce_in=["script-src"],
            )(view)

    def storage_unavailable(error):
        app.logger.error("learning.storage_unavailable type=%s", type(error).__name__)
        return jsonify(error="Learning progress is temporarily unavailable. Please try again."), 503

    app.register_error_handler(StorageAccessError, storage_unavailable)
    app.register_error_handler(ProgressStorageError, storage_unavailable)
