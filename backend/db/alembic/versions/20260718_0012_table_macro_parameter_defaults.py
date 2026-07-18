"""table macro parameter defaults

Revision ID: 20260718_0012
Revises: 20260718_0011
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260718_0012"
down_revision: str | None = "20260718_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalogue_table_macros",
        sa.Column(
            "parameter_defaults",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.alter_column(
        "catalogue_table_macros",
        "parameter_defaults",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("catalogue_table_macros", "parameter_defaults")
