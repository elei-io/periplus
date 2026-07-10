"""Add concurrent-worker capacity and global crawl permits."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260710_0019"
down_revision = "20260710_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("worker_heartbeats_current_run_id_fkey", "worker_heartbeats", type_="foreignkey")
    op.drop_column("worker_heartbeats", "current_run_id")
    op.add_column("worker_heartbeats", sa.Column("capacity", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(
        "worker_heartbeats", sa.Column("active_run_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_table(
        "crawl_permits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("permit_key", sa.Text(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("holder_worker_id", sa.Text(), nullable=False),
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["crawl_policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["url_id"], ["urls.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("permit_key", "slot", name="uq_crawl_permits_key_slot"),
    )
    op.create_index("ix_crawl_permits_lease", "crawl_permits", ["leased_until"])
    op.create_index("ix_crawl_permits_task_run", "crawl_permits", ["task_run_id"])


def downgrade() -> None:
    op.drop_table("crawl_permits")
    op.drop_column("worker_heartbeats", "active_run_count")
    op.drop_column("worker_heartbeats", "capacity")
    op.add_column(
        "worker_heartbeats",
        sa.Column("current_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "worker_heartbeats_current_run_id_fkey",
        "worker_heartbeats",
        "task_runs",
        ["current_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
