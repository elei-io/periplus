"""remove obsolete manifest artifacts

Revision ID: 20260708_0006
Revises: 20260708_0005
Create Date: 2026-07-08 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260708_0006"
down_revision: str | None = "20260708_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM artifacts WHERE kind IN ('atlas.json', 'result.json', 'crawl.json')")


def downgrade() -> None:
    pass
