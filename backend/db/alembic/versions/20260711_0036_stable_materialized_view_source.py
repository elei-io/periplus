"""use stable Atlas references for materialized view sources

Revision ID: 20260711_0036
Revises: 20260711_0035
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260711_0036"
down_revision: str | None = "20260711_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "materialized_views",
        "source_view_uuid",
        new_column_name="source_view_reference_id",
    )
    op.create_foreign_key(
        "fk_materialized_views_source_view_reference",
        "materialized_views",
        "catalogue_view_references",
        ["source_view_reference_id"],
        ["id"],
    )


def downgrade() -> None:
    raise RuntimeError("Stable materialized-view sources are not downgraded.")
