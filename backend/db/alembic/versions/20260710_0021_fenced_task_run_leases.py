"""fenced task run leases

Revision ID: 20260710_0021
Revises: 20260710_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260710_0021"
down_revision: str | None = "20260710_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Greenfield hard cut: in-flight work from the old ownership model is requeued.
    op.execute(
        """
        UPDATE task_runs
        SET status = 'queued', started_at = NULL, retry_at = now()
        WHERE status = 'running'
        """
    )
    op.create_table(
        "task_run_leases",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.UniqueConstraint("lease_token"),
    )
    op.create_index("ix_task_run_leases_expires_at", "task_run_leases", ["expires_at"])
    op.create_index("ix_task_run_leases_worker_id", "task_run_leases", ["worker_id"])
    op.drop_index("ix_task_runs_lease", table_name="task_runs")
    for column in ("last_heartbeat_at", "leased_until", "leased_at", "leased_by"):
        op.drop_column("task_runs", column)


def downgrade() -> None:
    op.add_column("task_runs", sa.Column("leased_by", sa.Text(), nullable=True))
    op.add_column("task_runs", sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_runs", sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_runs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_task_runs_lease", "task_runs", ["leased_until"])
    op.drop_index("ix_task_run_leases_worker_id", table_name="task_run_leases")
    op.drop_index("ix_task_run_leases_expires_at", table_name="task_run_leases")
    op.drop_table("task_run_leases")
