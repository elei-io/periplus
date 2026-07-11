"""add incremental materialization state

Revision ID: 20260711_0033
Revises: 20260711_0032
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
revision: str = "20260711_0033"
down_revision: str | None = "20260711_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
def upgrade() -> None:
    op.add_column("materialized_views", sa.Column("definition_revision_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("materialized_views", sa.Column("refresh_mode", sa.Text(), server_default="full", nullable=False))
    op.add_column("materialized_views", sa.Column("scope_kind", sa.Text(), nullable=True))
    op.add_column("materialized_views", sa.Column("scope_column", sa.Text(), nullable=True))
    op.add_column("materialized_views", sa.Column("activation_snapshot", sa.BigInteger(), nullable=True))
    op.add_column("materialized_views", sa.Column("live_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("materialized_views", sa.Column("backfill_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("materialized_views", sa.Column("backfill_scopes_per_minute", sa.Integer(), server_default="60", nullable=False))
    op.execute("UPDATE materialized_views SET definition_revision_id = gen_random_uuid() WHERE definition_revision_id IS NULL")
    op.alter_column("materialized_views", "definition_revision_id", nullable=False)
def downgrade() -> None:
    raise RuntimeError("Incremental materialization state is not downgraded.")
