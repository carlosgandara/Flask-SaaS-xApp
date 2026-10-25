# /app.py

import os

from flask import Flask

from auth import init_auth
from billing import init_billing
from configuration import configure_app
from diagnostics import init_diagnostics


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    )
    if config:
        app.config.update(config)
    configure_app(app)
    init_diagnostics(app)
    init_auth(app)
    init_billing(app)
    from integration import init_language_app
    init_language_app(app)
    from database_migrations import init_migrations
    init_migrations(app)
    return app

app = create_app()


if __name__ == "__main__":
    # Windows can allow two development servers to share the same address.
    # Keep one launcher per checkout so old code cannot serve some requests.
    if os.name == "nt":
        import hashlib
        import msvcrt
        from pathlib import Path
        import tempfile

        checkout = hashlib.sha256(str(Path(__file__).resolve()).encode()).hexdigest()[:16]
        server_lock = open(Path(tempfile.gettempdir()) / f"despacio-{checkout}.lock", "a+b")
        server_lock.seek(0)
        try:
            msvcrt.locking(server_lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("Despacio is already running. Open http://localhost:5000 instead of starting another server.")
    app.run()
