"""add frozen task and crawl-policy revisions

Revision ID: 20260711_0026
Revises: 20260711_0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0026"
down_revision: str | None = "20260711_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "task_runs",
        sa.Column("task_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "crawl_policies",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("crawl_policies", "revision")
    op.drop_column("task_runs", "task_revision")
    op.drop_column("tasks", "revision")
