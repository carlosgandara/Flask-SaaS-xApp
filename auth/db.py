# /auth/db.py

import os
import datetime
import hashlib

import bcrypt
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship


load_dotenv()


# ================================================================
# DATABASE CONFIGURATION
# ================================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./test.db"
    print("DATABASE_URL not found. Using SQLite for development.")


# ================================================================
# SQLALCHEMY SETUP
# ================================================================

engine = create_engine(DATABASE_URL, pool_pre_ping=True, hide_parameters=True)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


# ================================================================
# MODELS
# ================================================================

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)

    # Email verification
    verified = Column(Boolean, default=False)
    verification_token = Column(String, nullable=True)
    verification_expiry = Column(DateTime, nullable=True)

    # Password reset
    reset_token = Column(String, nullable=True)
    reset_expiry = Column(DateTime, nullable=True)

    # Security and lockout
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)

    refresh_tokens = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    session_id = Column(String(64), ForeignKey("auth_sessions.id"), nullable=True, index=True)

    token_hash_sha256 = Column(String(64), nullable=False)
    token_hash_bcrypt = Column(String, nullable=False)

    expires_at = Column(DateTime, nullable=False)
    created_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
    )
    revoked_at = Column(DateTime, nullable=True)

    user = relationship(
        "User",
        back_populates="refresh_tokens",
    )

    __table_args__ = (
        Index(
            "ix_refresh_tokens_token_hash_sha256",
            "token_hash_sha256",
        ),
    )

    def __repr__(self):
        return (
            f"<RefreshToken(id={self.id}, "
            f"user_id={self.user_id}, "
            f"revoked={self.revoked_at is not None})>"
        )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)


class ActionToken(Base):
    __tablename__ = "auth_action_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    purpose = Column(String(16), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)


# ================================================================
# USER DATABASE FUNCTIONS
# ================================================================

def find_user_by_email(email):
    db = SessionLocal()

    try:
        return (
            db.query(User)
            .filter(User.email == email)
            .first()
        )
    finally:
        db.close()


def create_user(email, password_hash):
    db = SessionLocal()

    try:
        new_user = User(
            email=email,
            password=password_hash,
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return new_user
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_user(email, updates):
    db = SessionLocal()

    try:
        user = (
            db.query(User)
            .filter(User.email == email)
            .first()
        )

        if not user:
            return None

        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)

        db.commit()
        db.refresh(user)

        return user
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ================================================================
# REFRESH TOKEN DATABASE FUNCTIONS
# ================================================================

def create_refresh_token(user_id, raw_token, expires_at, session_id=None):
    sha256_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    bcrypt_hash = bcrypt.hashpw(
        raw_token.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    db = SessionLocal()

    try:
        if session_id is None:
            import secrets
            session_id = secrets.token_urlsafe(32)
            db.add(AuthSession(id=session_id, user_id=user_id, expires_at=expires_at))
            db.flush()
        token_record = RefreshToken(
            user_id=user_id,
            token_hash_sha256=sha256_hash,
            token_hash_bcrypt=bcrypt_hash,
            expires_at=expires_at,
            session_id=session_id,
        )

        db.add(token_record)
        db.commit()
        db.refresh(token_record)

        return token_record
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
