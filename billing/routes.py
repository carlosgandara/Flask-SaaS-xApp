"""HTTP boundary. Validate input here; keep payment rules in service.py."""

import secrets

from flask import Blueprint, current_app, g, jsonify, render_template, request, session
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from auth.extensions import limiter
from auth.security import csrf_token, check_browser_request
from .access import recover_page_session, verified_user_required
from .errors import BillingError, InvalidWebhook
from .service import TERMINAL_STATUSES, create_checkout, membership_rows, portal_url, provider_for


billing_bp = Blueprint("billing", __name__, url_prefix="/billing", template_folder="templates",
                       static_folder="static")


@billing_bp.before_request
def protect_billing():
    g.billing_request_id = getattr(g, "request_id", secrets.token_hex(12))
    if not current_app.config["BILLING_ENABLED"]:
        return jsonify(error="Billing is not configured"), 503
    if request.method == "POST":
        limit = 262144 if request.endpoint == "billing.webhook" else 4096
        if request.content_length is not None and request.content_length > limit:
            raise RequestEntityTooLarge()
        # The app-wide MAX_CONTENT_LENGTH bounds chunked reads as well. Cache the
        # original bytes so signatures are checked before parsing or reserializing.
        if len(request.get_data(cache=True)) > limit:
            raise RequestEntityTooLarge()


@billing_bp.after_request
def private_responses(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Request-ID"] = getattr(g, "billing_request_id", "")
    return response


@billing_bp.errorhandler(BillingError)
def billing_error(error):
    if error.status_code >= 500:
        current_app.logger.warning("billing.provider_unavailable endpoint=%s request_id=%s",
                                   request.endpoint, g.billing_request_id)
    return jsonify(error=str(error)), error.status_code


@billing_bp.errorhandler(SQLAlchemyError)
def database_error(error):
    # Do not log SQL parameters, raw webhook bodies, portal URLs or credentials.
    current_app.logger.error("billing.database_error type=%s endpoint=%s request_id=%s",
                             type(error).__name__, request.endpoint, g.billing_request_id)
    return jsonify(error="Billing is temporarily unavailable. Please retry later."), 503


def require_csrf():
    check_browser_request()


@billing_bp.get("/pricing")
def pricing():
    return render_template("billing/pricing.html", plans=current_app.config["BILLING_PLANS"], csrf=csrf_token(),
                           lemon_overlay="lemonsqueezy" in current_app.extensions["billing_providers"])


@billing_bp.get("/membership")
@recover_page_session
@verified_user_required
def membership():
    return render_template("billing/membership.html", subscriptions=membership_rows(g.billing_user.id),
                           csrf=csrf_token())


@billing_bp.get("/api/membership")
@verified_user_required
def membership_api():
    rows = membership_rows(g.billing_user.id)
    current = next((row for row in rows if row["status"] not in TERMINAL_STATUSES), None)
    return jsonify(subscriptions=rows, current_subscription=current, can_subscribe=current is None)


@billing_bp.post("/checkout")
@limiter.limit("5 per minute")
@verified_user_required
def checkout():
    require_csrf()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"plan"} or not isinstance(data["plan"], str):
        raise BillingError("Select a valid plan")
    url = create_checkout(g.billing_user.id, data["plan"])
    return jsonify(url=url, provider=current_app.config["BILLING_PLANS"][data["plan"]]["provider"])


@billing_bp.post("/portal")
@limiter.limit("10 per minute")
@verified_user_required
def portal():
    require_csrf()
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or set(data) != {"subscription_id"}
            or type(data["subscription_id"]) is not int):
        raise BillingError("Select a valid subscription")
    return jsonify(url=portal_url(g.billing_user.id, data["subscription_id"]))


@billing_bp.post("/webhooks/<provider>")
@limiter.exempt
def webhook(provider):
    from .inbox import enqueue
    adapter = provider_for(provider)
    try:
        payload = adapter.verify_payload(request.get_data(), request.headers)
        outcome = enqueue(provider, payload, g.billing_request_id)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise InvalidWebhook("Malformed provider event") from exc
    current_app.logger.info("billing.webhook provider=%s outcome=%s request_id=%s", provider, outcome, g.billing_request_id)
    return jsonify(received=True)
