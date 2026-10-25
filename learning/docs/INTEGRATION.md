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

The integrated experience allows guest levels 1–3, verified-account levels 1–6,
and membership levels 7–40. Lesson completion still controls sequential unlocking.
Free Word Match and arcade Word Shooter use independent game history and cannot
advance course mastery. Course-specific required shooter practice remains part of
the lesson path.

`AccountAndGuestProgress` routes verified integer user IDs to Neon, and server-issued
signed-session guest IDs to temporary storage. Guests use shared Redis in production
and bounded process memory locally (six-hour rolling expiry). A first-time account
can import trial history for a language; existing account progress is never replaced.
All guest and account mutations use the same CSRF and Origin checks.

All 40 curriculum IDs map to ten distinct concepts with translations for es/fr/tl.
The shared catalog contains 260 concepts per language. Newly added abstract concepts
have no invented image or audio asset; the UI provides a text prompt instead.
Practice and final question banks are checked for all 120 language-level combinations.

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
