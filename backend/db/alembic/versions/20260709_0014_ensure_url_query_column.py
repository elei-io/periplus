"""ensure url query column

Revision ID: 20260709_0014
Revises: 20260709_0013
Create Date: 2026-07-09 09:05:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260709_0014"
down_revision: str | None = "20260709_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE urls ADD COLUMN IF NOT EXISTS query TEXT")
    op.execute(
        """
        UPDATE urls
        SET query = NULLIF(split_part(normalized_url, '?', 2), '')
        WHERE query IS NULL
          AND normalized_url LIKE '%?%'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE urls DROP COLUMN IF EXISTS query")
