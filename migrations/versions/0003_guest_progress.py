"""Share temporary guest rounds across application workers and deployments."""
from alembic import op
import sqlalchemy as sa
from learning.storage.guest_progress import define_guest_table

revision = '0003_guest_progress'
down_revision = '0002_learning'
branch_labels = None
depends_on = None


def upgrade():
    define_guest_table(sa.MetaData()).create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table('learning_guest_progress')
