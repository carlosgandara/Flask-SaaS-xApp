# Integrated Despacio application

Root `app.py` registers authentication, billing and learning in one Flask app.
`integration.py` supplies the verified account identity, membership policy, CSRF
validation, shared browser session recovery and SQLAlchemy progress adapter.

Accounts, subscriptions and learning progress use the same host-owned engine and
`DATABASE_URL`. The configured Neon URL must keep its SSL parameters. Run:

```powershell
.venv/Scripts/python.exe -m flask --app app db-upgrade
.venv/Scripts/python.exe -m flask --app app app-check
.venv/Scripts/python.exe app.py
```

Migration `0002_learning` adds `learning_progress` and `learning_content`. Progress
is keyed by authenticated integer user ID and language, with revision checks to
reject concurrent overwrites. Content stays in versioned `learning/data` JSON by
default; the content database adapter is available for a future explicit import.
No local-user progress is automatically assigned to registered accounts.

Verified users can learn when billing is disabled. With billing enabled, all
learning pages and APIs require server-confirmed active membership. HTML requests
recover expired sessions or redirect to pricing; APIs return 401/403. Static assets
remain public. All learning mutations require the existing CSRF token and Origin.
Learning clients share the account client's coordinated refresh and retry behavior.

The separate billing worker is required to apply signed webhook updates:

```powershell
.venv/Scripts/python.exe -m flask --app app billing-worker
```

`app.py` is the single application launcher. The package factory
`learning.create_app()` remains available for isolated learning tests and demos;
the production launcher uses the authenticated, database-backed app.

Validation runs on isolated SQLite with mocked providers: account isolation,
membership enforcement, CSRF rejection, expired session recovery, learning storage
migrations and existing learning behaviors. Live checkout/webhook delivery still
requires a provider sandbox exercise.
