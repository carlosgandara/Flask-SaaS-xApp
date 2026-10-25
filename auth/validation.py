"""The same input policy is used for registration, login and account recovery."""

import hashlib
from email_validator import EmailNotValidError, validate_email
from flask import current_app


def normalize_email(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 254:
        raise ValueError("Enter a valid email address")
    try:
        email = validate_email(value.strip(), check_deliverability=False,
                               allow_smtputf8=False, test_environment=current_app.testing).ascii_email
    except EmailNotValidError as exc:
        raise ValueError("Enter a valid email address") from exc
    # This app intentionally treats email identities as case-insensitive.
    return email.lower()


def password_error(password):
    if not isinstance(password, str) or not password:
        return "Password required"
    if len(password.encode("utf-8")) > 72:
        return "Password must be at most 72 UTF-8 bytes"
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not any(c.isupper() for c in password):
        return "Password must contain an uppercase letter"
    if not any(c.isdigit() for c in password):
        return "Password must contain a number"
    if not any(c in "!@#$%^&*()_-+=<>?/" for c in password):
        return "Password must contain a special character (!@#$%^&*)"
    return None


def valid_token(value):
    return isinstance(value, str) and 20 <= len(value) <= 128 and value.isascii()


def token_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
