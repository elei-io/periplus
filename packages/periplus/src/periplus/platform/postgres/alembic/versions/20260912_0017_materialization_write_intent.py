"""Durable first-publication intent for clean rebuild batches."""
from alembic import op
import sqlalchemy as sa

revision = "20260912_0017"
down_revision = "20260911_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("materialization_batches", sa.Column("write_intent_at", sa.DateTime(timezone=True), nullable=True))
    # Existing attempts may already have committed to the lake without a receipt.
    # They must never be treated as provably new after this online migration.
    op.execute("UPDATE materialization_batches SET write_intent_at = CURRENT_TIMESTAMP")


def downgrade():
    op.drop_column("materialization_batches", "write_intent_at")
