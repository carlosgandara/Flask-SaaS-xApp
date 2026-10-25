"""Billing tables share the app database, but do not modify auth tables."""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, Index

from auth.db import Base


class CheckoutIntent(Base):
    __tablename__ = "billing_checkout_intents"

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan_key = Column(String(64), nullable=False)
    provider = Column(String(24), nullable=False)
    product_id = Column(String(128), nullable=False)
    mode = Column(String(8), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    checkout_url = Column(String(2048), nullable=True)
    subscription_id = Column(String(128), nullable=True)
    # An ambiguous API failure stays pending until expiry: never blindly retry a payment POST.
    state = Column(String(16), nullable=False, default="pending")


class Subscription(Base):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        UniqueConstraint("provider", "mode", "external_id", name="uq_billing_subscription"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(24), nullable=False)
    mode = Column(String(8), nullable=False)
    external_id = Column(String(128), nullable=False)
    customer_id = Column(String(128), nullable=False)
    plan_key = Column(String(64), nullable=True)
    product_id = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    access_until = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, nullable=False, default=False)
    blocked = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WebhookReceipt(Base):
    __tablename__ = "billing_webhook_receipts"
    __table_args__ = (
        UniqueConstraint("provider", "mode", "event_id", name="uq_billing_webhook"),
    )

    id = Column(Integer, primary_key=True)
    provider = Column(String(24), nullable=False)
    mode = Column(String(8), nullable=False)
    event_id = Column(String(128), nullable=False)
    event_type = Column(String(96), nullable=False)
    processed_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WebhookInbox(Base):
    __tablename__ = "billing_webhook_inbox"
    __table_args__ = (
        UniqueConstraint("provider", "mode", "event_id", name="uq_billing_inbox_event"),
        Index("ix_billing_inbox_due", "mode", "status", "available_at"),
    )

    id = Column(Integer, primary_key=True)
    provider = Column(String(24), nullable=False)
    mode = Column(String(8), nullable=False)
    event_id = Column(String(128), nullable=False)
    event_type = Column(String(96), nullable=False)
    payload = Column(JSON, nullable=True)
    status = Column(String(24), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    last_error = Column(String(80), nullable=True)
    request_id = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class SyncLease(Base):
    __tablename__ = "billing_sync_leases"

    key = Column(String(200), primary_key=True)
    token = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)


class WorkerHeartbeat(Base):
    __tablename__ = "billing_worker_heartbeats"

    id = Column(String(64), primary_key=True)
    mode = Column(String(8), nullable=False)
    last_seen = Column(DateTime, nullable=False)


class OperatorAction(Base):
    __tablename__ = "billing_operator_actions"

    id = Column(Integer, primary_key=True)
    actor = Column(String(128), nullable=False)
    action = Column(String(32), nullable=False)
    target_id = Column(Integer, nullable=False)
    reason = Column(String(256), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


BILLING_TABLES = [table for table in Base.metadata.sorted_tables if table.name.startswith("billing_")]
