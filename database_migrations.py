"""Explicit schema upgrades; importing the application never changes the database."""

from pathlib import Path

import click
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError


def upgrade_database(engine):
    configuration = Config()
    configuration.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            from sqlalchemy import text
            connection.execute(text("SELECT pg_advisory_xact_lock(607220241)"))
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "head")


def init_migrations(app):
    @app.cli.command("db-upgrade")
    def db_upgrade():
        """Apply reviewed schema migrations to the configured database."""
        from auth.db import engine
        try:
            upgrade_database(engine)
        except SQLAlchemyError as exc:
            raise click.ClickException("Schema upgrade failed (" + type(exc).__name__ + "). Check database permissions and backups.") from None
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo("Database schema is up to date. Existing users and subscriptions were preserved.")
