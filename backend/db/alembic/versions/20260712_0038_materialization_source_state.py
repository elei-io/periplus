"""fence materializations while a source view changes

Revision ID: 20260712_0038
Revises: 20260712_0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0038"
down_revision: str | None = "20260712_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalogue_materializations",
        sa.Column(
            "source_state",
            sa.Text(),
            nullable=False,
            server_default="current",
        ),
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_source_state",
        "catalogue_materializations",
        "source_state IN ('current', 'source_changing', 'source_changed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_catalogue_materializations_source_state",
        "catalogue_materializations",
        type_="check",
    )
    op.drop_column("catalogue_materializations", "source_state")
