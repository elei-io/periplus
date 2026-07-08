"""drop artifact extracted column

Revision ID: 20260708_0007
Revises: 20260708_0006
Create Date: 2026-07-08 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260708_0007"
down_revision: str | None = "20260708_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("artifacts", "extracted")


def downgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column("extracted", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.alter_column("artifacts", "extracted", server_default=None)
