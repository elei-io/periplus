"""batch incremental materialization bootstrap

Revision ID: 20260724_0018
Revises: 20260724_0017
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260724_0018"
down_revision: str | None = "20260724_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_catalogue_materializations_observed_state",
        "catalogue_materializations",
        type_="check",
    )
    op.add_column(
        "catalogue_materializations",
        sa.Column("bootstrap_partition_count", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "catalogue_materializations",
        sa.Column("bootstrap_partition_cursor", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_observed_state",
        "catalogue_materializations",
        "observed_state IN ('creating', 'backfilling', 'live', 'paused', "
        "'deleting', 'blocked_schema', 'failed')",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_bootstrap_progress",
        "catalogue_materializations",
        "(bootstrap_partition_count IS NULL AND "
        "bootstrap_partition_cursor IS NULL) OR "
        "(bootstrap_partition_count >= 1 AND "
        "bootstrap_partition_cursor >= 0 AND "
        "bootstrap_partition_cursor <= bootstrap_partition_count)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_catalogue_materializations_bootstrap_progress",
        "catalogue_materializations",
        type_="check",
    )
    op.drop_constraint(
        "ck_catalogue_materializations_observed_state",
        "catalogue_materializations",
        type_="check",
    )
    op.drop_column(
        "catalogue_materializations", "bootstrap_partition_cursor"
    )
    op.drop_column(
        "catalogue_materializations", "bootstrap_partition_count"
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_observed_state",
        "catalogue_materializations",
        "observed_state IN ('creating', 'live', 'paused', 'deleting', "
        "'blocked_schema', 'failed')",
    )
