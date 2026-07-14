"""catalogue fixture provenance

Revision ID: 20260714_0006
Revises: 20260714_0005
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260714_0006"
down_revision: str | None = "20260714_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in (
        "catalogue_queries",
        "catalogue_view_references",
        "catalogue_table_macros",
    ):
        op.add_column(table, sa.Column("fixture_path", sa.Text(), nullable=True))
        op.create_unique_constraint(
            f"uq_{table}_fixture_path", table, ["fixture_path"]
        )


def downgrade() -> None:
    for table in (
        "catalogue_table_macros",
        "catalogue_view_references",
        "catalogue_queries",
    ):
        op.drop_constraint(f"uq_{table}_fixture_path", table, type_="unique")
        op.drop_column(table, "fixture_path")
