"""Allow the payment vendor only on the page that embeds its checkout."""

from functools import wraps


def configure_overlay_policy(app):
    if "lemonsqueezy" not in app.extensions["billing_providers"]:
        return
    security = app.extensions["security_headers"]
    policy = dict(security.content_security_policy)
    policy.update({
        # The documented app URL redirects to this exact CDN resource.
        "script-src": ["'self'", "https://app.lemonsqueezy.com/js/lemon.js",
                       "https://assets.lemonsqueezy.com/lemon.js"],
        "frame-src": ["https://*.lemonsqueezy.com"],
        "connect-src": ["'self'"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
    })
    original = app.view_functions["billing.pricing"]

    @wraps(original)
    def pricing_with_overlay():
        return original()

    # A per-app wrapper avoids mutating the shared blueprint view across app factories.
    app.view_functions["billing.pricing"] = security(content_security_policy=policy)(pricing_with_overlay)
