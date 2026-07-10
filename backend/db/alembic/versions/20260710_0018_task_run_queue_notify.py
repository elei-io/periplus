"""Notify workers when task runs enter the queued state."""

from alembic import op

revision = "20260710_0018"
down_revision = "20260710_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION atlas_notify_task_run_queued() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM pg_notify('atlas_task_runs_queued', NEW.id::text);
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_runs_notify_queued_insert
        AFTER INSERT ON task_runs
        FOR EACH ROW
        WHEN (NEW.status = 'queued')
        EXECUTE FUNCTION atlas_notify_task_run_queued()
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_runs_notify_queued_update
        AFTER UPDATE OF status ON task_runs
        FOR EACH ROW
        WHEN (NEW.status = 'queued' AND OLD.status IS DISTINCT FROM NEW.status)
        EXECUTE FUNCTION atlas_notify_task_run_queued()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER task_runs_notify_queued_update ON task_runs")
    op.execute("DROP TRIGGER task_runs_notify_queued_insert ON task_runs")
    op.execute("DROP FUNCTION atlas_notify_task_run_queued()")
