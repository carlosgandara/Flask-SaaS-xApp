# /auth/config.py

import os
import datetime

from dotenv import load_dotenv


load_dotenv()


# ================================================================
# EMAIL SETTINGS
# ================================================================

EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM")

EMAIL_DISPLAY_NAME = os.getenv(
    "EMAIL_DISPLAY_NAME",
    "wolfchad",
)


# ================================================================
# COOKIE SETTINGS
# ================================================================

COOKIE_SECURE = (
    os.getenv("COOKIE_SECURE", "False").lower() == "true"
)


# ================================================================
# WEBSITE URL
# Used in verification and password reset emails.
# ================================================================

# Public URLs are resolved once by configuration.configure_base_url.


# ================================================================
# JWT SETTINGS
# ================================================================

JWT_SECRET = os.getenv("JWT_SECRET_KEY")

if not JWT_SECRET or len(JWT_SECRET.strip().encode("utf-8")) < 32:
    raise EnvironmentError(
        "JWT_SECRET_KEY must be configured with at least 32 bytes. "
        "Generate a random secret with: "
        "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )

JWT_EXPIRATION = datetime.timedelta(minutes=15)
RESET_TOKEN_EXPIRATION = datetime.timedelta(minutes=15)
VERIFICATION_EXPIRATION = datetime.timedelta(days=1)


# ================================================================
# REQUIRED EMAIL SETTINGS
# ================================================================

REQUIRED_ENV = [
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_USER",
    "EMAIL_PASS",
    "EMAIL_FROM",
]

missing = [
    name
    for name in REQUIRED_ENV
    if not os.getenv(name)
]

if missing:
    raise EnvironmentError(
        f"Missing env: {', '.join(missing)}"
    )
