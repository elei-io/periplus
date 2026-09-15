"""Keep final customer accounting when execution payloads are pruned."""
from alembic import op
import sqlalchemy as sa

revision = "20260915_0020"
down_revision = "20260915_0019"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("supplied_pages", "failed_pages", "shared_pages", "reused_pages"):
        op.add_column("collections", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.add_column("collections", sa.Column("execution_pruned_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("collections", "execution_pruned_at")
    for name in ("supplied_pages", "failed_pages", "shared_pages", "reused_pages"):
        op.drop_column("collections", name)
