"""add versioned catalogue queries and view provenance

Revision ID: 20260711_0031
Revises: 20260711_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_0031"
down_revision: str | None = "20260711_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalogue_queries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("current_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalogue_queries_archived_at", "catalogue_queries", ["archived_at"])
    op.create_table(
        "catalogue_query_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("sql_hash", sa.Text(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["query_id"], ["catalogue_queries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("query_id", "revision", name="uq_catalogue_query_revision_number"),
    )
    op.create_index("ix_catalogue_query_revisions_query_id", "catalogue_query_revisions", ["query_id"])
    op.create_foreign_key("fk_catalogue_queries_current_revision", "catalogue_queries", "catalogue_query_revisions", ["current_revision_id"], ["id"])
    op.add_column("catalogue_view_references", sa.Column("created_from_query_revision_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_catalogue_view_created_from_query_revision", "catalogue_view_references", "catalogue_query_revisions", ["created_from_query_revision_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    raise RuntimeError("Saved query revisions are an immutable greenfield contract and are not downgraded.")
