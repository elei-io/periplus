"""archive tasks

Revision ID: 20260704_0002
Revises: 20260704_0001
Create Date: 2026-07-04 19:35:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260704_0002"
down_revision: str | None = "20260704_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("fk_tasks_disabled_by_effect_run_id_effect_runs", "tasks", type_="foreignkey")
    op.drop_index("ix_tasks_enabled", table_name="tasks")

    op.alter_column("tasks", "disabled_by_effect_run_id", new_column_name="archived_by_effect_run_id")
    op.alter_column("tasks", "disabled_at", new_column_name="archived_at")
    op.add_column("tasks", sa.Column("archived_reason", sa.Text(), nullable=True))

    op.execute("UPDATE tasks SET archived_at = now(), archived_reason = 'legacy_disabled' WHERE enabled = false AND archived_at IS NULL")
    op.drop_column("tasks", "enabled")

    op.create_index("ix_tasks_archived_at", "tasks", ["archived_at"])
    op.create_foreign_key(
        "fk_tasks_archived_by_effect_run_id_effect_runs",
        "tasks",
        "effect_runs",
        ["archived_by_effect_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_tasks_archived_by_effect_run_id_effect_runs", "tasks", type_="foreignkey")
    op.drop_index("ix_tasks_archived_at", table_name="tasks")

    op.add_column("tasks", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute("UPDATE tasks SET enabled = false WHERE archived_at IS NOT NULL")
    op.alter_column("tasks", "enabled", server_default=None)

    op.drop_column("tasks", "archived_reason")
    op.alter_column("tasks", "archived_at", new_column_name="disabled_at")
    op.alter_column("tasks", "archived_by_effect_run_id", new_column_name="disabled_by_effect_run_id")

    op.create_index("ix_tasks_enabled", "tasks", ["enabled"])
    op.create_foreign_key(
        "fk_tasks_disabled_by_effect_run_id_effect_runs",
        "tasks",
        "effect_runs",
        ["disabled_by_effect_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
