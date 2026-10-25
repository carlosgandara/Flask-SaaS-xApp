"""Run with python -m unittest discover -s tests -v. Never connects to Neon."""

import os
import secrets

# Override before importing app/database modules. Use a fresh process for this suite.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["BILLING_ENABLED"] = "false"
for name in ("EMAIL_HOST", "EMAIL_USER", "EMAIL_PASS", "EMAIL_FROM"):
    os.environ[name] = "test"
os.environ["EMAIL_PORT"] = "587"

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import Mock, patch

from flask import Flask
from sqlalchemy import select

from app import create_app
from auth.db import Base, SessionLocal, User, engine
if engine.dialect.name != "sqlite" or engine.url.database != ":memory:":
    raise RuntimeError("Billing tests require a fresh process with an in-memory SQLite database")
from auth.routes import generate_jwt as mint_access_token
from auth.db import AuthSession
from billing.access import membership_required
from billing.errors import InvalidWebhook, ProviderUnavailable
from billing.models import CheckoutIntent, Subscription, WebhookInbox, WebhookReceipt
from billing.worker import process_one
from billing.providers.base import Event, Snapshot, safe_url
from billing.providers.lemonsqueezy import LemonSqueezyProvider
from billing.providers.stripe import StripeProvider
from billing.service import has_membership, process_event


def generate_jwt(email):
    with SessionLocal.begin() as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        sid = secrets.token_urlsafe(32)
        db.add(AuthSession(id=sid, user_id=user.id, expires_at=datetime.utcnow() + timedelta(days=30)))
    return mint_access_token(email, sid)


class BillingTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.app = create_app({
            "TESTING": True, "RATELIMIT_ENABLED": False, "BILLING_ENABLED": True,
            "BILLING_MODE": "test", "BILLING_BASE_URL": "http://localhost:5000",
            "STRIPE_SECRET_KEY": "sk_test_" + "s" * 32,
            "STRIPE_WEBHOOK_SECRET": "whsec_" + "s" * 32,
            "LEMONSQUEEZY_API_KEY": "test-key", "LEMONSQUEEZY_WEBHOOK_SECRET": "l" * 32,
            "LEMONSQUEEZY_STORE_ID": "123",
            "BILLING_PLANS": {
                "monthly": {"name": "Monthly", "description": "Monthly access", "price_label": "$10/month",
                            "provider": "stripe", "product_id": "price_monthly"},
                "lemon": {"name": "Monthly", "description": "Monthly access", "price_label": "$10/month",
                          "provider": "lemonsqueezy", "product_id": "456"}},
        })
        with SessionLocal.begin() as db:
            db.add_all([User(id=1, email="one@example.test", password="unused", verified=True),
                        User(id=2, email="two@example.test", password="unused", verified=True),
                        User(id=3, email="unverified@example.test", password="unused", verified=False)])
        self.client = self.app.test_client()
        self.client.set_cookie("access_token", generate_jwt("one@example.test"))
        with self.client.session_transaction() as session:
            session["csrf_token"] = "test-csrf"
        self.headers = {"Origin": "http://localhost:5000", "X-CSRF-Token": "test-csrf"}
        self.stripe = self.app.extensions["billing_providers"]["stripe"]
        self.lemon = self.app.extensions["billing_providers"]["lemonsqueezy"]

    def intent(self, provider="stripe", product="price_monthly", user_id=1):
        with SessionLocal.begin() as db:
            db.add(CheckoutIntent(id="opaque-reference", user_id=user_id, plan_key="monthly" if provider == "stripe" else "lemon",
                                  provider=provider, product_id=product, mode="test",
                                  expires_at=datetime.utcnow() + timedelta(hours=1)))
        return "opaque-reference"

    def snapshot(self, **overrides):
        values = dict(id="sub_1", customer_id="cus_1", product_id="price_monthly", status="active",
                      access_until=datetime.utcnow() + timedelta(days=30), cancel_at_period_end=False,
                      checkout_ref="opaque-reference")
        values.update(overrides)
        return Snapshot(**values)

    def signed_stripe(self, obj=None, kind="customer.subscription.updated", event_id="evt_1", sent=None, live=False):
        raw = json.dumps({"id": event_id, "type": kind, "livemode": live,
                          "data": {"object": obj or {"id": "sub_1", "metadata": {"checkout_ref": "opaque-reference"}}}}).encode()
        sent = sent or int(time.time())
        sig = hmac.new(self.app.config["STRIPE_WEBHOOK_SECRET"].encode(), str(sent).encode() + b"." + raw, hashlib.sha256).hexdigest()
        return raw, {"Stripe-Signature": f"t={sent},v1={sig}", "Content-Type": "application/json"}

    def signed_lemon(self, kind="subscription_created", mode=True):
        raw = json.dumps({"meta": {"event_name": kind, "custom_data": {"checkout_ref": "opaque-reference"}},
                          "data": {"type": "subscriptions", "id": "99", "attributes": {"test_mode": mode}}}).encode()
        sig = hmac.new(self.app.config["LEMONSQUEEZY_WEBHOOK_SECRET"].encode(), raw, hashlib.sha256).hexdigest()
        return raw, {"X-Signature": sig, "Content-Type": "application/json"}

    def test_disabled_billing_does_not_enable_checkout(self):
        app = create_app({"TESTING": True, "BILLING_ENABLED": False})
        self.assertEqual(app.test_client().post("/billing/checkout").status_code, 503)

    def test_pages_assets_and_no_inline_handlers(self):
        for path in ("/billing/pricing", "/billing/membership", "/billing/static/js/billing.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertNotIn(b"onclick=", response.data)
            response.close()

    def test_checkout_requires_login_and_verified_account(self):
        self.client.delete_cookie("access_token")
        self.assertEqual(self.client.post("/billing/checkout", json={"plan": "monthly"}, headers=self.headers).status_code, 401)
        self.client.set_cookie("access_token", generate_jwt("unverified@example.test"))
        self.assertEqual(self.client.post("/billing/checkout", json={"plan": "monthly"}, headers=self.headers).status_code, 403)

    def test_csrf_and_cross_site_requests_rejected(self):
        for headers in ({}, {"Origin": "https://evil.invalid", "X-CSRF-Token": "test-csrf"},
                        {**self.headers, "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.client.post("/billing/checkout", json={"plan": "monthly"}, headers=headers).status_code, 403)

    def test_client_cannot_supply_price_provider_or_user(self):
        for payload in ({"plan": "unknown"}, {"plan": []}, {"plan": "monthly", "user_id": 2},
                        {"plan": "monthly", "price": 1}, ["monthly"]):
            self.assertEqual(self.client.post("/billing/checkout", json=payload, headers=self.headers).status_code, 400)

    def test_checkout_reuses_pending_url_and_does_not_grant_access(self):
        with patch.object(self.stripe, "checkout", return_value="https://checkout.stripe.com/test") as mocked:
            for _ in range(2):
                response = self.client.post("/billing/checkout", json={"plan": "monthly"}, headers=self.headers)
                self.assertEqual(response.status_code, 200)
            self.assertEqual(mocked.call_count, 1)
        with self.app.app_context():
            self.assertFalse(has_membership(1))
        self.assertEqual(self.client.post("/billing/checkout", json={"plan": "lemon"}, headers=self.headers).status_code, 409)

    def test_ambiguous_provider_failure_does_not_retry_post(self):
        with patch.object(self.stripe, "checkout", side_effect=ProviderUnavailable("Provider unavailable")) as mocked:
            self.assertEqual(self.client.post("/billing/checkout", json={"plan": "monthly"}, headers=self.headers).status_code, 503)
            self.assertEqual(self.client.post("/billing/checkout", json={"plan": "monthly"}, headers=self.headers).status_code, 409)
            self.assertEqual(mocked.call_count, 1)

    def test_portal_cannot_access_another_users_subscription(self):
        with SessionLocal.begin() as db:
            db.add(Subscription(id=10, user_id=2, provider="stripe", mode="test", external_id="sub_other",
                                customer_id="cus_other", plan_key="monthly", product_id="price_monthly", status="active"))
        with patch.object(self.stripe, "portal") as mocked:
            self.assertEqual(self.client.post("/billing/portal", json={"subscription_id": 10}, headers=self.headers).status_code, 400)
            mocked.assert_not_called()

    def test_signed_webhook_creates_membership_and_deduplicates(self):
        self.intent()
        raw, headers = self.signed_stripe()
        with patch.object(self.stripe, "subscription", return_value=self.snapshot()) as mocked:
            for _ in range(2):
                self.assertEqual(self.client.post("/billing/webhooks/stripe", data=raw, headers=headers).status_code, 200)
            mocked.assert_not_called()
            with self.app.app_context():
                self.assertTrue(process_one())
                self.assertFalse(process_one())
            self.assertEqual(mocked.call_count, 1)
        with SessionLocal() as db:
            self.assertEqual(db.query(Subscription).count(), 1)
            self.assertEqual(db.query(WebhookReceipt).count(), 1)
            self.assertEqual(db.query(WebhookInbox).count(), 1)
        with self.app.app_context():
            self.assertTrue(has_membership(1, "monthly"))
            self.assertFalse(has_membership(2))

    def test_invalid_tampered_expired_and_wrong_mode_signatures_rejected(self):
        self.intent()
        cases = [self.signed_stripe(sent=int(time.time()) - 600), self.signed_stripe(live=True)]
        raw, headers = self.signed_stripe()
        cases.append((raw + b" ", headers))
        cases.append((raw, {"Stripe-Signature": "invalid"}))
        with patch.object(self.stripe, "subscription") as mocked:
            for raw, headers in cases:
                self.assertEqual(self.client.post("/billing/webhooks/stripe", data=raw, headers=headers).status_code, 400)
            mocked.assert_not_called()

    def test_unknown_checkout_and_wrong_product_do_not_grant_access(self):
        raw, headers = self.signed_stripe()
        self.assertEqual(self.client.post("/billing/webhooks/stripe", data=raw, headers=headers).status_code, 200)
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()):
            process_one()
        with SessionLocal.begin() as db:
            row = db.query(WebhookInbox).one()
            self.assertEqual(row.status, 'retry')
            row.available_at = datetime.utcnow() - timedelta(seconds=1)
        self.intent()
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot(product_id="price_wrong")):
            process_one()
        with SessionLocal() as db:
            self.assertEqual(db.query(Subscription).count(), 0)
            self.assertEqual(db.query(WebhookReceipt).count(), 0)
            self.assertEqual(db.query(WebhookInbox).one().status, 'needs_review')

    def test_provider_failure_rolls_back_receipt_for_retry(self):
        self.intent()
        raw, headers = self.signed_stripe()
        with patch.object(self.stripe, "subscription", side_effect=ProviderUnavailable("Unavailable")) as mocked:
            self.assertEqual(self.client.post("/billing/webhooks/stripe", data=raw, headers=headers).status_code, 200)
            mocked.assert_not_called()
            with self.app.app_context():
                process_one()
        with SessionLocal.begin() as db:
            self.assertEqual(db.query(WebhookReceipt).count(), 0)
            row = db.query(WebhookInbox).one()
            self.assertEqual(row.status, 'retry')
            row.available_at = datetime.utcnow() - timedelta(seconds=1)
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()):
            process_one()
        with SessionLocal() as db:
            self.assertEqual(db.query(WebhookReceipt).count(), 1)
            self.assertEqual(db.query(WebhookInbox).one().status, 'done')

    def test_delayed_events_use_current_state_and_refund_block_is_sticky(self):
        self.intent()
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()) as mocked:
            process_event("stripe", Event("evt_1", "created", "sub_1", "opaque-reference"))
            mocked.return_value = self.snapshot(status="canceled")
            process_event("stripe", Event("evt_old", "created", "sub_1", "opaque-reference"))
            self.assertFalse(has_membership(1))
            mocked.return_value = self.snapshot()
            process_event("stripe", Event("evt_refund", "refund", "sub_1", block_access=True))
            process_event("stripe", Event("evt_new", "updated", "sub_1"))
            self.assertFalse(has_membership(1))

    def test_expired_unpaid_paused_and_removed_plans_fail_closed(self):
        self.intent()
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()) as mocked:
            process_event("stripe", Event("initial", "created", "sub_1", "opaque-reference"))
            for index, status in enumerate(("past_due", "unpaid", "paused", "incomplete", "unknown")):
                mocked.return_value = self.snapshot(status=status)
                process_event("stripe", Event(f"event_{index}", "updated", "sub_1"))
                self.assertFalse(has_membership(1))
            mocked.return_value = self.snapshot(access_until=datetime.utcnow() - timedelta(seconds=1))
            process_event("stripe", Event("expired", "updated", "sub_1"))
            self.assertFalse(has_membership(1))
            mocked.return_value = self.snapshot()
            process_event("stripe", Event("renewed", "updated", "sub_1"))
            self.assertTrue(has_membership(1))
            self.app.config["BILLING_PLANS"].pop("monthly")
            self.assertFalse(has_membership(1))

    def test_cancellation_grace_ends_at_deadline(self):
        self.intent("lemonsqueezy", "456")
        snapshot = self.snapshot(id="99", product_id="456", status="cancelled", cancel_at_period_end=True, checkout_ref=None)
        with self.app.app_context(), patch.object(self.lemon, "subscription", return_value=snapshot):
            process_event("lemonsqueezy", Event("lemon_1", "cancelled", "99", "opaque-reference"))
            self.assertTrue(has_membership(1))
        with SessionLocal.begin() as db:
            db.query(Subscription).update({"access_until": datetime.utcnow() - timedelta(seconds=1)})
        with self.app.app_context():
            self.assertFalse(has_membership(1))

    def test_lemon_signature_mode_and_raw_body(self):
        self.intent("lemonsqueezy", "456")
        snapshot = self.snapshot(id="99", product_id="456", checkout_ref=None)
        raw, headers = self.signed_lemon()
        with patch.object(self.lemon, "subscription", return_value=snapshot):
            self.assertEqual(self.client.post("/billing/webhooks/lemonsqueezy", data=raw, headers=headers).status_code, 200)
            self.assertEqual(self.client.post("/billing/webhooks/lemonsqueezy", data=raw + b" ", headers=headers).status_code, 400)
        raw, headers = self.signed_lemon(mode=False)
        self.assertEqual(self.client.post("/billing/webhooks/lemonsqueezy", data=raw, headers=headers).status_code, 400)

    def test_membership_decorator_checks_database(self):
        @self.app.get("/paid-test")
        @membership_required("monthly")
        def paid():
            return {"paid": True}
        self.assertEqual(self.client.get("/paid-test").status_code, 403)
        self.intent()
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()):
            process_event("stripe", Event("evt_1", "created", "sub_1", "opaque-reference"))
        self.assertEqual(self.client.get("/paid-test").status_code, 200)

    def test_request_body_limits(self):
        self.assertEqual(self.client.post("/billing/checkout", data="x" * 4097).status_code, 413)
        self.assertEqual(self.client.post("/billing/webhooks/stripe", data="x" * 262145).status_code, 413)

    def test_external_redirects_are_allowlisted(self):
        for value in ("https://evil.invalid/", "https://stripe.com.evil.invalid/", "javascript:alert(1)",
                      "https://checkout.stripe.com@evil.invalid/", "http://checkout.stripe.com/",
                      "https://checkout.stripe.com:444/", "https://evil-lemonsqueezy.com/"):
            with self.assertRaises(ProviderUnavailable):
                safe_url(value, "stripe" if "stripe" in value else "lemonsqueezy")

    def test_stripe_api_snapshot_and_portal_contract(self):
        data = {"id": "sub_1", "livemode": False, "customer": "cus_1", "status": "active",
                "metadata": {"checkout_ref": "opaque-reference"}, "cancel_at_period_end": True,
                "items": {"data": [{"price": {"id": "price_monthly"}, "current_period_end": 1900000000}]}}
        with patch.object(self.stripe, "request", return_value=data):
            snapshot = self.stripe.subscription("sub_1")
            self.assertEqual(snapshot.product_id, "price_monthly")
            self.assertEqual(snapshot.access_until, datetime.fromtimestamp(1900000000, timezone.utc).replace(tzinfo=None))
        with patch.object(self.stripe, "request", return_value={"url": "https://billing.stripe.com/session"}) as mocked:
            self.stripe.portal(Mock(customer_id="cus_1"))
            self.assertEqual(mocked.call_args.kwargs["data"]["customer"], "cus_1")

    def test_lemon_api_rejects_wrong_store_and_maps_expiry(self):
        data = {"data": {"id": "99", "attributes": {"store_id": 123, "test_mode": True,
                "customer_id": 1, "variant_id": 456, "status": "cancelled", "cancelled": True,
                "ends_at": "2030-01-01T00:00:00Z"}}}
        with patch.object(self.lemon, "request", return_value=data):
            self.assertEqual(self.lemon.subscription("99").access_until, datetime(2030, 1, 1))
            data["data"]["attributes"]["store_id"] = 999
            with self.assertRaises(InvalidWebhook):
                self.lemon.subscription("99")

    def test_live_configuration_rejects_insecure_origin(self):
        config = dict(self.app.config)
        config.update(BILLING_MODE="live", BILLING_BASE_URL="http://example.com")
        with self.assertRaises(ValueError):
            create_app(config)

    def test_checkout_provider_requests_use_server_owned_references(self):
        self.intent()
        with SessionLocal() as db:
            intent = db.get(CheckoutIntent, "opaque-reference")
            with patch.object(self.stripe, "request", side_effect=[
                    {"livemode": False, "active": True, "type": "recurring", "recurring": {"usage_type": "licensed"}},
                    {"livemode": False, "url": "https://checkout.stripe.com/test"}]) as mocked:
                self.stripe.checkout(intent, "one@example.test")
                data = mocked.call_args.kwargs["data"]
                self.assertEqual(data["subscription_data[metadata][checkout_ref]"], intent.id)
                self.assertEqual(data["line_items[0][price]"], "price_monthly")
                self.assertIn("Idempotency-Key", mocked.call_args.kwargs["headers"])

    def test_one_time_products_rejected_before_payment_checkout(self):
        self.intent()
        with SessionLocal() as db:
            intent = db.get(CheckoutIntent, "opaque-reference")
            with patch.object(self.stripe, "request", return_value={"livemode": False, "active": True, "type": "one_time"}) as mocked:
                with self.assertRaises(ProviderUnavailable):
                    self.stripe.checkout(intent, "one@example.test")
                self.assertEqual(mocked.call_count, 1)
            with patch.object(self.lemon, "request", return_value={"data": [{"attributes": {
                    "variant_id": "price_monthly", "category": "one_time", "scheme": "standard"}}]}) as mocked:
                with self.assertRaises(ProviderUnavailable):
                    self.lemon.checkout(intent, "one@example.test")
                self.assertEqual(mocked.call_count, 1)

    def test_stripe_refunds_and_disputes_resolve_invoice_payments(self):
        for kind, obj in (("charge.refunded", {"payment_intent": "pi_1"}),
                          ("charge.dispute.created", {"charge": "ch_1"})):
            responses = [{"data": [{"invoice": "in_1"}]},
                         {"parent": {"subscription_details": {"subscription": "sub_1"}}}]
            if kind == "charge.dispute.created":
                responses.insert(0, {"payment_intent": "pi_1"})
            raw, headers = self.signed_stripe(obj=obj, kind=kind)
            with patch.object(self.stripe, "request", side_effect=responses):
                event = self.stripe.event(raw, headers)
            self.assertTrue(event.block_access)
            self.assertEqual(event.subscription_id, "sub_1")

    def test_reused_checkout_reference_cannot_bind_a_second_subscription(self):
        self.intent()
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()) as mocked:
            process_event("stripe", Event("evt_1", "created", "sub_1", "opaque-reference"))
            mocked.return_value = self.snapshot(id="sub_2")
            with self.assertRaises(InvalidWebhook):
                process_event("stripe", Event("evt_2", "created", "sub_2", "opaque-reference"))

    def test_subscription_customer_cannot_be_changed(self):
        self.intent()
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()) as mocked:
            process_event("stripe", Event("evt_1", "created", "sub_1", "opaque-reference"))
            mocked.return_value = self.snapshot(customer_id="cus_other")
            with self.assertRaises(InvalidWebhook):
                process_event("stripe", Event("evt_2", "updated", "sub_1"))

    def test_lemon_checkout_payload_and_portal_contract(self):
        self.intent("lemonsqueezy", "456")
        responses = [{"data": [{"attributes": {"variant_id": 456, "category": "subscription",
                                               "scheme": "standard", "usage_aggregation": None}}]},
                     {"data": {"attributes": {"test_mode": True, "url": "https://store.lemonsqueezy.com/checkout"}}}]
        with SessionLocal() as db, patch.object(self.lemon, "request", side_effect=responses) as mocked:
            intent = db.get(CheckoutIntent, "opaque-reference")
            self.lemon.checkout(intent, "one@example.test")
            attrs = mocked.call_args.kwargs["json"]["data"]["attributes"]
            self.assertEqual(attrs["checkout_data"]["custom"]["checkout_ref"], intent.id)
            self.assertEqual(attrs["product_options"]["enabled_variants"], [456])
            self.assertTrue(attrs['checkout_options']['embed'])
        with patch.object(self.lemon, "subscription_data", return_value=({}, {
                "customer_id": 1, "urls": {"customer_portal": "https://store.lemonsqueezy.com/billing"}})):
            self.assertEqual(self.lemon.portal(Mock(external_id="99", customer_id="1")),
                             "https://store.lemonsqueezy.com/billing")

    def test_provider_transport_disables_redirects_and_bounds_timeouts(self):
        with patch("billing.providers.base.requests.Session") as session:
            request = session.return_value.__enter__.return_value.request
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"ok": True}
            self.stripe.request("GET", "/v1/subscriptions/sub_1")
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
            self.assertEqual(request.call_args.kwargs["timeout"], (3.05, 12))
            self.assertEqual(request.call_args.args[1], "https://api.stripe.com/v1/subscriptions/sub_1")

    def test_live_mode_requires_production_guards(self):
        config = dict(self.app.config)
        config.update(TESTING=False, BILLING_MODE="live", BILLING_BASE_URL="https://example.com",
                      STRIPE_SECRET_KEY="sk_live_" + "s" * 32)
        with self.assertRaisesRegex(ValueError, "secure session cookies"):
            create_app(config)

    def test_reconciliation_command_updates_saved_subscription(self):
        self.intent()
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()):
            process_event("stripe", Event("evt_1", "created", "sub_1", "opaque-reference"))
        with patch.object(self.stripe, 'list_subscriptions', return_value=([{'id':'sub_1','product_id':'price_monthly'}],None)), patch.object(self.lemon, 'list_subscriptions', return_value=([],None)):
            result = self.app.test_cli_runner().invoke(args=["billing-reconcile"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('Queued 1',result.output)
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot(status="canceled")):
            process_one()
            self.assertFalse(has_membership(1))

    def test_initial_schema_install_is_idempotent(self):
        for _ in range(2):
            result = self.app.test_cli_runner().invoke(args=["billing-init-db"])
            self.assertEqual(result.exit_code, 0, result.output)
        with SessionLocal() as db:
            self.assertEqual(db.query(User).count(), 3)

    def test_browser_auth_pages_still_render(self):
        for path in ("/login", "/register", "/forgot-password", "/reset-password?token=test", "/dashboard", "/profile"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"onclick=", response.data)

    def test_late_stripe_invoice_can_create_subscription_from_api_reference(self):
        self.intent()
        raw, headers = self.signed_stripe(kind="invoice.paid", obj={
            "parent": {"subscription_details": {"subscription": "sub_1"}}})
        with patch.object(self.stripe, "subscription", return_value=self.snapshot()):
            self.assertEqual(self.client.post("/billing/webhooks/stripe", data=raw, headers=headers).status_code, 200)
            with self.app.app_context():
                process_one()
        with self.app.app_context():
            self.assertTrue(has_membership(1))

    def test_active_subscription_prevents_second_checkout(self):
        self.intent()
        with self.app.app_context(), patch.object(self.stripe, "subscription", return_value=self.snapshot()):
            process_event("stripe", Event("evt_1", "created", "sub_1", "opaque-reference"))
        with patch.object(self.stripe, "checkout") as mocked:
            self.assertEqual(self.client.post("/billing/checkout", json={"plan": "monthly"}, headers=self.headers).status_code, 409)
            mocked.assert_not_called()
        data = self.client.get('/billing/api/membership').get_json()
        self.assertFalse(data['can_subscribe'])
        self.assertEqual(data['current_subscription']['plan_name'], 'Monthly')

    def test_no_membership_allows_subscribing_but_anonymous_api_is_protected(self):
        data = self.client.get('/billing/api/membership').get_json()
        self.assertTrue(data['can_subscribe'])
        self.assertIsNone(data['current_subscription'])
        self.client.delete_cookie('access_token')
        self.assertEqual(self.client.get('/billing/api/membership').status_code, 401)

    def test_overlay_permissions_are_scoped_to_pricing(self):
        pricing = self.client.get('/billing/pricing')
        policy = pricing.headers['Content-Security-Policy']
        self.assertIn('https://app.lemonsqueezy.com/js/lemon.js', policy)
        self.assertIn('https://assets.lemonsqueezy.com/lemon.js', policy)
        self.assertIn('frame-src https://*.lemonsqueezy.com', policy)
        self.assertNotIn("'unsafe-inline'", policy)
        self.assertIn(b'js/checkout-overlay.js', pricing.data)
        self.assertIn(b'billing-checkout-link', pricing.data)
        for path in ('/login', '/billing/membership', '/billing/api/membership'):
            response = self.client.get(path)
            self.assertNotIn('lemonsqueezy.com', response.headers['Content-Security-Policy'])
            self.assertNotIn(b'https://app.lemonsqueezy.com/js/lemon.js', response.data)

    def test_stripe_only_app_does_not_load_lemon_script_or_relax_policy(self):
        config = dict(self.app.config)
        config['BILLING_PLANS'] = {'monthly': self.app.config['BILLING_PLANS']['monthly']}
        app = create_app(config)
        response = app.test_client().get('/billing/pricing')
        self.assertNotIn(b'https://app.lemonsqueezy.com/js/lemon.js', response.data)
        self.assertNotIn('lemonsqueezy.com', response.headers['Content-Security-Policy'])

    def test_checkout_response_identifies_provider_without_leaking_secrets(self):
        with patch.object(self.lemon, 'checkout', return_value='https://store.lemonsqueezy.com/checkout/custom/test'):
            response = self.client.post('/billing/checkout', json={'plan': 'lemon'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'provider': 'lemonsqueezy', 'url': 'https://store.lemonsqueezy.com/checkout/custom/test'})

    def test_checkout_return_recovers_expired_session_without_exposing_membership(self):
        import jwt
        from auth.config import JWT_SECRET
        from auth.db import create_refresh_token
        expired = jwt.encode({'sub': 'one@example.test',
                              'exp': datetime.utcnow() - timedelta(minutes=1)}, JWT_SECRET, algorithm='HS256')
        self.client.set_cookie('access_token', expired)
        create_refresh_token(1, 'test-refresh-token-with-enough-length', datetime.utcnow() + timedelta(days=30))
        self.client.set_cookie('refresh_token', 'test-refresh-token-with-enough-length')
        with patch('billing.routes.membership_rows') as rows:
            page = self.client.get('/billing/membership?checkout=returned')
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'id="billing-session-recovery"', page.data)
            self.assertEqual(page.headers['Cache-Control'], 'no-store')
            rows.assert_not_called()
        self.assertEqual(self.client.get('/billing/api/membership').status_code, 401)
        self.assertEqual(self.client.post('/refresh', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/billing/api/membership').status_code, 200)
        page = self.client.get('/billing/membership?checkout=returned')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'id="billing-session-recovery"', page.data)

    def test_membership_shell_does_not_bypass_auth_without_refresh_cookie(self):
        self.client.delete_cookie('access_token')
        page = self.client.get('/billing/membership?checkout=returned')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="billing-session-recovery"', page.data)
        self.assertEqual(self.client.get('/billing/api/membership').status_code, 401)
        self.assertEqual(self.client.post('/refresh', headers=self.headers).status_code, 401)
        self.assertEqual(self.client.post('/billing/portal', json={'subscription_id': 1}, headers=self.headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
