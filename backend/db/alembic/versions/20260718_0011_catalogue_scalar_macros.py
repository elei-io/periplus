"""catalogue scalar macros

Revision ID: 20260718_0011
Revises: 20260717_0010
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260718_0011"
down_revision: str | None = "20260717_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalogue_scalar_macros",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("macro_name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("definition_revision_id", sa.UUID(), nullable=False),
        sa.Column("fixture_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_catalogue_scalar_macro_slug"),
        sa.UniqueConstraint(
            "schema_name", "macro_name", name="uq_catalogue_scalar_macro_name"
        ),
        sa.UniqueConstraint(
            "fixture_path", name="uq_catalogue_scalar_macros_fixture_path"
        ),
    )
    op.create_index(
        "ix_catalogue_scalar_macros_updated_at",
        "catalogue_scalar_macros",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_catalogue_scalar_macros_updated_at",
        table_name="catalogue_scalar_macros",
    )
    op.drop_table("catalogue_scalar_macros")
