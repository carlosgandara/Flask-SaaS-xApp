"""Provider boundary: HTTPS transport and small normalized data contracts."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from urllib.parse import urlsplit

import requests

from billing.errors import InvalidWebhook, ProviderUnavailable


@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    subscription_id: str
    checkout_ref: str | None = None
    block_access: bool = False


@dataclass(frozen=True)
class Snapshot:
    id: str
    customer_id: str
    product_id: str
    status: str
    access_until: datetime | None
    cancel_at_period_end: bool
    checkout_ref: str | None = None


def identifier(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise InvalidWebhook("Invalid provider identifier")
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise InvalidWebhook("Invalid provider identifier")
    return value


def timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            result = datetime.fromtimestamp(value, timezone.utc)
        else:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if result.tzinfo is None:
                raise ValueError("Timezone required")
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError, AttributeError, OSError) as exc:
        raise InvalidWebhook("Invalid provider date") from exc


def decode_event(raw):
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError) as exc:
        raise InvalidWebhook("Invalid webhook JSON") from exc


def safe_url(value, provider):
    if not isinstance(value, str) or len(value) > 2048:
        raise ProviderUnavailable("Provider returned an invalid redirect")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        allowed = (host in {"checkout.stripe.com", "billing.stripe.com"} if provider == "stripe"
                   else host.endswith(".lemonsqueezy.com"))
        if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
                or parsed.port not in {None, 443} or any(ord(c) < 32 for c in value)):
            raise ValueError()
    except ValueError as exc:
        raise ProviderUnavailable("Provider returned an invalid redirect") from exc
    return value


class Provider:
    def __init__(self, config):
        self.config = config
        self.live = config["BILLING_MODE"] == "live"

    def request(self, method, path, **kwargs):
        # Origins are hardcoded by each adapter. Never follow redirects with API credentials.
        try:
            with requests.Session() as session:
                response = session.request(method, self.api_origin + path, timeout=(3.05, 12),
                                           allow_redirects=False, **kwargs)
            if not 200 <= response.status_code < 300:
                logging.getLogger(__name__).warning("billing.provider_http_error provider=%s status=%s",
                                                    type(self).__name__, response.status_code)
                if response.status_code == 404:
                    raise ProviderUnavailable("Payment resource not found. Check configured IDs and test/live mode")
                if response.status_code in {401, 403}:
                    raise ProviderUnavailable("Payment provider rejected the API credentials or permissions")
                raise ProviderUnavailable("Payment provider is temporarily unavailable")
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (requests.RequestException, ValueError) as exc:
            logging.getLogger(__name__).warning("billing.provider_transport_error provider=%s type=%s",
                                                type(self).__name__, type(exc).__name__)
            raise ProviderUnavailable("Payment provider is temporarily unavailable") from exc
