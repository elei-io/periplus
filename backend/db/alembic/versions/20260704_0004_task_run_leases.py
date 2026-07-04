"""task run leases

Revision ID: 20260704_0004
Revises: 20260704_0003
Create Date: 2026-07-04 22:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260704_0004"
down_revision: str | None = "20260704_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("leased_by", sa.Text(), nullable=True))
    op.add_column("task_runs", sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_runs", sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_task_runs_lease", "task_runs", ["leased_until"])
    op.create_index(
        "uq_task_runs_one_active_per_task",
        "task_runs",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_task_runs_one_active_per_task", table_name="task_runs")
    op.drop_index("ix_task_runs_lease", table_name="task_runs")
    op.drop_column("task_runs", "leased_until")
    op.drop_column("task_runs", "leased_at")
    op.drop_column("task_runs", "leased_by")
