"""bounded rebuild catch-up progress

Revision ID: 20260727_0003
Revises: 20260727_0002
Create Date: 2026-07-27 05:50:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260727_0003"
down_revision: str | None = "20260727_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "materialization_runs",
        sa.Column("catchup_snapshot", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "materialization_runs",
        sa.Column(
            "catchup_target_snapshot", sa.BigInteger(), nullable=True
        ),
    )
    op.add_column(
        "materialization_runs",
        sa.Column(
            "catchup_stage",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "materialization_runs",
        sa.Column(
            "catchup_cursors",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE materialization_runs "
        "SET catchup_snapshot = source_snapshot"
    )
    op.alter_column(
        "materialization_runs", "catchup_snapshot", nullable=False
    )
    op.alter_column(
        "materialization_runs", "catchup_stage", server_default=None
    )
    op.alter_column(
        "materialization_runs", "catchup_cursors", server_default=None
    )


def downgrade() -> None:
    op.drop_column("materialization_runs", "catchup_cursors")
    op.drop_column("materialization_runs", "catchup_stage")
    op.drop_column("materialization_runs", "catchup_target_snapshot")
    op.drop_column("materialization_runs", "catchup_snapshot")
