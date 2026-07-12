"""name the durable-table removal lifecycle dematerialization

Revision ID: 20260712_0039
Revises: 20260712_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0039"
down_revision: str | None = "20260712_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "catalogue_materializations",
        "deletion_requested_at",
        new_column_name="dematerialization_requested_at",
    )
    op.drop_constraint(
        "catalogue_materializations_name_key",
        "catalogue_materializations",
        type_="unique",
    )
    op.create_index(
        "uq_catalogue_materializations_active_name",
        "catalogue_materializations",
        ["name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Dematerialization is a direct greenfield contract and is not downgraded."
    )
