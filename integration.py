"""Compose learning with the host's accounts, billing, and shared database."""
import secrets
from copy import deepcopy
from flask import current_app, jsonify, redirect, render_template, request, session, url_for

from auth.db import Base, engine
from auth.security import authenticated_user, check_browser_request
from billing.service import has_membership
from learning import init_learning
from learning.storage.contracts import StorageAccessError
from learning.storage.json_progress import ProgressStorageError
from learning.storage.sqlalchemy_store import SqlAlchemyProgressStore, define_tables
from learning.storage.guest_progress import GuestProgressStore
from learning.storage.contracts import StorageConflictError
from redis.exceptions import RedisError

content_table, progress_table = define_tables(Base.metadata)


def gate_level(level_id):
    from learning.services.content import get_levels
    index = next((i for i, level in enumerate(get_levels()['levels'], 1) if level['id'] == level_id), None)
    if index is None:
        return None  # The route returns its normal 404.
    user = authenticated_user()
    html = request.method == 'GET' and not request.is_json
    if index >= 4 and (user is None or not user.verified):
        if html:
            if user is None and request.cookies.get('refresh_token'):
                return render_template('auth/session_recovery.html')
            return render_template('site/gate.html', account_gate=True, level_number=index), 403
        return jsonify(error='Create and verify a free account to unlock level 4.', gate='account', url='/register'), 401
    if index >= 7 and (not current_app.config['BILLING_ENABLED'] or not has_membership(user.id)):
        if html:
            return render_template('site/gate.html', account_gate=False, level_number=index), 403
        return jsonify(error='Membership unlocks level 7 and beyond.', gate='membership', url='/pricing'), 403


def learning_access():
    user = authenticated_user()
    # Restore existing accounts before accidentally treating their work as guest play.
    if user is None and request.cookies.get('refresh_token'):
        if request.method == 'GET' and request.accept_mimetypes.best == 'text/html':
            return render_template('auth/session_recovery.html')
        return jsonify(error='Please restore your session'), 401
    level_id = (request.view_args or {}).get('level_id')
    if level_id:
        return gate_level(level_id)
    if request.endpoint == 'learning.answer':
        from learning.services.progress import get_progress
        return gate_level(get_progress()['level_id'])


class AccountAndGuestProgress:
    def __init__(self, accounts, guests):
        self.accounts, self.guests = accounts, guests

    def load(self, learner, language, default):
        if not isinstance(learner, int):
            return self.guests.load(learner, language, default)
        progress = self.accounts.load(learner, language, default)
        guest_id = session.get('learning_guest_id')
        if guest_id and progress.get('_storage_revision') == 0:
            guest = self.guests.load(guest_id, language, default)
            if guest.get('_storage_revision', 0):
                progress = dict(deepcopy(guest), _storage_revision=0)
                try:
                    self.accounts.save(learner, language, progress)
                except StorageConflictError:
                    progress = self.accounts.load(learner, language, default)
        return progress

    def save(self, learner, language, progress):
        store = self.accounts if isinstance(learner, int) else self.guests
        store.save(learner, language, progress)


def learner_identity():
    user = authenticated_user()
    if user and user.verified:
        return user.id
    if 'learning_guest_id' not in session:
        session['learning_guest_id'] = 'guest-' + secrets.token_urlsafe(24)
    return session['learning_guest_id']


def access_summary():
    user = authenticated_user()
    account = bool(user and user.verified)
    return {'account':account, 'member':bool(account and current_app.config['BILLING_ENABLED'] and has_membership(user.id))}


def init_language_app(app):
    app.config.update(
        LEARNING_LEARNER_ID_RESOLVER=learner_identity,
        LEARNING_ACCESS_CHECK=learning_access,
        LEARNING_MUTATION_CHECK=check_browser_request,
        LEARNING_AUTH_SCRIPT_URL="/auth/static/js/app.js?v=session-recovery-2",
        LEARNING_LEVEL_GATE=gate_level,
        LEARNING_ARCADE_OPEN=True,
        LEARNING_PUBLIC_EXPERIENCE=True,
        LEARNING_ACCESS_SUMMARY=access_summary,
    )
    redis_url = app.config['RATELIMIT_STORAGE_URI']
    guests = GuestProgressStore(redis_url if redis_url.startswith(('redis://', 'rediss://')) else None)
    app.config.setdefault("LEARNING_PROGRESS_STORE", AccountAndGuestProgress(SqlAlchemyProgressStore(engine, progress_table), guests))
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
    app.register_error_handler(RedisError, storage_unavailable)
