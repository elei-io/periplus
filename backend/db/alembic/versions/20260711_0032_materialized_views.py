"""add managed materialized views

Revision ID: 20260711_0032
Revises: 20260711_0031
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
revision: str = "20260711_0032"
down_revision: str | None = "20260711_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
def upgrade() -> None:
    op.create_table("materialized_views", sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("name", sa.Text(), nullable=False), sa.Column("display_name", sa.Text(), nullable=False), sa.Column("description", sa.Text(), nullable=True), sa.Column("query_revision_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("ducklake_table_uuid", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["query_revision_id"], ["catalogue_query_revisions.id"]), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("name"))
    op.create_index("ix_materialized_views_archived_at", "materialized_views", ["archived_at"])
def downgrade() -> None:
    raise RuntimeError("Materialized views are a direct greenfield contract and are not downgraded.")
