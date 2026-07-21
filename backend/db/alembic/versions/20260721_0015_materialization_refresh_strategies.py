"""add explicit materialization refresh strategies

Revision ID: 20260721_0015
Revises: 20260720_0014
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0015"
down_revision: str | None = "20260720_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Refresh behavior is part of an immutable incarnation. Atlas is
    # greenfield, so definitions created under the whole-table-only contract
    # must be dematerialized (or disposable state reset), not guessed at.
    active = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM catalogue_materializations "
            "WHERE archived_at IS NULL"
        )
    ).scalar_one()
    if active:
        raise RuntimeError(
            "Dematerialize or reset the disposable Atlas catalogue before "
            "installing materialization refresh strategies."
        )
    # Archived incarnations own no DuckLake table or NATS consumer. Their old
    # whole-table-only definition is not meaningful under the new immutable
    # refresh contract, so discard that operational history instead of
    # inventing a strategy for it.
    op.execute(
        sa.text(
            "DELETE FROM catalogue_materializations WHERE archived_at IS NOT NULL"
        )
    )
    op.drop_column("catalogue_materializations", "source_schema_version")
    op.add_column(
        "catalogue_materializations",
        sa.Column("refresh_strategy", sa.Text(), nullable=False),
    )
    op.add_column(
        "catalogue_materializations",
        sa.Column("key_columns", sa.JSON(), nullable=False),
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_refresh_strategy",
        "catalogue_materializations",
        "refresh_strategy IN ('keyed', 'append', 'full')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_catalogue_materializations_refresh_strategy",
        "catalogue_materializations",
        type_="check",
    )
    op.drop_column("catalogue_materializations", "key_columns")
    op.drop_column("catalogue_materializations", "refresh_strategy")
    op.add_column(
        "catalogue_materializations",
        sa.Column("source_schema_version", sa.Integer(), nullable=True),
    )
