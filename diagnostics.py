"""Readiness checks and safe request references; no credential/payload logging."""

import secrets

import click
from flask import g, jsonify, request
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException


def schema_problems():
    from auth.db import Base, engine
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            missing.append(table.name)
        else:
            existing = {column['name'] for column in inspector.get_columns(table.name)}
            missing.extend(table.name + '.' + column.name for column in table.columns if column.name not in existing)
    if 'alembic_version' not in tables:
        missing.append('migration revision')
    else:
        with engine.connect() as connection:
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
        if revision != '0003_guest_progress':
            missing.append('migration revision')
    return missing


def init_diagnostics(app):
    @app.before_request
    def request_reference():
        g.request_id = secrets.token_hex(12)
        if app.config["APP_ENV"] == "production" and app.debug:
            return jsonify(error="Unsafe deployment configuration"), 503
        public_endpoint = (request.endpoint or '').startswith('site.') or request.endpoint in {'static', 'auth.static', 'auth.home'}
        if app.config["SCHEMA_CHECK"] and not app.extensions.get("schema_ready") and not public_endpoint:
            if schema_problems():
                return jsonify(error="Database schema needs an upgrade. Run flask --app app db-upgrade.", reference=g.request_id), 503
            app.extensions["schema_ready"] = True

    @app.after_request
    def safe_headers(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        if not (request.endpoint or "").endswith(".static"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.errorhandler(SQLAlchemyError)
    def database_failure(error):
        app.logger.error("database.unavailable type=%s endpoint=%s request_id=%s",
                         type(error).__name__, request.endpoint, getattr(g, "request_id", ""))
        return jsonify(error="Service temporarily unavailable", reference=getattr(g, "request_id", "")), 503

    @app.errorhandler(Exception)
    def unexpected_failure(error):
        if isinstance(error, HTTPException):
            return error
        app.logger.error("application.failure type=%s endpoint=%s request_id=%s",
                         type(error).__name__, request.endpoint, getattr(g, "request_id", ""))
        return jsonify(error="Unexpected service error", reference=getattr(g, "request_id", "")), 500

    @app.cli.command("app-check")
    def app_check():
        """Read-only configuration, schema, and rate-limit storage readiness check."""
        try:
            missing = schema_problems()
            if missing:
                raise click.ClickException("Schema upgrade required: " + ", ".join(missing) + ". Run db-upgrade.")
            from auth.extensions import limiter
            if not limiter.storage.check():
                raise click.ClickException("Rate-limit storage is unavailable")
        except SQLAlchemyError as exc:
            raise click.ClickException("Database connection failed (" + type(exc).__name__ + ")") from None
        click.echo("Configuration, database schema, and rate-limit storage are ready.")
