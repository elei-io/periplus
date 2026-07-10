"""Add task-run lifecycle and worker heartbeat state."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260710_0017"
down_revision = "20260709_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_runs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_runs", sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_runs", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_runs", sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("task_runs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.Text(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stopping", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["current_run_id"], ["task_runs.id"], ondelete="SET NULL"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    for column in (
        "max_attempts", "attempt", "retry_at", "last_heartbeat_at", "cancelled_at", "cancellation_requested_at"
    ):
        op.drop_column("task_runs", column)
