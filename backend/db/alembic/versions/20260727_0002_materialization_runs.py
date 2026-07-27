"""materialization maintenance runs

Revision ID: 20260727_0002
Revises: 20260726_0001
Create Date: 2026-07-27 02:30:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260727_0002"
down_revision: str | None = "20260726_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "materialization_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "requested_stages",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "stages",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "projector_versions",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("source_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("current_stage", sa.Integer(), nullable=False),
        sa.Column(
            "cursors",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "destinations",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("item_budget", sa.Integer(), nullable=False),
        sa.Column("byte_budget", sa.BigInteger(), nullable=False),
        sa.Column("source_items", sa.BigInteger(), nullable=False),
        sa.Column("source_bytes", sa.BigInteger(), nullable=False),
        sa.Column("output_rows", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "mode IN ('backfill', 'rebuild')",
            name="ck_materialization_runs_mode",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_materialization_runs_status",
        ),
        sa.CheckConstraint(
            "item_budget >= 1 AND byte_budget >= 1",
            name="ck_materialization_runs_budgets",
        ),
        sa.CheckConstraint(
            "source_items >= 0 AND source_bytes >= 0 AND output_rows >= 0",
            name="ck_materialization_runs_counts",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_materialization_runs_status"),
        "materialization_runs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_materialization_runs_status"),
        table_name="materialization_runs",
    )
    op.drop_table("materialization_runs")
