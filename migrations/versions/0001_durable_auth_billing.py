"""Add revocable auth sessions, indexed action tokens and durable billing recovery.

Existing starter tables are adopted, not dropped. Take a backup before upgrading.
"""

from alembic import op
import sqlalchemy as sa
from migrations.schema_v1 import metadata

revision = "0001_durable_auth_billing"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "users" in tables:
        duplicate = bind.execute(sa.text("SELECT lower(trim(email)) FROM users GROUP BY lower(trim(email)) HAVING count(*) > 1 LIMIT 1")).first()
        if duplicate:
            raise ValueError("Duplicate normalized email identities exist. Review and resolve duplicates before migration; accounts were not merged.")
        bind.execute(sa.text("UPDATE users SET email = lower(trim(email)), verification_token = NULL, verification_expiry = NULL, reset_token = NULL, reset_expiry = NULL"))
    # PostgreSQL validates foreign-key targets at CREATE TABLE time.
    metadata.tables["users"].create(bind, checkfirst=True)
    metadata.tables["auth_sessions"].create(bind, checkfirst=True)
    if "refresh_tokens" in tables:
        columns = {column["name"] for column in inspector.get_columns("refresh_tokens")}
        if "session_id" not in columns:
            with op.batch_alter_table("refresh_tokens") as batch:
                batch.add_column(sa.Column("session_id", sa.String(64), nullable=True))
                batch.create_foreign_key("fk_refresh_tokens_session_id", "auth_sessions", ["session_id"], ["id"])
                batch.create_index("ix_refresh_tokens_session_id", ["session_id"])
    metadata.create_all(bind)
    indexes = {index['name'] for index in sa.inspect(bind).get_indexes('users')}
    if 'uq_users_email_normalized' not in indexes:
        op.create_index('uq_users_email_normalized', 'users', [sa.text('lower(email)')], unique=True)


def downgrade():
    raise RuntimeError("This adoption migration cannot safely infer which tables predated it. Restore a reviewed backup rather than deleting user data.")
