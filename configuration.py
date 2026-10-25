"""Validate deployment settings without connecting to providers or changing data."""

import os
from urllib.parse import urlsplit


def configure_app(app):
    app.config.setdefault("APP_ENV", os.getenv("APP_ENV", "development"))
    app.config.setdefault("APP_BASE_URL", app.config.get("BILLING_BASE_URL") or os.getenv("BASE_URL", "http://localhost:5000"))
    origin = app.config["APP_BASE_URL"].rstrip("/")
    url = urlsplit(origin)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.path or url.query
            or url.fragment or url.username or url.password):
        raise ValueError("BASE_URL must be an HTTP(S) origin without a path or credentials")
    app.config["APP_BASE_URL"] = origin
    app.config.setdefault("SCHEMA_CHECK", not app.testing)
    app.config["MAX_CONTENT_LENGTH"] = 262144
    app.config.setdefault("BILLING_POLL_SECONDS", int(os.getenv("BILLING_POLL_SECONDS", "2")))
    app.config.setdefault("BILLING_RECONCILE_SECONDS", int(os.getenv("BILLING_RECONCILE_SECONDS", "900")))
    app.config.setdefault("BILLING_EVENT_RETENTION_DAYS", int(os.getenv("BILLING_EVENT_RETENTION_DAYS", "30")))
    if app.config["APP_ENV"] not in {"development", "production"}:
        raise ValueError("APP_ENV must be development or production")
    if not 1 <= app.config["BILLING_POLL_SECONDS"] <= 60:
        raise ValueError("BILLING_POLL_SECONDS must be between 1 and 60")
    if app.config["BILLING_RECONCILE_SECONDS"] < 60:
        raise ValueError("BILLING_RECONCILE_SECONDS must be at least 60")
    if not 1 <= app.config["BILLING_EVENT_RETENTION_DAYS"] <= 365:
        raise ValueError("BILLING_EVENT_RETENTION_DAYS must be between 1 and 365")
    if app.config["APP_ENV"] == "production" and not app.testing:
        if url.scheme != "https" or not app.config["SESSION_COOKIE_SECURE"] or app.debug:
            raise ValueError("Production requires HTTPS BASE_URL, COOKIE_SECURE=True and debug disabled")
        if not os.getenv("DATABASE_URL", "").startswith(("postgresql://", "postgresql+psycopg2://")):
            raise ValueError("Production requires an explicit PostgreSQL DATABASE_URL")
        if not app.config["RATELIMIT_STORAGE_URI"].startswith(("redis://", "rediss://")):
            raise ValueError("Production requires shared Redis rate-limit storage")
        app.config["TRUSTED_HOSTS"] = [url.hostname]
    hops = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
    if not 0 <= hops <= 3:
        raise ValueError("TRUSTED_PROXY_HOPS must be between 0 and 3")
    if hops:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=0, x_port=0, x_prefix=0)
