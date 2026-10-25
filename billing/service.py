"""Application rules. Provider adapters never write to the database."""

from datetime import datetime, timedelta
import secrets

from flask import current_app
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from auth.db import SessionLocal, User
from .errors import BillingConflict, BillingError, CheckoutNotCreated, InvalidWebhook, UnmatchedCheckout
from .models import CheckoutIntent, Subscription, WebhookReceipt

TERMINAL_STATUSES = {"canceled", "expired", "incomplete_expired"}


def settings():
    return current_app.config


def provider_for(name):
    provider = current_app.extensions["billing_providers"].get(name)
    if provider is None:
        raise BillingError("Payment provider is not enabled")
    return provider


def lock_user(db, user_id):
    # PostgreSQL serializes checkout and subscription updates across workers for this user.
    # SQLite is for development/tests only; it does not implement SELECT FOR UPDATE.
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if user is None or not user.verified:
        raise BillingError("A verified account is required")
    return user


def grants_access(subscription, now=None):
    now = now or datetime.utcnow()
    return bool(not subscription.blocked and subscription.plan_key
                and subscription.status in {"active", "trialing", "on_trial", "cancelled"}
                and subscription.access_until and subscription.access_until > now)


def plan_is_configured(subscription):
    plan = settings()["BILLING_PLANS"].get(subscription.plan_key)
    return bool(plan and plan["provider"] == subscription.provider
                and plan["product_id"] == subscription.product_id)


def has_membership(user_id, plan_key=None):
    with SessionLocal() as db:
        records = db.execute(select(Subscription).where(
            Subscription.user_id == user_id, Subscription.mode == settings()["BILLING_MODE"])).scalars()
        return any(grants_access(row) and plan_is_configured(row)
                   and (plan_key is None or row.plan_key == plan_key) for row in records)


def membership_rows(user_id):
    with SessionLocal() as db:
        rows = db.execute(select(Subscription).where(
            Subscription.user_id == user_id, Subscription.mode == settings()["BILLING_MODE"])
            .order_by(Subscription.id.desc())).scalars().all()
        return [{"id": row.id, "provider": row.provider, "plan": row.plan_key or "Unrecognized plan",
                 "plan_name": settings()["BILLING_PLANS"].get(row.plan_key, {}).get("name", "Unrecognized plan"),
                 "status": row.status, "access_until": row.access_until,
                 "has_access": grants_access(row) and plan_is_configured(row),
                 "blocked": row.blocked, "cancel_at_period_end": row.cancel_at_period_end} for row in rows]


def create_checkout(user_id, plan_key):
    plan = settings()["BILLING_PLANS"].get(plan_key)
    if plan is None:
        raise BillingError("Unknown plan")
    now = datetime.utcnow()
    with SessionLocal.begin() as db:
        user = lock_user(db, user_id)
        existing = db.execute(select(Subscription).where(
            Subscription.user_id == user_id, Subscription.mode == settings()["BILLING_MODE"])).scalars()
        if any(row.status not in TERMINAL_STATUSES for row in existing):
            raise BillingConflict("Manage your existing subscription before starting another")
        pending = db.execute(select(CheckoutIntent).where(
            CheckoutIntent.user_id == user_id, CheckoutIntent.mode == settings()["BILLING_MODE"],
            CheckoutIntent.expires_at > now, CheckoutIntent.state.in_(["pending", "ready"]))).scalars().first()
        if pending:
            if pending.plan_key == plan_key and pending.checkout_url:
                return pending.checkout_url
            raise BillingConflict("A checkout is already pending. Return to that checkout or wait for it to expire")
        intent = CheckoutIntent(id=secrets.token_urlsafe(32), user_id=user_id, plan_key=plan_key,
                                provider=plan["provider"], product_id=plan["product_id"],
                                mode=settings()["BILLING_MODE"], expires_at=now + timedelta(hours=1))
        db.add(intent)
        db.flush()
        reference = intent.id
        email = user.email
    # Commit the reference before calling the provider: a fast webhook can now resolve it.
    # If the network times out, keep it pending; an ambiguous POST must not be auto-retried.
    with SessionLocal() as db:
        intent = db.get(CheckoutIntent, reference)
        try:
            url = provider_for(plan["provider"]).checkout(intent, email)
        except CheckoutNotCreated:
            with SessionLocal.begin() as failed_db:
                lock_user(failed_db, user_id)
                failed = failed_db.get(CheckoutIntent, reference)
                if failed.state == "pending" and not failed.checkout_url:
                    failed.state = "failed"
            raise
    with SessionLocal.begin() as db:
        lock_user(db, user_id)
        intent = db.get(CheckoutIntent, reference)
        intent.checkout_url = url
        if intent.state == "pending":
            intent.state = "ready"
    return url


def portal_url(user_id, subscription_id):
    with SessionLocal() as db:
        row = db.execute(select(Subscription).where(
            Subscription.id == subscription_id, Subscription.user_id == user_id,
            Subscription.mode == settings()["BILLING_MODE"])).scalar_one_or_none()
        if row is None:
            raise BillingError("Subscription not found")
        return provider_for(row.provider).portal(row)


def record_event(db, provider, event):
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    result = db.execute(insert(WebhookReceipt).values(
        provider=provider, mode=settings()["BILLING_MODE"], event_id=event.id,
        event_type=event.kind, processed_at=datetime.utcnow()).on_conflict_do_nothing(
            index_elements=["provider", "mode", "event_id"]))
    return result.rowcount == 1


def process_event(provider_name, event):
    """Fetch outside transactions; a fenced lease orders subscription snapshots."""
    from .leases import acquire, fence, release
    provider = provider_for(provider_name)
    mode = settings()["BILLING_MODE"]
    with SessionLocal() as db:
        if db.execute(select(WebhookReceipt.id).where(WebhookReceipt.provider == provider_name,
                WebhookReceipt.mode == mode, WebhookReceipt.event_id == event.id)).first():
            return "duplicate"
    lease_key = f"{provider_name}:{mode}:{event.subscription_id}"
    lease_token = acquire(lease_key)
    try:
        snapshot = provider.subscription(event.subscription_id)
        if snapshot.id != event.subscription_id:
            raise InvalidWebhook("Subscription identifier mismatch")
        with SessionLocal.begin() as db:
            fence(db, lease_key, lease_token)
            query = select(Subscription).where(Subscription.provider == provider_name,
                Subscription.mode == mode, Subscription.external_id == event.subscription_id)
            row = db.execute(query).scalar_one_or_none()
            reference = event.checkout_ref or snapshot.checkout_ref
            intent = db.get(CheckoutIntent, reference) if isinstance(reference, str) else None
            if row is None:
                if intent is None or intent.provider != provider_name or intent.mode != mode:
                    raise UnmatchedCheckout("Subscription needs checkout-link review")
                user_id = intent.user_id
            else:
                user_id = row.user_id
            lock_user(db, user_id)
            if intent is not None:
                db.refresh(intent)
            row = db.execute(query.execution_options(populate_existing=True)).scalar_one_or_none()
            if row is not None and row.user_id != user_id:
                raise InvalidWebhook("Subscription owner mismatch")
            if not record_event(db, provider_name, event):
                return "duplicate"
            if row is None:
                if intent.subscription_id and intent.subscription_id != snapshot.id:
                    raise InvalidWebhook("Checkout reference was already used")
                if snapshot.product_id != intent.product_id:
                    raise InvalidWebhook("Checkout product mismatch")
                if snapshot.checkout_ref and snapshot.checkout_ref != intent.id:
                    raise InvalidWebhook("Checkout reference mismatch")
                row = Subscription(user_id=user_id, provider=provider_name, mode=mode,
                    external_id=snapshot.id, customer_id=snapshot.customer_id,
                    product_id=snapshot.product_id, status=snapshot.status)
                db.add(row)
                intent.subscription_id = snapshot.id
                intent.state = "completed"
            elif row.customer_id != snapshot.customer_id:
                raise InvalidWebhook("Subscription customer mismatch")
            row.plan_key = next((key for key, plan in settings()["BILLING_PLANS"].items()
                if plan["provider"] == provider_name and plan["product_id"] == snapshot.product_id), None)
            row.product_id, row.status = snapshot.product_id, snapshot.status
            row.access_until = snapshot.access_until
            row.cancel_at_period_end = snapshot.cancel_at_period_end
            row.blocked = bool(row.blocked or event.block_access)
            row.updated_at = datetime.utcnow()
        current_app.logger.info("billing.event_processed provider=%s event=%s subscription=%s",
                                provider_name, event.id, event.subscription_id)
        return "processed"
    finally:
        release(lease_key, lease_token)
