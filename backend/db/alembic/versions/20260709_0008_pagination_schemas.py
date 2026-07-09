"""pagination schemas

Revision ID: 20260709_0008
Revises: 20260708_0007
Create Date: 2026-07-09 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260709_0008"
down_revision: str | None = "20260708_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pagination_schemas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_key", sa.Text(), nullable=False, unique=True),
        sa.Column("match", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("expected_max_page_item_count", sa.Integer(), nullable=True),
        sa.Column("item_selector", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("generated_from_crawl_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_from_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_by_task_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("inputs_json", postgresql.JSONB(), nullable=False),
        sa.Column("validation_status", sa.Text(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_pagination_schemas_generated_from_crawl_id_crawls",
        "pagination_schemas",
        "crawls",
        ["generated_from_crawl_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_pagination_schemas_generated_from_artifact_id_artifacts",
        "pagination_schemas",
        "artifacts",
        ["generated_from_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_pagination_schemas_generated_by_task_run_id_task_runs",
        "pagination_schemas",
        "task_runs",
        ["generated_by_task_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_pagination_schemas_enabled", "pagination_schemas", ["enabled"])
    op.create_index("ix_pagination_schemas_match", "pagination_schemas", ["match"])
    op.create_index("ix_pagination_schemas_kind", "pagination_schemas", ["kind"])
    op.create_index("ix_pagination_schemas_domain", "pagination_schemas", ["domain"])


def downgrade() -> None:
    op.drop_index("ix_pagination_schemas_domain", table_name="pagination_schemas")
    op.drop_index("ix_pagination_schemas_kind", table_name="pagination_schemas")
    op.drop_index("ix_pagination_schemas_match", table_name="pagination_schemas")
    op.drop_index("ix_pagination_schemas_enabled", table_name="pagination_schemas")
    op.execute("ALTER TABLE pagination_schemas DROP CONSTRAINT IF EXISTS fk_pagination_schemas_generated_by_task_run_id_task_runs")
    op.execute("ALTER TABLE pagination_schemas DROP CONSTRAINT IF EXISTS fk_pagination_schemas_generated_from_artifact_id_artifacts")
    op.execute("ALTER TABLE pagination_schemas DROP CONSTRAINT IF EXISTS fk_pagination_schemas_generated_from_crawl_id_crawls")
    op.drop_table("pagination_schemas")
