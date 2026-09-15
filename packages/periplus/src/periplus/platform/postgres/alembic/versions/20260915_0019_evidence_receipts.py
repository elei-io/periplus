"""Replace lake snapshot acknowledgements with evidence commit times.

The ClickHouse experiment starts from disposable control state. There is no
translation from a DuckLake snapshot to a ClickHouse evidence receipt.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_0019"
down_revision = "20260914_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_frontier_ingestion_receipts", table_name="frontier_outbox")
    op.drop_column("frontier_outbox", "committed_snapshot")
    op.drop_column("frontier_acquisitions", "evidence_snapshot")
    op.add_column("frontier_acquisitions", sa.Column("evidence_committed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_frontier_ingestion_receipts", "frontier_outbox",
                    ["kind", "committed_at", "next_receipt_at", "published_at"])


def downgrade():
    raise RuntimeError("Reset disposable experiment state to return to the DuckLake contract")
