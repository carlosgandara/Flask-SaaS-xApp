"""Optional billing registration. Setup never calls providers or changes schema."""

import click
from .config import load_config


def init_billing(app):
    load_config(app)
    from .providers import build_providers
    from .routes import billing_bp
    from .security import configure_overlay_policy
    from .worker import init_worker_cli
    app.extensions["billing_providers"] = build_providers(app.config) if app.config["BILLING_ENABLED"] else {}
    app.register_blueprint(billing_bp)
    configure_overlay_policy(app)
    init_worker_cli(app)

    @app.cli.command("billing-init-db")
    def init_db():
        """Compatibility alias for db-upgrade; applies versioned schema changes."""
        from auth.db import engine
        from database_migrations import upgrade_database
        upgrade_database(engine)
        click.echo("Database schema is up to date (existing data preserved).")

    @app.cli.command("billing-check")
    def billing_check():
        """Read-only provider credential/store/price checks. Never creates checkout."""
        if not app.config["BILLING_ENABLED"]:
            raise click.ClickException("Enable and configure billing first")
        from .errors import BillingError
        from .service import provider_for
        try:
            for name, provider in app.extensions["billing_providers"].items():
                if hasattr(provider, 'check_store'):
                    provider.check_store()
            for key, plan in app.config["BILLING_PLANS"].items():
                provider_for(plan["provider"]).validate_plan(plan["product_id"])
                click.echo(f"Plan {key}: provider configuration is valid.")
        except BillingError as exc:
            raise click.ClickException(str(exc)) from None
