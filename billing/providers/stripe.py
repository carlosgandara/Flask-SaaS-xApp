"""Stripe hosted checkout; explicit API version; raw-body signature verification."""

import hashlib
import hmac
import time

from billing.errors import CheckoutNotCreated, InvalidWebhook, ProviderUnavailable
from .base import Event, Provider, Snapshot, decode_event, identifier, safe_url, timestamp


class StripeProvider(Provider):
    name = "stripe"
    api_origin = "https://api.stripe.com"
    api_version = "2025-06-30.basil"

    def request(self, method, path, **kwargs):
        headers = {"Authorization": "Bearer " + self.config["STRIPE_SECRET_KEY"],
                   "Stripe-Version": self.api_version}
        headers.update(kwargs.pop("headers", {}))
        return super().request(method, path, headers=headers, **kwargs)

    def validate_plan(self, product_id):
        try:
            price = self.request("GET", "/v1/prices/" + identifier(product_id))
        except ProviderUnavailable as exc:
            raise CheckoutNotCreated(str(exc)) from exc
        if (price.get("livemode") is not self.live or price.get("active") is not True
                or price.get("type") != "recurring" or not price.get("recurring")
                or price["recurring"].get("usage_type") != "licensed"):
            raise CheckoutNotCreated("Configure an active recurring Stripe price in the selected test/live mode")

    def checkout(self, intent, email):
        self.validate_plan(intent.product_id)
        base = self.config["BILLING_BASE_URL"]
        data = {"mode": "subscription", "line_items[0][price]": intent.product_id,
                "line_items[0][quantity]": "1", "customer_email": email,
                "client_reference_id": intent.id,
                "subscription_data[metadata][checkout_ref]": intent.id,
                "success_url": base + "/billing/membership?checkout=returned",
                "cancel_url": base + "/billing/pricing"}
        # UTC conversion must not depend on the host machine's timezone.
        from datetime import timezone
        data["expires_at"] = int(intent.expires_at.replace(tzinfo=timezone.utc).timestamp())
        result = self.request("POST", "/v1/checkout/sessions", data=data,
                              headers={"Idempotency-Key": "checkout-" + intent.id})
        if result.get("livemode") is not self.live:
            raise InvalidWebhook("Provider mode mismatch")
        return safe_url(result.get("url"), self.name)

    def verify_payload(self, raw, headers):
        try:
            fields = [part.split("=", 1) for part in headers.get("Stripe-Signature", "").split(",")]
            times = [value for key, value in fields if key == "t"]
            if len(times) != 1:
                raise ValueError()
            sent = int(times[0])
            if abs(time.time() - sent) > 300:
                raise ValueError()
            expected = hmac.new(self.config["STRIPE_WEBHOOK_SECRET"].encode(),
                                times[0].encode() + b"." + raw, hashlib.sha256).hexdigest()
            if not any(hmac.compare_digest(expected, value) for key, value in fields if key == "v1"):
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise InvalidWebhook("Invalid webhook signature") from exc
        payload = decode_event(raw)
        if payload.get("livemode") is not self.live:
            raise InvalidWebhook("Webhook mode mismatch")
        return payload

    def event(self, raw, headers):
        return self.event_from_payload(self.verify_payload(raw, headers))

    def event_from_payload(self, payload):
        kind = payload.get("type", "")
        obj = payload.get("data", {}).get("object", {})
        ref = None
        blocked = False
        if kind in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            sub_id = obj.get("id")
            ref = obj.get("metadata", {}).get("checkout_ref")
        elif kind in {"invoice.paid", "invoice.payment_failed", "invoice.payment_action_required"}:
            sub_id = self.invoice_subscription(obj)
            if not sub_id:
                return None
        elif kind in {"charge.refunded", "charge.dispute.created"}:
            charge = obj if kind == "charge.refunded" else self.request("GET", "/v1/charges/" + identifier(obj.get("charge")))
            invoice_id = charge.get("invoice")
            if not invoice_id:
                payment_intent = charge.get("payment_intent")
                if not payment_intent:
                    return None  # A non-subscription payment, outside this module.
                payments = self.request("GET", "/v1/invoice_payments", params={
                    "payment[type]": "payment_intent",
                    "payment[payment_intent]": identifier(payment_intent), "limit": 2})
                if payments.get("has_more") or len(payments.get("data", [])) != 1:
                    raise ProviderUnavailable("Refund or dispute needs billing reconciliation")
                invoice_id = payments["data"][0].get("invoice")
            invoice = self.request("GET", "/v1/invoices/" + identifier(invoice_id))
            sub_id = self.invoice_subscription(invoice)
            if not sub_id:
                return None
            blocked = True
        else:
            return None
        return Event(identifier(payload.get("id")), kind, identifier(sub_id), ref, blocked)

    @staticmethod
    def invoice_subscription(invoice):
        return ((invoice.get("parent") or {}).get("subscription_details") or {}).get("subscription") or invoice.get("subscription")

    def subscription(self, sub_id):
        obj = self.request("GET", "/v1/subscriptions/" + identifier(sub_id))
        if obj.get("livemode") is not self.live:
            raise InvalidWebhook("Subscription mode mismatch")
        items = obj.get("items", {}).get("data", [])
        if len(items) != 1 or obj.get("items", {}).get("has_more"):
            raise InvalidWebhook("Only single-plan subscriptions are supported")
        item = items[0]
        status = obj.get("status", "unknown")
        if obj.get("pause_collection"):
            status = "paused"
        end = obj.get("trial_end") if status == "trialing" else item.get("current_period_end")
        if obj.get("cancel_at"):
            end = min(end, obj["cancel_at"]) if end else obj["cancel_at"]
        return Snapshot(identifier(obj.get("id")), identifier(obj.get("customer")),
                        identifier(item.get("price", {}).get("id")), status, timestamp(end),
                        bool(obj.get("cancel_at_period_end")), obj.get("metadata", {}).get("checkout_ref"))

    def portal(self, subscription):
        result = self.request("POST", "/v1/billing_portal/sessions", data={
            "customer": subscription.customer_id,
            "return_url": self.config["BILLING_BASE_URL"] + "/billing/membership"})
        return safe_url(result.get("url"), self.name)

    def list_subscriptions(self, cursor=None):
        params = {'limit': 100, 'status': 'all'}
        if cursor:
            params['starting_after'] = identifier(cursor)
        data = self.request('GET', '/v1/subscriptions', params=params)
        records = []
        for item in data.get('data', []):
            if item.get('livemode') is not self.live:
                raise InvalidWebhook('Subscription mode mismatch during reconciliation')
            items = item.get('items', {}).get('data', [])
            if len(items) != 1:
                continue
            records.append({'id': identifier(item['id']), 'product_id': identifier(items[0]['price']['id']),
                            'checkout_ref': (item.get('metadata') or {}).get('checkout_ref')})
        return records, identifier(data['data'][-1]['id']) if data.get('has_more') and data.get('data') else None
