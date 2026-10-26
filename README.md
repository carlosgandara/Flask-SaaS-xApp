# Despacio — learning, accounts and billing

One Flask app combines language learning, verified accounts and subscription billing
through Lemon Squeezy or Stripe. Accounts, subscriptions and per-account learning
progress share the configured Neon/PostgreSQL database (`DATABASE_URL`). Lesson
content remains versioned in `learning/data`. All 40 levels have shared ten-word
vocabulary sets in Spanish, French and Tagalog (260 concepts per language).
Account progress never uses local-user JSON files in the integrated app.

Open `/` for the public landing page, `/menu` for the learning map, `/games` for
the free arcade, `/pricing` for the access overview, or `/about` for the project story.
The supplied hero image is stored at `static/site/indexmainpage.png`.

- Guests can complete levels 1–3 and play every arcade word set without login.
- Verified free accounts can complete levels 1–6 and save progress in Neon.
- Active membership unlocks levels 7–40; passing each final still unlocks the next level.
- Both arcade games remain free independently of lesson access and completion.
- Guest progress is temporary (rolling six-hour expiry). The integrated app uses the
  configured shared Redis service, or the database when Redis is not configured.
- On a new account's first progress load, trial history in that browser can be imported
  for that language. Existing account progress is never replaced.

Membership is checked on the server for both lesson pages and APIs. Checkout return
URLs never grant access. Keep the billing worker running to apply signed updates.
Disabling billing leaves the first six account levels free but keeps paid levels closed.

`app.py` is the single application launcher.
Run `db-upgrade` after updating: revision `0002_learning` adds learning tables without
changing existing accounts or subscriptions. Historical local JSON profiles are not imported. Current temporary trial progress
can carry into a new account as described above.

Revision `0003_guest_progress` adds shared temporary guest storage. Before restarting
an updated deployment, run `python -m flask --app app db-upgrade` in that service's
environment, then `python -m flask --app app app-check`. For Railway, configure the
upgrade command as the service's pre-deploy command so future deployments apply
pending migrations before serving traffic.

## Boundaries

| Location | Responsibility |
| --- | --- |
| `app.py`, `configuration.py` | Application creation and deployment validation |
| `auth/routes.py` | HTTP input, cookies and safe responses |
| `auth/service.py` | Account, password, session and token transactions |
| `auth/security.py`, `auth/validation.py` | Authentication, CSRF and input validation |
| `auth/db.py` | Shared database connection and auth models |
| `auth/mail_service.py` | Bounded SMTP delivery over verified TLS |
| `billing/routes.py`, `billing/service.py` | Checkout and membership rules |
| `billing/providers/` | Separate Lemon Squeezy and Stripe integrations |
| `billing/inbox.py`, `billing/worker.py`, `billing/leases.py` | Durable deliveries, retries and reconciliation |
| `diagnostics.py` | Safe error references and readiness checks |
| `migrations/` | Versioned database upgrades |
| `tests/`, `.github/workflows/checks.yml` | Regression, concurrency and dependency checks |

## Local setup

Use Python 3.10 or newer. On Windows PowerShell, from the project directory:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Copy the example only for a new installation; preserve an existing private `.env`.
Set a random `JWT_SECRET_KEY`, SMTP credentials and `DATABASE_URL`. The example
explains every setting. Neon/PostgreSQL is required for production; SQLite is
available for development. Generate a secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Apply the schema explicitly, check readiness, then start the development server:

```powershell
.venv/Scripts/python.exe -m flask --app app db-upgrade
.venv/Scripts/python.exe -m flask --app app app-check
.venv/Scripts/python.exe app.py
```

Open http://localhost:5000/login. Use the exact origin in `BASE_URL`; localhost
and 127.0.0.1 have separate cookies. Importing the application never creates or
alters database tables. `db-upgrade` upgrades the configured database; it does
not create a separate Neon database. Billing tables are included even when
billing is disabled. Missing schema produces an actionable 503 response.

## Upgrading an existing installation

Back up the database and test on a staging copy before applying migrations.
Install the updated requirements, then run `db-upgrade` once before restarting
the web service and worker. PostgreSQL migration runs are serialized.

The baseline migration preserves users, password hashes, subscriptions, checkout
intents and processed-event receipts. It normalizes email case/whitespace and
refuses ambiguous duplicate identities rather than merging accounts. Review and
resolve those identities before retrying. Legacy verification/reset links are
invalidated; request a new link through Forgot password or Resend verification.
Old access JWTs lack the new session identifier and are rejected. A valid legacy
refresh cookie can be adopted once; otherwise log in again. A destructive automatic
downgrade is intentionally unavailable. Restore a reviewed backup if rollback is needed.

## Authentication behavior

- Email verification is required before login. Email identities are validated and
  normalized, with a database uniqueness constraint.
- Access JWTs expire after 15 minutes. Refresh rotates on demand after a protected
  request returns 401. Each login session has a fixed maximum lifetime of 30 days.
- Database-backed sessions allow logout, logout-all and password reset to revoke
  existing access immediately. Refresh reuse revokes that session's descendants.
- Password reset and verification tokens are random, stored as indexed SHA256
  hashes, expire, and can be consumed only once. Password hashes use bcrypt; inputs
  longer than 72 UTF-8 bytes are rejected rather than silently truncated.
- Login failure counts are updated under a database lock. Five failures lock the
  account for 15 minutes; shared Redis request limits also apply in production.
- All browser POSTs, including login/refresh/logout, require a signed-session CSRF
  token and the configured Origin. Custom clients must fetch `/auth/csrf`, retain
  cookies and send `X-CSRF-Token` and `Origin` headers.
- Expired HTML sessions recover through a data-free page. Browser refresh calls
  share one promise; Web Locks coordinate supported browsers across tabs.
- SMTP delivery has a timeout and verified TLS. Delivery failures have a safe
  reference; users can request a replacement link. Email is not a background queue. Verification/reset emails include an HTML action
  button, expiry information and a plain-text fallback.

| Method | Path | Purpose |
| --- | --- | --- |
| GET/POST | `/register`, `/login` | Account creation and login |
| GET | `/verify-email` | Consume emailed verification link |
| GET/POST | `/resend-verification`, `/forgot-password` | Request a fresh email link |
| GET/POST | `/reset-password` | Reset form / consume reset token |
| GET | `/auth/csrf`, `/auth/session` | CSRF bootstrap / authenticated session probe |
| POST | `/refresh`, `/logout`, `/logout-all` | Rotate / revoke sessions |
| GET | `/dashboard`, `/profile`, `/protected` | Example protected pages/API |
| GET | `/health` | Database connectivity and application readiness |

## Billing and background worker

Follow [billing setup and recovery](billing/README.md) for provider configuration,
webhook events, membership policy and operator commands. Before checkout:

```powershell
.venv/Scripts/python.exe -m flask --app app billing-check
```

Run the worker in a second terminal, with the same configuration and database:

```powershell
.venv/Scripts/python.exe -m flask --app app billing-worker
```

The worker prints a database-check message, then confirms when it is online.
Keep its terminal open; an idle worker does not exit. `Aborted!` alone does not
identify a cause: startup may have been interrupted. Allow the database check
to finish and inspect any preceding error. Status/review commands are documented
in the billing guide.

Signed payment notifications are committed to the database before acknowledgement.
The separate worker fetches current provider state, updates membership atomically,
retries failures and periodically reconciles provider subscriptions. Checkout
success messages and redirect parameters never grant access. The worker must be
running for membership updates to be applied.

## Stable local webhook address

Set `NGROK_DOMAIN` in your private `.env` to the development hostname assigned
in your ngrok dashboard, without `https://` or a path. Keep
`BASE_URL=http://localhost:5000` and browse the app through localhost.

Stop the previous ngrok tunnel, then run in a separate terminal:

```powershell
.venv/Scripts/python.exe scripts/start_ngrok.py
```

This command always requests the configured domain and prints the exact webhook
URL. Set that URL in Lemon Squeezy once; use the same launcher on future restarts.
It fails rather than silently choosing another hostname. Do not commit your
actual hostname or authtoken in the environment example. Keep three terminals
running during local payment testing: Flask, the billing worker and this tunnel.
A successful checkout with no membership can mean the notification is missing
or queued: check the tunnel destination, then `billing-jobs --status pending`.
Do not create another payment to repair a missing notification. The domain must belong
to your ngrok account. Authentication stays in ngrok CLI configuration.
`--check` validates/displays the configuration without opening a tunnel.
The web app and billing worker still run separately. Restarting the tunnel does
not automatically redeliver previously failed payment notifications.

## Production deployment

Set `APP_ENV=production`, HTTPS `BASE_URL`, `COOKIE_SECURE=True`, an explicit
PostgreSQL URL and shared Redis storage. Startup rejects unsafe production settings.
Use separate test/live databases and provider credentials. Provision backups,
restricted database credentials and a stable HTTPS webhook endpoint.

Run a production web server behind your TLS reverse proxy, and supervise the
worker as a separate service with automatic restart:

```text
waitress-serve --host=127.0.0.1 --port=5000 app:app
python -m flask --app app billing-worker
```

Set `TRUSTED_PROXY_HOPS` only to the actual number of trusted proxies and prevent
direct public access to the backend. The default trusts no forwarded headers.
Run `app-check` during deployment and monitor `billing-status --check`; a nonzero
exit means attention is needed. Connect these to your hosting alert system;
this repository does not provision hosting or send operator alerts by itself.

Errors expose a request reference, not SQL parameters, tokens or provider bodies.
Keep application/HTTP wire debug logging off. Configure proxy/access logs to omit
query strings, Cookie/Authorization headers and webhook bodies because emailed
links contain credentials. Protect CLI access with deployment IAM/OS permissions.
This starter does not include MFA, passkeys, a security audit or an uptime guarantee.

## Verification

Run the Python tests in the application environment:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
node tests/test_auth_browser.cjs
node tests/test_billing_browser.cjs
node tests/test_checkout_overlay.cjs
```

Development/audit tools require Python 3.11 or newer (the application runtime
still supports 3.10). In a separate 3.11+ environment, install and audit with:

```powershell
python -m pip install --upgrade pip setuptools
python -m pip install -r requirements-dev.txt
python -m pip_audit --local
```

Default tests use isolated SQLite and mocked providers. Set `TEST_POSTGRES_URL`
and `TEST_REDIS_URL` to dedicated test services to run concurrency/shared-limit
checks. PostgreSQL tests create and remove only their randomly named schema;
Redis tests remove only their own random key prefix. Never point test services
at customer databases. GitHub Actions provisions both services and tests Python
3.12/3.13, browser behavior and dependencies. Enable required checks in repository
branch protection after the workflow is pushed; local files do not enable that setting.
The full local suite has also passed against temporary PostgreSQL/Redis services
with no skipped checks. These automated checks do not replace an end-to-end
provider sandbox checkout, webhook and portal exercise before live payments.

## License

MIT License

Copyright (c) 2024 Carlos Gandara

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
# Flask-SaaS-xApp
