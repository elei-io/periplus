"""catalogue table macros

Revision ID: 20260714_0005
Revises: 20260712_0004
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260714_0005"
down_revision: str | None = "20260712_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalogue_table_macros",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("macro_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("definition_revision_id", sa.UUID(), nullable=False),
        sa.Column("created_from_query_revision_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_from_query_revision_id"],
            ["catalogue_query_revisions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schema_name", "macro_name", name="uq_catalogue_table_macro_name"
        ),
    )
    op.create_index(
        "ix_catalogue_table_macros_updated_at",
        "catalogue_table_macros",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_catalogue_table_macros_updated_at", table_name="catalogue_table_macros"
    )
    op.drop_table("catalogue_table_macros")
