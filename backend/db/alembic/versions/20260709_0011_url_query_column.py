"""url query column

Revision ID: 20260709_0011
Revises: 20260709_0010
Create Date: 2026-07-09 01:11:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260709_0011"
down_revision: str | None = "20260709_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("urls", sa.Column("query", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE urls
        SET query = NULLIF(split_part(normalized_url, '?', 2), '')
        WHERE normalized_url LIKE '%?%'
        """
    )


def downgrade() -> None:
    op.drop_column("urls", "query")
