"""Explicit, server-owned plan catalog. Billing is disabled by default."""

import json
import os
import re
from urllib.parse import urlsplit


def load_config(app):
    app.config.setdefault("BILLING_ENABLED", os.getenv("BILLING_ENABLED", "false").lower() == "true")
    app.config.setdefault("BILLING_MODE", os.getenv("BILLING_MODE", "test"))
    app.config.setdefault("BILLING_BASE_URL", os.getenv("BASE_URL", "http://localhost:5000").rstrip("/"))
    app.config.setdefault("BILLING_PLANS", json.loads(os.getenv("BILLING_PLANS", "{}")))
    for key in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "LEMONSQUEEZY_API_KEY",
                "LEMONSQUEEZY_WEBHOOK_SECRET", "LEMONSQUEEZY_STORE_ID"):
        app.config.setdefault(key, os.getenv(key, ""))
    if not app.config["BILLING_ENABLED"]:
        return
    if app.config["BILLING_MODE"] not in {"test", "live"}:
        raise ValueError("BILLING_MODE must be test or live")
    url = urlsplit(app.config["BILLING_BASE_URL"])
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or url.path not in {"", "/"}):
        raise ValueError("BASE_URL must be a canonical origin without a path")
    local_test = app.config["BILLING_MODE"] == "test" and url.hostname in {"localhost", "127.0.0.1"}
    if url.scheme != "https" and not (local_test and url.scheme == "http"):
        raise ValueError("Billing requires HTTPS except for localhost test mode")
    if app.config["BILLING_MODE"] == "live" and not app.testing:
        if app.debug:
            raise ValueError("Live billing must not run with Flask debug enabled")
        from auth.config import COOKIE_SECURE
        if not COOKIE_SECURE or not app.config.get("SESSION_COOKIE_SECURE"):
            raise ValueError("Live billing requires COOKIE_SECURE=True and secure session cookies")
        if not app.config.get("RATELIMIT_STORAGE_URI", "").startswith(("redis://", "rediss://")):
            raise ValueError("Live billing requires shared Redis rate-limit storage")
        from auth.db import engine
        if engine.dialect.name != "postgresql":
            raise ValueError("Live billing requires PostgreSQL for transactional row locking")
        if app.config["APP_ENV"] != "production":
            raise ValueError("Live billing requires APP_ENV=production")
    plans = app.config["BILLING_PLANS"]
    if not isinstance(plans, dict) or not plans:
        raise ValueError("BILLING_PLANS must contain at least one plan")
    products = set()
    for key, plan in plans.items():
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", key) or not isinstance(plan, dict):
            raise ValueError("Invalid billing plan")
        if set(plan) != {"name", "description", "price_label", "provider", "product_id"}:
            raise ValueError("Each plan needs name, description, price_label, provider, product_id")
        if not all(isinstance(value, str) and value.strip() for value in plan.values()):
            raise ValueError("Plan fields must be nonempty strings")
        provider, product = plan["provider"], plan["product_id"]
        if provider not in {"stripe", "lemonsqueezy"}:
            raise ValueError("Unknown payment provider")
        if (provider, product) in products:
            raise ValueError("Provider product IDs must map to exactly one local plan")
        products.add((provider, product))
        if provider == "stripe":
            if not re.fullmatch(r"price_[A-Za-z0-9]+", product):
                raise ValueError("Stripe plans require a recurring price ID")
            prefix = "sk_test_" if app.config["BILLING_MODE"] == "test" else "sk_live_"
            if not app.config["STRIPE_SECRET_KEY"].startswith(prefix):
                raise ValueError("Stripe key does not match BILLING_MODE")
            secret = app.config["STRIPE_WEBHOOK_SECRET"]
            if not secret.startswith("whsec_") or len(secret) < 32:
                raise ValueError("Configure a Stripe endpoint signing secret")
        else:
            if not product.isdecimal() or not app.config["LEMONSQUEEZY_STORE_ID"].isdecimal():
                raise ValueError("Lemon Squeezy requires numeric variant and store IDs")
            if not app.config["LEMONSQUEEZY_API_KEY"]:
                raise ValueError("Configure LEMONSQUEEZY_API_KEY")
            if not 32 <= len(app.config["LEMONSQUEEZY_WEBHOOK_SECRET"]) <= 40:
                raise ValueError("Use a random 32–40 character Lemon Squeezy signing secret")
