"""move task execution state to NATS

Revision ID: 20260711_0029
Revises: 20260711_0028
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260711_0029"
down_revision: str | None = "20260711_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM task_runs WHERE status IN ('queued', 'running')) THEN
                RAISE EXCEPTION 'finish or cancel active Postgres task runs before migrating execution to NATS';
            END IF;
        END $$
        """
    )
    op.execute("DROP TABLE IF EXISTS crawl_permits CASCADE")
    op.execute("DROP TABLE IF EXISTS task_run_leases CASCADE")
    op.execute("DROP TABLE IF EXISTS worker_heartbeats CASCADE")
    op.execute("DROP TABLE IF EXISTS task_runs CASCADE")
    op.execute("DROP FUNCTION IF EXISTS atlas_notify_task_run_queued()")


def downgrade() -> None:
    raise RuntimeError("NATS task state cannot be reconstructed as Postgres rows.")
