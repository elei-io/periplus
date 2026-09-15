"""Remove the superseded receipt polling protocol."""

from alembic import op

revision = "20260916_0024"
down_revision = "20260916_0023"
branch_labels = depends_on = None


def upgrade():
    op.drop_index("ix_frontier_ingestion_receipts", table_name="frontier_outbox")
    op.drop_constraint("ck_frontier_receipt_checks", "frontier_outbox", type_="check")
    op.drop_column("frontier_outbox", "next_receipt_at")
    op.drop_column("frontier_outbox", "receipt_checks")


def downgrade():
    raise RuntimeError("Restore the backup to return to the superseded protocol")
