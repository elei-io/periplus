"""add lightweight DuckLake view references

Revision ID: 20260711_0030
Revises: 20260711_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_0030"
down_revision: str | None = "20260711_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalogue_view_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ducklake_view_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("view_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ducklake_view_uuid"),
        sa.UniqueConstraint("schema_name", "view_name", name="uq_catalogue_view_reference_name"),
    )
    op.create_index("ix_catalogue_view_references_archived_at", "catalogue_view_references", ["archived_at"])


def downgrade() -> None:
    raise RuntimeError("Catalogue view references are a direct greenfield contract and are not downgraded.")
