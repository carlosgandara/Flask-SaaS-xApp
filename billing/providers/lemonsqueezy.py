"""Lemon Squeezy JSON:API adapter. Store, mode and variant are server-owned."""

import hashlib
import hmac

from billing.errors import CheckoutNotCreated, InvalidWebhook, ProviderUnavailable
from .base import Event, Provider, Snapshot, decode_event, identifier, safe_url, timestamp


class LemonSqueezyProvider(Provider):
    name = "lemonsqueezy"
    api_origin = "https://api.lemonsqueezy.com"

    def request(self, method, path, **kwargs):
        headers = {"Authorization": "Bearer " + self.config["LEMONSQUEEZY_API_KEY"],
                   "Accept": "application/vnd.api+json", "Content-Type": "application/vnd.api+json"}
        return super().request(method, path, headers=headers, **kwargs)

    def validate_plan(self, product_id):
        try:
            prices = self.request("GET", "/v1/prices", params={
                "filter[variant_id]": product_id, "page[size]": 1}).get("data", [])
        except ProviderUnavailable as exc:
            raise CheckoutNotCreated(str(exc)) from exc
        price = prices[0].get("attributes", {}) if prices else {}
        if not prices:
            raise CheckoutNotCreated("No price found for the configured Lemon Squeezy variant. Check the variant ID, API key and test/live mode")
        if (str(price.get("variant_id")) != product_id or price.get("category") != "subscription"
                or price.get("scheme") != "standard" or price.get("usage_aggregation") is not None):
            raise CheckoutNotCreated("Configure a standard, non-metered Lemon Squeezy subscription variant")

    def check_store(self):
        self.request("GET", "/v1/stores/" + identifier(self.config["LEMONSQUEEZY_STORE_ID"]))

    def checkout(self, intent, email):
        self.validate_plan(intent.product_id)
        payload = {"data": {"type": "checkouts", "attributes": {
            "checkout_options": {"embed": True},
            "checkout_data": {"email": email, "custom": {"checkout_ref": intent.id}},
            "product_options": {"enabled_variants": [int(intent.product_id)],
                                "redirect_url": self.config["BILLING_BASE_URL"] + "/billing/membership?checkout=returned"},
            "expires_at": intent.expires_at.isoformat() + "Z", "test_mode": not self.live},
            "relationships": {"store": {"data": {"type": "stores", "id": self.config["LEMONSQUEEZY_STORE_ID"]}},
                              "variant": {"data": {"type": "variants", "id": intent.product_id}}}}}
        obj = self.request("POST", "/v1/checkouts", json=payload)["data"]["attributes"]
        if obj.get("test_mode") is not (not self.live):
            raise InvalidWebhook("Provider mode mismatch")
        return safe_url(obj.get("url"), self.name)

    def verify_payload(self, raw, headers):
        expected = hmac.new(self.config["LEMONSQUEEZY_WEBHOOK_SECRET"].encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("X-Signature", "")):
            raise InvalidWebhook("Invalid webhook signature")
        payload = decode_event(raw)
        meta = payload.get("meta", {})
        # Some documented payloads omit meta.test_mode. The API snapshot below
        # always validates test_mode and store_id before granting any access.
        mode = meta.get("test_mode", payload.get("data", {}).get("attributes", {}).get("test_mode"))
        if mode is not None and mode is not (not self.live):
            raise InvalidWebhook("Webhook mode mismatch")
        payload["_delivery_id"] = hashlib.sha256(raw).hexdigest()
        return payload

    def event(self, raw, headers):
        return self.event_from_payload(self.verify_payload(raw, headers))

    def event_from_payload(self, payload):
        meta = payload.get("meta", {})
        kind = meta.get("event_name", "")
        data = payload.get("data", {})
        if kind in {"subscription_created", "subscription_updated", "subscription_cancelled",
                    "subscription_resumed", "subscription_expired", "subscription_paused", "subscription_unpaused"}:
            if data.get("type") != "subscriptions":
                raise InvalidWebhook("Invalid subscription event")
            sub_id = data.get("id")
        elif kind in {"subscription_payment_success", "subscription_payment_failed",
                      "subscription_payment_recovered", "subscription_payment_refunded"}:
            if data.get("type") != "subscription-invoices":
                raise InvalidWebhook("Invalid subscription invoice event")
            sub_id = data.get("attributes", {}).get("subscription_id")
        else:
            return None
        # Lemon Squeezy has no stable event ID or signed timestamp. Hash raw bytes for dedup;
        # fetch authoritative state on every new delivery to make old signed replays harmless.
        return Event(payload["_delivery_id"], kind, identifier(sub_id),
                     (meta.get("custom_data") or {}).get("checkout_ref"),
                     kind == "subscription_payment_refunded")

    def subscription_data(self, sub_id):
        data = self.request("GET", "/v1/subscriptions/" + identifier(sub_id))["data"]
        obj = data["attributes"]
        if (str(obj.get("store_id")) != self.config["LEMONSQUEEZY_STORE_ID"]
                or obj.get("test_mode") is not (not self.live)):
            raise InvalidWebhook("Subscription store or mode mismatch")
        return data, obj

    def subscription(self, sub_id):
        data, obj = self.subscription_data(sub_id)
        status = obj.get("status", "unknown")
        if status == "on_trial":
            end = obj.get("trial_ends_at")
        elif status in {"cancelled", "expired"}:
            end = obj.get("ends_at")
        else:
            end = obj.get("renews_at")
        return Snapshot(identifier(data["id"]), identifier(obj.get("customer_id")),
                        identifier(obj.get("variant_id")), status, timestamp(end), bool(obj.get("cancelled")))

    def portal(self, subscription):
        _, obj = self.subscription_data(subscription.external_id)
        if str(obj.get("customer_id")) != subscription.customer_id:
            raise InvalidWebhook("Subscription ownership mismatch")
        return safe_url(obj.get("urls", {}).get("customer_portal"), self.name)

    def list_subscriptions(self, cursor=None):
        page = int(cursor or 1)
        data = self.request('GET', '/v1/subscriptions', params={
            'filter[store_id]': self.config['LEMONSQUEEZY_STORE_ID'], 'page[number]': page, 'page[size]': 100})
        records = []
        for item in data.get('data', []):
            attributes = item['attributes']
            if (str(attributes.get('store_id')) != self.config['LEMONSQUEEZY_STORE_ID']
                    or attributes.get('test_mode') is not (not self.live)):
                raise InvalidWebhook('Subscription store or mode mismatch during reconciliation')
            records.append({'id': identifier(item['id']), 'product_id': identifier(attributes['variant_id']),
                            'checkout_ref': None})
        last_page = int(data.get('meta', {}).get('page', {}).get('lastPage', page))
        return records, page + 1 if page < last_page else None
