"""Durable, minimized verified deliveries. Never acknowledge before database commit."""

from datetime import datetime, timedelta
import secrets

from flask import current_app
from sqlalchemy import and_, or_, select, update

from auth.db import SessionLocal
from .leases import insert_for
from .models import WebhookInbox, WebhookReceipt
from .providers.base import identifier

EVENTS = {
    "stripe": {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
               "invoice.paid", "invoice.payment_failed", "invoice.payment_action_required", "charge.refunded", "charge.dispute.created"},
    "lemonsqueezy": {"subscription_created", "subscription_updated", "subscription_cancelled", "subscription_resumed",
                     "subscription_expired", "subscription_paused", "subscription_unpaused", "subscription_payment_success",
                     "subscription_payment_failed", "subscription_payment_recovered", "subscription_payment_refunded"},
}


def optional_id(value):
    if value is None:
        return None
    # Expanded Stripe resources may contain personal information. Persist only IDs.
    return identifier(value.get('id') if isinstance(value, dict) else value)


def minimize(provider, payload):
    """Keep linkage fields only, not buyer addresses, names, card data or invoice contents."""
    if provider == "stripe":
        kind = payload["type"]
        obj = payload["data"]["object"]
        clean = {key: optional_id(obj[key]) for key in ("id", "invoice", "payment_intent", "charge", "subscription") if key in obj}
        clean["metadata"] = {"checkout_ref": optional_id((obj.get("metadata") or {}).get("checkout_ref"))}
        details = (obj.get("parent") or {}).get("subscription_details") or {}
        clean["parent"] = {"subscription_details": {"subscription": optional_id(details.get("subscription"))}}
        return identifier(payload["id"]), kind, {"id": payload["id"], "type": kind, "data": {"object": clean}}
    meta = payload["meta"]
    data = payload["data"]
    attributes = data.get("attributes") or {}
    clean = {"_delivery_id": payload["_delivery_id"],
             "meta": {"event_name": meta["event_name"], "custom_data": {
                 "checkout_ref": optional_id((meta.get("custom_data") or {}).get("checkout_ref"))}},
             "data": {"type": data["type"], "id": identifier(data["id"]), "attributes": {
                 "subscription_id": optional_id(attributes.get("subscription_id"))}}}
    return identifier(payload["_delivery_id"]), meta["event_name"], clean


def enqueue(provider, payload, request_id):
    kind = payload.get("type") if provider == "stripe" else (payload.get("meta") or {}).get("event_name")
    if kind not in EVENTS[provider]:
        return "ignored"
    event_id, kind, clean = minimize(provider, payload)
    mode = current_app.config["BILLING_MODE"]
    with SessionLocal.begin() as db:
        if db.execute(select(WebhookReceipt.id).where(WebhookReceipt.provider == provider,
                WebhookReceipt.mode == mode, WebhookReceipt.event_id == event_id)).first():
            return "duplicate"
        result = db.execute(insert_for(db, WebhookInbox).values(
            provider=provider, mode=mode, event_id=event_id, event_type=kind, payload=clean,
            status="pending", attempts=0, available_at=datetime.utcnow(), request_id=request_id)
            .on_conflict_do_nothing(index_elements=["provider", "mode", "event_id"]))
        return "queued" if result.rowcount else "duplicate"


def claim():
    now = datetime.utcnow()
    eligible = or_(and_(WebhookInbox.status.in_(["pending", "retry"]), WebhookInbox.available_at <= now),
                   and_(WebhookInbox.status == "processing", WebhookInbox.lease_until < now))
    with SessionLocal.begin() as db:
        row = db.execute(select(WebhookInbox).where(WebhookInbox.mode == current_app.config["BILLING_MODE"], eligible)
                         .order_by(WebhookInbox.available_at, WebhookInbox.id).limit(1)
                         .with_for_update(skip_locked=True)).scalar_one_or_none()
        if row is None:
            return None
        token = secrets.token_urlsafe(24)
        updated = db.execute(update(WebhookInbox).where(WebhookInbox.id == row.id, eligible).values(
            status="processing", attempts=WebhookInbox.attempts + 1, lease_token=token,
            lease_until=now + timedelta(seconds=120)))
        if updated.rowcount != 1:
            return None
        return {"id": row.id, "token": token, "provider": row.provider, "mode": row.mode,
                "payload": row.payload, "attempts": row.attempts, "event_id": row.event_id}


def finish(job, status, error=None, delay=0):
    now = datetime.utcnow()
    with SessionLocal.begin() as db:
        result = db.execute(update(WebhookInbox).where(WebhookInbox.id == job["id"],
            WebhookInbox.status == "processing", WebhookInbox.lease_token == job["token"],
            WebhookInbox.lease_until > now).values(status=status, last_error=error,
                completed_at=now if status == "done" else None, available_at=now + timedelta(seconds=delay),
                lease_token=None, lease_until=None))
        return result.rowcount == 1
