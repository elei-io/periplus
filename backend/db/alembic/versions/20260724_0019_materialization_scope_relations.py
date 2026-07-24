"""add explicit dependent-scan scope relations

Revision ID: 20260724_0019
Revises: 20260724_0018
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260724_0019"
down_revision: str | None = "20260724_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalogue_materializations",
        sa.Column(
            "scope_relations",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column(
        "catalogue_materializations",
        "scope_relations",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("catalogue_materializations", "scope_relations")
