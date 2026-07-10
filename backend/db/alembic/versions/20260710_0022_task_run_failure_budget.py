"""separate task-run attempt generation from failure budget

Revision ID: 20260710_0022
Revises: 20260710_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_0022"
down_revision: str | None = "20260710_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_runs",
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("task_runs", "failed_attempts")
