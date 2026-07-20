"""add URL materialization scope

Revision ID: 20260720_0013
Revises: 20260718_0012
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260720_0013"
down_revision: str | None = "20260718_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_catalogue_materializations_scope",
        "catalogue_materializations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_scope",
        "catalogue_materializations",
        "scope_kind IN ('url', 'document', 'crawl')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_catalogue_materializations_scope",
        "catalogue_materializations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_scope",
        "catalogue_materializations",
        "scope_kind IN ('document', 'crawl')",
    )
