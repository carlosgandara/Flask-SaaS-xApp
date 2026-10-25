# Billing

Optional subscription billing for one application and its own users/database.
Stripe and Lemon Squeezy are separate adapters. No shared account system between
independent apps is introduced. Billing is disabled by default.

## Boundaries

| File | Responsibility |
| --- | --- |
| `__init__.py` | Register billing and maintenance commands |
| `config.py` | Validate environment, canonical origin, provider credentials, plan catalog |
| `routes.py` | HTTP input, authentication, CSRF, request limits, safe errors |
| `service.py` | Checkout coordination and transactional membership rules |
| `models.py` | Checkout intents, subscriptions, receipts, inbox, leases and audit records |
| `inbox.py`, `worker.py`, `leases.py` | Durable notifications, claims, retries and recovery |
| `access.py` | Verified-user and paid-access decorators |
| `providers/` | Provider API calls, signatures, normalized events/subscriptions |
| `templates/billing/` | Pricing and membership pages |
| `static/js/billing.js` | Checkout and portal actions; no secrets or access decisions |
| `static/js/checkout-overlay.js` | Lemon.js overlay lifecycle and bounded confirmation polling |
| `security.py` | Payment script/frame permissions scoped to the pricing page |

The tables use the existing SQLAlchemy engine and `users.id`. No card numbers,
raw payment tokens, raw webhook bodies, or API credentials are stored in billing
tables. Checkout URLs are temporary credentials: restrict database access and
never log them. The module stores only the data needed to link and authorize
subscriptions. There is no public endpoint for modifying membership status.

## Local setup

1. Install `requirements.txt` and configure authentication as described in the
   root README. Use test credentials first.
2. Create a recurring, fixed-price membership in your provider dashboard. For
   Stripe, enable/configure the hosted customer portal. For Lemon Squeezy, use
   standard subscription pricing, not metered or pay-what-you-want pricing.
3. Add ONE of the following examples to your private `.env`, replacing example
   IDs and secrets with your own. Both adapters may be enabled, but a user can
   have only one current subscription in this app. A plan maps to one provider;
   switching providers does not migrate existing subscriptions automatically.

Stripe example:

```dotenv
BILLING_ENABLED=true
BILLING_MODE=test
BASE_URL=http://localhost:5000
STRIPE_SECRET_KEY=sk_test_REPLACE
STRIPE_WEBHOOK_SECRET=whsec_REPLACE_WITH_ENDPOINT_SECRET
BILLING_PLANS='{"monthly":{"name":"Monthly membership","description":"Access to paid features","price_label":"$10/month","provider":"stripe","product_id":"price_REPLACE"}}'
```

Lemon Squeezy example:

```dotenv
BILLING_ENABLED=true
BILLING_MODE=test
BASE_URL=http://localhost:5000
LEMONSQUEEZY_API_KEY=REPLACE
LEMONSQUEEZY_STORE_ID=123
LEMONSQUEEZY_WEBHOOK_SECRET=REPLACE_WITH_RANDOM_32_TO_40_CHARACTERS
BILLING_PLANS='{"monthly":{"name":"Monthly membership","description":"Access to paid features","price_label":"$10/month","provider":"lemonsqueezy","product_id":"456"}}'
```

For Lemon Squeezy, `product_id` means the variant ID; for Stripe, it means the
recurring price ID. IDs are strings. `price_label` is presentation only: the
provider determines the charge, so keep this text consistent with provider
pricing. A one-time payment product is rejected before creating checkout.
Trials follow the provider's product configuration. There is no local free-trial
counter or free access based on a browser flag.

4. Apply the versioned database upgrade and run read-only readiness checks:

```bash
python -m flask --app app db-upgrade
python -m flask --app app app-check
python -m flask --app app billing-check
```

`db-upgrade` upgrades the configured database without creating a second database.
Back up and test existing installations on staging first. `billing-init-db` is a
compatibility alias for this versioned upgrade, including auth changes. Existing
users/subscriptions are preserved; see the root README for token-link transition
and duplicate-email checks. `billing-check` calls provider GET endpoints only and
checks store, mode and recurring plan IDs before any checkout is created.

5. Configure webhooks at the provider. For local testing, use Stripe CLI or a
   trusted HTTPS tunnel. Keep `BASE_URL` equal to the origin actually used by the
   browser: mixing localhost and 127.0.0.1 will fail the Origin check.
6. Start the web app. In a second terminal start the separate worker:

```bash
python -m flask --app app billing-worker
```

Register/verify/log in and visit `/billing/pricing`. Both processes load the same
private environment and use the same database. The web app does not start an
embedded worker. Run one or more supervised workers in production.

Billing does not change `.env`, create products, charge cards, or change your Neon
schema automatically. Real account configuration and a provider sandbox checkout
are still required before enabling live billing.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/billing/pricing` | Public pricing page and browser CSRF session |
| GET | `/billing/membership` | Verified user's own subscription status |
| GET | `/billing/api/membership` | Same status as JSON |
| POST | `/billing/checkout` | Create/reuse checkout for a server-defined plan |
| POST | `/billing/portal` | Manage an owned subscription at the provider |
| POST | `/billing/webhooks/stripe` | Stripe-signed events |
| POST | `/billing/webhooks/lemonsqueezy` | Lemon Squeezy-signed events |

Browser POSTs require a signed Flask session CSRF token in `X-CSRF-Token`, an
exact canonical `Origin`, verified login, and JSON input. User/customer IDs,
prices, return URLs, and provider selections cannot be supplied by the browser.
Only signed webhook endpoints bypass browser CSRF and IP-based application rate
limits; constrain webhook traffic at the edge as well. Bodies are limited to
256 KiB for webhooks and 4 KiB for billing actions. Responses are not cached.

## Provider events

### Checkout presentation

Lemon Squeezy Subscribe opens the official Lemon.js checkout overlay, using the
same server-created, account-linked checkout URL. Customers can close it and
reopen the pending checkout. A full-page link remains available, and missing or
failing overlay scripts fall back to hosted checkout without creating a second
checkout. Stripe checkout and billing portals continue to use full-page redirects.

Only the pricing page loads Lemon.js. Its content security policy permits the
documented script URL and its exact CDN redirect (`assets.lemonsqueezy.com/lemon.js`),
plus HTTPS Lemon Squeezy frames. Login, membership, and API policies stay unchanged.
Inline scripts and arbitrary inline styles remain blocked. External presentation
styles cover the vendor's generated loader and frame; provider-generated inline
style attempts may still appear as blocked CSP messages in browser diagnostics.

After the vendor reports checkout success, Subscribe buttons are hidden and the
old "complete checkout" prompt is cleared while membership is confirmed. This
prevents contradictory payment instructions without granting browser-controlled
access.

The vendor's Checkout.Success callback is presentation only. The page checks the
authenticated membership API up to eight times over approximately 28 seconds,
waiting for a matching plan with server-confirmed access. It then opens Membership.
If confirmation is delayed or the network fails, the page points to Membership
and explains that another payment is unnecessary. No order data from the callback
is used to grant access. Test checkout, close/reopen, and payment-method behavior
in your supported browsers before enabling live payments.

Run browser-script checks without a network or actual payments:

```bash
node tests/test_billing_browser.cjs
node tests/test_checkout_overlay.cjs
```

### Webhook destinations

Stripe: configure a **snapshot** webhook destination using API version
`2025-06-30.basil`, matching the adapter's pinned REST API version, for:

```text
customer.subscription.created
customer.subscription.updated
customer.subscription.deleted
invoice.paid
invoice.payment_failed
invoice.payment_action_required
charge.refunded
charge.dispute.created
```

Lemon Squeezy: subscribe to:

```text
subscription_created
subscription_updated
subscription_cancelled
subscription_resumed
subscription_expired
subscription_paused
subscription_unpaused
subscription_payment_success
subscription_payment_failed
subscription_payment_recovered
subscription_payment_refunded
```

The adapters validate signatures against the exact raw bytes. Stripe additionally
requires a signed timestamp within five minutes. Lemon Squeezy does not provide
an equivalent signed timestamp; its raw-payload hash is the delivery identity,
and current subscription state is fetched from the authenticated API rather than
trusting an old signed payload. Lemon Squeezy store and test/live mode are checked.

Verified deliveries are committed to `billing_webhook_inbox` before HTTP 200.
A database failure returns 503 so the provider retries. The HTTP handler makes
no provider API calls. The worker applies supported deliveries; duplicates are
safe. Unknown checkout references retry, then enter `needs_review`; subscriptions
are never linked by email. Unsupported signed event types are acknowledged and
ignored. Subscribe to the supported events above so relevant changes are delivered.

## Access and cancellation policy

Use the public decorator on every paid page AND paid API route:

```python
from billing.access import membership_required

@app.get('/paid-feature')
@membership_required()  # Or membership_required('monthly') for a specific plan.
def paid_feature():
    return {'message': 'Paid feature'}
```

The existing dashboard/profile remain free authenticated pages. Membership lives
in the database, never inside a long-lived JWT claim or a browser variable.

- Access requires a recognized configured plan, active/trial status, and a
  future provider-supplied access deadline. Removing a plan denies its access;
  retain old plan entries while grandfathered subscribers should keep access.
- Stripe scheduled cancellation retains active access until its deadline.
  Lemon Squeezy `cancelled` retains access until `ends_at`.
- `past_due`, unpaid, paused, expired, unknown, or missing-deadline states deny
  access. This intentionally has no payment-failure grace period.
- Subscription refunds (including partial refunds) and Stripe disputes block
  access pending support review. This block survives later subscription updates.
  Refunds do not automatically cancel future billing: the operator must review
  and cancel or restore service using the provider dashboard and an authorized
  database change. There is no browser/admin bypass endpoint.
- Lemon Squeezy chargeback handling is limited to the events it delivers. Monitor
  provider notices and review accounts when the provider cannot send a matching
  subscription event. Do not assume all financial disputes are covered by a
  subscription lifecycle event.

Checkout return URLs never activate access. A pending checkout is saved before
calling the provider so fast webhooks can resolve it. Repeated clicks reuse the
same unexpired checkout. An ambiguous network failure stays pending for up to
one hour instead of retrying a potentially successful payment POST. Do not
delete pending records to force retries without checking provider state first.

If the short-lived access cookie expires during hosted checkout, the membership
HTML route serves a page with no private data. Its external script calls the
protected membership API; the existing authenticated-fetch helper refreshes the
session using POST /refresh and retries. The page reloads only after authentication
succeeds. Missing/invalid refresh credentials send the user to login; a network
failure shows a retry action. No payment is repeated and GET requests do not
rotate tokens. The API and billing actions remain authenticated.

## Concurrency, recovery, and deployment

The web service verifies signatures and stores minimized linkage fields, not raw
webhook bodies. Workers claim due jobs with PostgreSQL `SKIP LOCKED`. Claims expire
after 120 seconds so a crashed worker's job becomes available again. Ownership
tokens prevent an expired worker from finishing another worker's job.

Before reading provider state, the worker acquires a per-subscription database
lease. Provider network calls happen outside transactions; a short final
transaction verifies the lease, locks the user and commits membership plus its
receipt atomically. This prevents stale snapshots from overwriting newer state
without holding account locks during slow provider requests. Unique identities
make duplicate delivery or a crash after commit safe.

Transient failures use exponential backoff with jitter, capped at one hour.
After eight attempts, or for malformed/permanent failures, the job enters
`needs_review`. Safe error types and job IDs appear in logs; payloads, tokens and
SQL parameters do not. Unprocessed jobs remain durable during a web/worker restart.

Every 15 minutes by default, a worker queues current provider subscriptions and
discovers purchases missing locally. A leader lease avoids duplicate scheduled
scans, and each subscription retains its own processing lease. Stripe purchases
with a recorded checkout reference can recover automatically. Lemon Squeezy's
subscription listing does not expose the app's checkout reference: unmatched
purchases enter review until a trusted operator explicitly links a known checkout.
No email matching is used. Known subscriptions removed from the plan catalog still
receive state reconciliation, although removed plans no longer grant paid access.
Reconciliation does not reconstruct missing historical refund/dispute events;
monitor provider notices and redeliver those events when needed.

Operator commands (run in the protected deployment environment):

```bash
# Readiness and backlog; --check exits nonzero for missing workers, lag >5m, review jobs.
python -m flask --app app billing-status --check
# List review jobs without exposing saved payloads.
python -m flask --app app billing-jobs --status needs_review
# Queue an immediate provider reconciliation (worker processes the jobs).
python -m flask --app app billing-reconcile
# Process at most one job without scheduled reconciliation.
python -m flask --app app billing-worker --once
# After investigating/fixing the cause, queue a retained review/retry job.
python -m flask --app app billing-retry 42 --actor operator-name --reason "Reviewed provider state"
# Link only after independently verifying purchase ownership and the checkout record.
python -m flask --app app billing-retry 42 --actor operator-name --reason "Verified ownership" --checkout-ref KNOWN_CHECKOUT_REFERENCE
```

Retry records an operator action, reason and target job. The actor is an audit
label; access is authorized by operating-system/deployment IAM permissions, not
by this string. There is no public retry or membership-editing endpoint. The
worker verifies immutable ownership and provider state again before granting
access. Never invent a checkout reference or link by email alone.

### Understanding worker output

The worker prints `Starting billing worker (test mode). Checking database schema...`
(or live mode), then `Billing worker online. Waiting for jobs; keep this terminal
open. Press Ctrl+C to stop.` after its first successful heartbeat. It runs
continuously and can stay quiet when there are no pending jobs. A startup database
failure prints a safe connection/check error. `Aborted!` alone is not a diagnosis;
allow startup to finish and inspect preceding output before assuming the cause.
Keep it open separately from the web server. On Windows, use the same project
Python for every process so globally installed packages do not differ:

```powershell
.venv/Scripts/python.exe app.py
# Separate terminal:
.venv/Scripts/python.exe -m flask --app app billing-worker
# Another terminal for a status snapshot:
.venv/Scripts/python.exe -m flask --app app billing-status --check
.venv/Scripts/python.exe -m flask --app app billing-jobs --status needs_review
```

For example, `Workers online: 1; oldest waiting event: 0s; event states:
{'done': 2, 'needs_review': 2}` means a worker has reported recently, no jobs
are waiting, two jobs finished and two require investigation. `--check` exits
nonzero for review jobs even when the worker is running; that exit is intended
for monitoring and does not indicate a worker crash.

`billing.unmatched_purchase` means a subscription was found in the configured
provider/store/mode/catalog without a corresponding local subscription or known
checkout reference. The job is saved for review. This can happen with older test
purchases or missing webhook processing, but the log alone does not establish the
cause or ownership. Inspect the provider and original checkout records before
linking/retrying. Starting another subscription does not resolve the missing link.

Monitor `billing-status --check`, database availability and provider delivery
failures with your hosting alert service. This repository supplies the checks,
not a provisioned monitoring account. Heartbeats update every 30 seconds; a
worker is considered stale after two minutes. Graceful termination finishes the
current bounded operation; forced termination is recovered through expiring claims.

Completed minimized payloads are cleared after 30 days by default. Deduplication
identities/receipts remain, and review/failure payloads are retained for recovery.
Restrict database access and define retention for audit/customer records. Tune
`BILLING_POLL_SECONDS`, `BILLING_RECONCILE_SECONDS` and
`BILLING_EVENT_RETENTION_DAYS` in the environment example.

Production requires HTTPS, secure cookies, PostgreSQL and shared Redis request
limits. Use `APP_ENV=production`, a production WSGI service and a separately
supervised worker. Keep test/live deployments, databases and credentials separate.
Use a permanent HTTPS webhook endpoint. Configure trusted proxy hops only for
your actual private backend/proxy topology. Protect database/provider secrets,
checkout URLs and signed portal links; do not log HTTP query strings or bodies.

## Debugging and verification

Browser errors include a safe reference and responses carry `X-Request-ID`.
Worker logs carry job ID, state and exception type. Inspect provider deliveries,
`billing_webhook_inbox` and receipts together: a receipt means the membership
transaction completed, whereas an inbox row alone means the event was saved.
An accepted HTTP 200 does not imply that the worker has processed it yet.

```bash
python -m unittest discover -s tests -v
node tests/test_auth_browser.cjs
node tests/test_billing_browser.cjs
node tests/test_checkout_overlay.cjs
python -m pip_audit -r requirements.txt
```

Tests use synthetic credentials, isolated databases and mocked payment APIs.
PostgreSQL/Redis tests require dedicated `TEST_POSTGRES_URL` / `TEST_REDIS_URL`;
GitHub Actions provisions these services. Test a real provider sandbox checkout,
portal, cancellation and webhook delivery before enabling live billing. This is
a modest deployment architecture; benchmark queue lag and reconciliation time
before increasing volume. It is not a completed independent security audit.

## Provider references

- [Stripe Checkout](https://docs.stripe.com/api/checkout/sessions/create)
- [Stripe webhooks](https://docs.stripe.com/webhooks)
- [Stripe customer portal](https://docs.stripe.com/api/customer_portal/sessions/create)
- [Stripe invoice payments](https://docs.stripe.com/api/invoice-payment/list)
- [Lemon Squeezy checkout](https://docs.lemonsqueezy.com/api/checkouts/create-checkout)
- [Lemon Squeezy signatures](https://docs.lemonsqueezy.com/help/webhooks/signing-requests)
- [Lemon Squeezy subscription listing](https://docs.lemonsqueezy.com/api/subscriptions/list-all-subscriptions)
- [Lemon Squeezy subscription lifecycle](https://docs.lemonsqueezy.com/api/subscriptions/the-subscription-object)
