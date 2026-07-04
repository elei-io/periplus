"""initial

Revision ID: 20260704_0001
Revises:
Create Date: 2026-07-04 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260704_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("primitive", sa.Text(), nullable=False),
        sa.Column("input_json", postgresql.JSONB(), nullable=False),
        sa.Column("schedule_json", postgresql.JSONB(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_effect_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_effect_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("disabled_by_effect_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_tasks_dedupe_key"),
    )
    op.create_index("ix_tasks_enabled", "tasks", ["enabled"])
    op.create_index("ix_tasks_next_run_at", "tasks", ["next_run_at"])
    op.create_index("ix_tasks_primitive", "tasks", ["primitive"])

    op.create_table(
        "task_effects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effect_type", sa.Text(), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_effects_task_id_tasks", ondelete="CASCADE"),
    )
    op.create_index("ix_task_effects_enabled", "task_effects", ["enabled"])
    op.create_index("ix_task_effects_effect_type", "task_effects", ["effect_type"])
    op.create_index("ix_task_effects_task_id", "task_effects", ["task_id"])

    op.create_table(
        "task_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trigger_kind", sa.Text(), nullable=False),
        sa.Column("triggered_by_effect_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_json", postgresql.JSONB(), nullable=False),
        sa.Column("output_json", postgresql.JSONB(), nullable=True),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_runs_task_id_tasks", ondelete="CASCADE"),
    )
    op.create_index("ix_task_runs_queued_at", "task_runs", ["queued_at"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])
    op.create_index("ix_task_runs_task_id", "task_runs", ["task_id"])
    op.create_index("ix_task_runs_trigger_kind", "task_runs", ["trigger_kind"])

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], name="fk_artifacts_task_run_id_task_runs", ondelete="CASCADE"),
    )
    op.create_index("ix_artifacts_cache_key", "artifacts", ["cache_key"])
    op.create_index("ix_artifacts_kind", "artifacts", ["kind"])
    op.create_index("ix_artifacts_task_run_id", "artifacts", ["task_run_id"])

    op.create_table(
        "effect_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("effect_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("target_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_json", postgresql.JSONB(), nullable=False),
        sa.Column("output_json", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["effect_id"], ["task_effects.id"], name="fk_effect_runs_effect_id_task_effects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_run_id"], ["task_runs.id"], name="fk_effect_runs_source_run_id_task_runs", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_task_id"], ["tasks.id"], name="fk_effect_runs_target_task_id_tasks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_run_id"], ["task_runs.id"], name="fk_effect_runs_target_run_id_task_runs", ondelete="SET NULL"),
    )
    op.create_index("ix_effect_runs_effect_id", "effect_runs", ["effect_id"])
    op.create_index("ix_effect_runs_operation", "effect_runs", ["operation"])
    op.create_index("ix_effect_runs_source_run_id", "effect_runs", ["source_run_id"])
    op.create_index("ix_effect_runs_status", "effect_runs", ["status"])
    op.create_index("ix_effect_runs_target_run_id", "effect_runs", ["target_run_id"])
    op.create_index("ix_effect_runs_target_task_id", "effect_runs", ["target_task_id"])

    op.create_foreign_key(
        "fk_tasks_created_by_effect_run_id_effect_runs",
        "tasks",
        "effect_runs",
        ["created_by_effect_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_tasks_updated_by_effect_run_id_effect_runs",
        "tasks",
        "effect_runs",
        ["updated_by_effect_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_tasks_disabled_by_effect_run_id_effect_runs",
        "tasks",
        "effect_runs",
        ["disabled_by_effect_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_task_runs_triggered_by_effect_run_id_effect_runs",
        "task_runs",
        "effect_runs",
        ["triggered_by_effect_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_task_runs_triggered_by_effect_run_id_effect_runs", "task_runs", type_="foreignkey")
    op.drop_constraint("fk_tasks_disabled_by_effect_run_id_effect_runs", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_updated_by_effect_run_id_effect_runs", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_created_by_effect_run_id_effect_runs", "tasks", type_="foreignkey")

    op.drop_table("effect_runs")
    op.drop_table("artifacts")
    op.drop_table("task_runs")
    op.drop_table("task_effects")
    op.drop_table("tasks")
