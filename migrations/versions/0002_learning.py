"""Add account-owned learning storage without modifying existing accounts."""
from alembic import op
import sqlalchemy as sa
from learning.storage.sqlalchemy_store import define_tables

revision = "0002_learning"
down_revision = "0001_durable_auth_billing"
branch_labels = None
depends_on = None


def upgrade():
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))
    content, progress = define_tables(metadata)
    content.create(op.get_bind(), checkfirst=True)
    progress.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("Restore a reviewed backup to avoid deleting learning progress.")
