"""add materialized-view lifecycle and source fields

Revision ID: 20260711_0034
Revises: 20260711_0033
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_0034"
down_revision: str | None = "20260711_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("materialized_views", "query_revision_id", nullable=True)
    op.add_column(
        "materialized_views",
        sa.Column("source_view_uuid", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "materialized_views", sa.Column("partition_column", sa.Text(), nullable=True)
    )
    op.add_column(
        "materialized_views",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_materialized_views_one_source",
        "materialized_views",
        "(query_revision_id IS NOT NULL) <> (source_view_uuid IS NOT NULL)",
    )


def downgrade() -> None:
    raise RuntimeError("Materialized-view lifecycle state is not downgraded.")
