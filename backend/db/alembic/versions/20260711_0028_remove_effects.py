"""remove unused task effects

Revision ID: 20260711_0028
Revises: 20260711_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_0028"
down_revision: str | None = "20260711_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("fk_task_runs_triggered_by_effect_run_id_effect_runs", "task_runs", type_="foreignkey")
    op.drop_constraint("fk_tasks_created_by_effect_run_id_effect_runs", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_updated_by_effect_run_id_effect_runs", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_archived_by_effect_run_id_effect_runs", "tasks", type_="foreignkey")
    op.drop_table("effect_runs")
    op.drop_table("task_effects")
    op.drop_column("task_runs", "triggered_by_effect_run_id")
    op.drop_column("tasks", "created_by_effect_run_id")
    op.drop_column("tasks", "updated_by_effect_run_id")
    op.drop_column("tasks", "archived_by_effect_run_id")


def downgrade() -> None:
    op.add_column("tasks", sa.Column("created_by_effect_run_id", postgresql.UUID(as_uuid=True)))
    op.add_column("tasks", sa.Column("updated_by_effect_run_id", postgresql.UUID(as_uuid=True)))
    op.add_column("tasks", sa.Column("archived_by_effect_run_id", postgresql.UUID(as_uuid=True)))
    op.add_column("task_runs", sa.Column("triggered_by_effect_run_id", postgresql.UUID(as_uuid=True)))
    raise RuntimeError("Task effects cannot be restored without recreating their deleted data.")
