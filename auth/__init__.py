# /auth/__init__.py

from flask_talisman import Talisman

from .config import JWT_SECRET


def init_auth(app):
    """
    Connect authentication to an existing Flask application.

    Call once after creating the Flask app.
    """
    from .routes import auth_bp
    from .extensions import limiter

    # Preserve an existing Flask session secret.
    # Auth JWTs use JWT_SECRET directly in routes.py.
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = JWT_SECRET

    # Preserve the existing security headers.
    app.extensions["security_headers"] = Talisman(
        app,
        force_https=app.config["APP_ENV"] == "production",
        session_cookie_secure=app.config.get("SESSION_COOKIE_SECURE", True),
        frame_options="DENY",
        x_xss_protection=True,
        x_content_type_options="nosniff",
        content_security_policy={
            "default-src": "'self'",
            "script-src": "'self'",
            "style-src": "'self'",
            "img-src": ["'self'", "data:"],
        },
    )

    # Activate the existing request limits.
    limiter.init_app(app)

    # Connect all authentication routes.
    # No prefix keeps URLs such as /login unchanged.
    app.register_blueprint(auth_bp)

    @app.context_processor
    def csrf_context():
        from .security import csrf_token
        return {"auth_csrf": csrf_token}
