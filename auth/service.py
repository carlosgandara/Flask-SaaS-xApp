"""Authentication transactions. HTTP routes do not coordinate database writes."""

from datetime import datetime, timedelta
import secrets

import bcrypt
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .config import RESET_TOKEN_EXPIRATION, VERIFICATION_EXPIRATION
from .db import ActionToken, AuthSession, RefreshToken, SessionLocal, User
from .errors import AuthError
from .security import generate_jwt
from .validation import password_error, token_hash, valid_token

_DUMMY_HASH = bcrypt.hashpw(b"unused-password-for-timing", bcrypt.gensalt())


def _lock_user(db, user_id):
    return db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one()


def _action(db, user_id, purpose):
    now = datetime.utcnow()
    db.execute(update(ActionToken).where(ActionToken.user_id == user_id, ActionToken.purpose == purpose,
                                        ActionToken.consumed_at.is_(None)).values(consumed_at=now))
    raw = secrets.token_urlsafe(32)
    lifetime = VERIFICATION_EXPIRATION if purpose == "verify" else RESET_TOKEN_EXPIRATION
    db.add(ActionToken(user_id=user_id, purpose=purpose, token_hash=token_hash(raw), expires_at=now + lifetime))
    return raw


def register_account(email, password):
    error = password_error(password)
    if error:
        raise AuthError(error)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        with SessionLocal.begin() as db:
            if db.execute(select(User.id).where(User.email == email)).first():
                return None
            user = User(email=email, password=hashed, verified=False)
            db.add(user)
            db.flush()
            return _action(db, user.id, "verify")
    except IntegrityError:
        # The database's case-insensitive unique index arbitrates concurrent signup.
        with SessionLocal() as db:
            if db.execute(select(User.id).where(User.email == email)).first():
                return None
        raise


def request_action(email, purpose):
    with SessionLocal.begin() as db:
        user = db.execute(select(User).where(User.email == email).with_for_update()).scalar_one_or_none()
        if user is None or (purpose == "verify" and user.verified):
            return None
        return _action(db, user.id, purpose)


def _consume_action(db, raw, purpose):
    if not valid_token(raw):
        raise AuthError("Invalid or expired link")
    now = datetime.utcnow()
    action = db.execute(select(ActionToken).where(
        ActionToken.token_hash == token_hash(raw), ActionToken.purpose == purpose,
        ActionToken.consumed_at.is_(None), ActionToken.expires_at > now)).scalar_one_or_none()
    if action is None:
        raise AuthError("Invalid or expired link")
    user = _lock_user(db, action.user_id)
    consumed = db.execute(update(ActionToken).where(ActionToken.id == action.id,
        ActionToken.consumed_at.is_(None), ActionToken.expires_at > datetime.utcnow()).values(consumed_at=now))
    if consumed.rowcount != 1:
        raise AuthError("Invalid or expired link")
    return user


def verify_account(raw):
    with SessionLocal.begin() as db:
        user = _consume_action(db, raw, "verify")
        user.verified = True


def _revoke_all(db, user_id):
    now = datetime.utcnow()
    db.execute(update(AuthSession).where(AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None)).values(revoked_at=now))
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id,
        RefreshToken.revoked_at.is_(None)).values(revoked_at=now))


def reset_account_password(raw, password):
    error = password_error(password)
    if error:
        raise AuthError(error)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with SessionLocal.begin() as db:
        user = _consume_action(db, raw, "reset")
        user.password = hashed
        user.failed_login_attempts = 0
        user.locked_until = None
        _revoke_all(db, user.id)


def _new_refresh(db, user_id, session, raw):
    db.add(RefreshToken(user_id=user_id, session_id=session.id,
        token_hash_sha256=token_hash(raw), token_hash_bcrypt=bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode(),
        expires_at=session.expires_at))


def login_account(email, password):
    if not isinstance(password, str) or not password or len(password.encode()) > 72:
        raise AuthError("Invalid credentials", 401)
    now, error, result = datetime.utcnow(), None, None
    with SessionLocal.begin() as db:
        user = db.execute(select(User).where(User.email == email).with_for_update()).scalar_one_or_none()
        if user and user.locked_until and user.locked_until <= now:
            user.failed_login_attempts, user.locked_until = 0, None
        valid = bcrypt.checkpw(password.encode(), user.password.encode() if user else _DUMMY_HASH)
        if user is None or not valid or (user.locked_until and user.locked_until > now):
            if user is not None and not (user.locked_until and user.locked_until > now):
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                if user.failed_login_attempts >= 5:
                    user.locked_until = now + timedelta(minutes=15)
            error = AuthError("Invalid credentials or account temporarily unavailable", 401)
        elif not user.verified:
            error = AuthError("Please verify your email first.", 403)
        else:
            user.failed_login_attempts, user.locked_until = 0, None
            session = AuthSession(id=secrets.token_urlsafe(32), user_id=user.id, expires_at=now + timedelta(days=30))
            db.add(session)
            db.flush()
            raw = secrets.token_urlsafe(32)
            _new_refresh(db, user.id, session, raw)
            result = generate_jwt(user.email, session.id), raw
    if error:
        raise error
    return result


def rotate_session(raw):
    if not valid_token(raw):
        raise AuthError("Invalid or expired refresh token", 401)
    result = None
    with SessionLocal.begin() as db:
        record = db.execute(select(RefreshToken).where(RefreshToken.token_hash_sha256 == token_hash(raw))).scalar_one_or_none()
        if record is None or not bcrypt.checkpw(raw.encode(), record.token_hash_bcrypt.encode()):
            raise AuthError("Invalid or expired refresh token", 401)
        user = _lock_user(db, record.user_id)
        db.refresh(record)
        now = datetime.utcnow()
        if record.revoked_at is not None:
            # A previously consumed token was replayed. Revoke that session's
            # descendants, but preserve other devices' independent sessions.
            if record.session_id:
                db.execute(update(AuthSession).where(AuthSession.id == record.session_id).values(revoked_at=now))
                db.execute(update(RefreshToken).where(RefreshToken.session_id == record.session_id,
                    RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
        elif record.expires_at > now and user.verified:
            family = db.get(AuthSession, record.session_id) if record.session_id else None
            if family is None and record.session_id is None:
                # Adopt an existing pre-migration refresh cookie once. Legacy JWTs
                # themselves are rejected because they lack a revocable session ID.
                family = AuthSession(id=secrets.token_urlsafe(32), user_id=user.id, expires_at=record.expires_at)
                db.add(family)
                db.flush()
                record.session_id = family.id
            if family and family.revoked_at is None and family.expires_at > now:
                changed = db.execute(update(RefreshToken).where(RefreshToken.id == record.id,
                    RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
                if changed.rowcount == 1:
                    new_raw = secrets.token_urlsafe(32)
                    _new_refresh(db, user.id, family, new_raw)
                    result = generate_jwt(user.email, family.id), new_raw
    if result is None:
        raise AuthError("Invalid or expired refresh token", 401)
    return result


def logout_session(session_id=None, raw_refresh=None, all_user_id=None):
    with SessionLocal.begin() as db:
        if all_user_id is not None:
            _lock_user(db, all_user_id)
            _revoke_all(db, all_user_id)
            return
        record = None
        if valid_token(raw_refresh):
            record = db.execute(select(RefreshToken).where(
                RefreshToken.token_hash_sha256 == token_hash(raw_refresh))).scalar_one_or_none()
            if record:
                session_id = record.session_id
        family = db.get(AuthSession, session_id) if session_id else None
        if family:
            _lock_user(db, family.user_id)
            now = datetime.utcnow()
            family.revoked_at = now
            db.execute(update(RefreshToken).where(RefreshToken.session_id == family.id,
                RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
        elif record:
            record.revoked_at = datetime.utcnow()
