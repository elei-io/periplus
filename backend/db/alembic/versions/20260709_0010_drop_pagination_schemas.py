"""drop pagination schemas

Revision ID: 20260709_0010
Revises: 20260709_0009
Create Date: 2026-07-09 01:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260709_0010"
down_revision: str | None = "20260709_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("pagination_schemas")


def downgrade() -> None:
    raise NotImplementedError("pagination_schemas was removed from the greenfield model")
