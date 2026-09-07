"""Current collection progress timestamps, separate from polling and claim renewal."""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0003"
down_revision = "20260907_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("collections", sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("collections", "last_progress_at")
