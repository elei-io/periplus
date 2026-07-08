"""crawl data model

Revision ID: 20260708_0005
Revises: 20260704_0004
Create Date: 2026-07-08 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260708_0005"
down_revision: str | None = "20260704_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "urls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("scheme", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("query_fingerprint", sa.Text(), nullable=True),
        sa.UniqueConstraint("normalized_url", name="uq_urls_normalized_url"),
        sa.UniqueConstraint("url", name="uq_urls_url"),
    )
    op.create_index("ix_urls_domain", "urls", ["domain"])
    op.create_index("ix_urls_host", "urls", ["host"])
    op.create_index("ix_urls_path", "urls", ["path"])

    op.create_table(
        "crawls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("url_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("inputs_json", postgresql.JSONB(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("redirects_json", postgresql.JSONB(), nullable=False),
        sa.Column("errors_json", postgresql.JSONB(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], name="fk_crawls_task_run_id_task_runs", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["url_id"], ["urls.id"], name="fk_crawls_url_id_urls", ondelete="CASCADE"),
    )
    op.create_index("ix_crawls_input_hash", "crawls", ["input_hash"])
    op.create_index("ix_crawls_started_at", "crawls", ["started_at"])
    op.create_index("ix_crawls_status_code", "crawls", ["status_code"])
    op.create_index("ix_crawls_success", "crawls", ["success"])
    op.create_index("ix_crawls_task_run_id", "crawls", ["task_run_id"])
    op.create_index("ix_crawls_url_id", "crawls", ["url_id"])

    op.drop_index("ix_artifacts_cache_key", table_name="artifacts")
    op.add_column("artifacts", sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("artifacts", sa.Column("url_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("artifacts", sa.Column("content_type", sa.Text(), nullable=False, server_default="application/octet-stream"))
    op.add_column("artifacts", sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("artifacts", sa.Column("sha256", sa.Text(), nullable=False, server_default=""))
    op.add_column("artifacts", sa.Column("input_hash", sa.Text(), nullable=True))
    op.add_column("artifacts", sa.Column("extracted", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("artifacts", sa.Column("warnings_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("artifacts", sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("artifacts", sa.Column("invalidated_reason", sa.Text(), nullable=True))
    op.alter_column("artifacts", "task_run_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.drop_column("artifacts", "cache_key")
    op.create_foreign_key("fk_artifacts_crawl_id_crawls", "artifacts", "crawls", ["crawl_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_artifacts_url_id_urls", "artifacts", "urls", ["url_id"], ["id"], ondelete="SET NULL")
    op.drop_constraint("fk_artifacts_task_run_id_task_runs", "artifacts", type_="foreignkey")
    op.create_foreign_key(
        "fk_artifacts_task_run_id_task_runs",
        "artifacts",
        "task_runs",
        ["task_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_artifacts_crawl_id", "artifacts", ["crawl_id"])
    op.create_index("ix_artifacts_input_hash", "artifacts", ["input_hash"])
    op.create_index("ix_artifacts_invalidated_at", "artifacts", ["invalidated_at"])
    op.create_index("ix_artifacts_sha256", "artifacts", ["sha256"])
    op.create_index("ix_artifacts_url_id", "artifacts", ["url_id"])
    op.alter_column("artifacts", "content_type", server_default=None)
    op.alter_column("artifacts", "size_bytes", server_default=None)
    op.alter_column("artifacts", "sha256", server_default=None)
    op.alter_column("artifacts", "extracted", server_default=None)
    op.alter_column("artifacts", "warnings_json", server_default=None)

    op.create_table(
        "task_run_crawls",
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["crawl_id"], ["crawls.id"], name="fk_task_run_crawls_crawl_id_crawls", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], name="fk_task_run_crawls_task_run_id_task_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_run_id", "crawl_id", "role", name="pk_task_run_crawls"),
    )
    op.create_index("ix_task_run_crawls_crawl_id", "task_run_crawls", ["crawl_id"])
    op.create_index("ix_task_run_crawls_role", "task_run_crawls", ["role"])
    op.create_index("ix_task_run_crawls_task_run_id", "task_run_crawls", ["task_run_id"])

    op.create_table(
        "task_run_artifacts",
        sa.Column("task_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], name="fk_task_run_artifacts_artifact_id_artifacts", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"], name="fk_task_run_artifacts_task_run_id_task_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_run_id", "artifact_id", "role", name="pk_task_run_artifacts"),
    )
    op.create_index("ix_task_run_artifacts_artifact_id", "task_run_artifacts", ["artifact_id"])
    op.create_index("ix_task_run_artifacts_role", "task_run_artifacts", ["role"])
    op.create_index("ix_task_run_artifacts_task_run_id", "task_run_artifacts", ["task_run_id"])

    op.create_table(
        "extract_schemas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("match", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("schema_type", sa.Text(), nullable=False),
        sa.Column("target_json_hash", sa.Text(), nullable=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("schema_json", postgresql.JSONB(), nullable=False),
        sa.Column("schema_hash", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["generated_by_task_run_id"], ["task_runs.id"], name="fk_extract_schemas_generated_by_task_run_id_task_runs", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["generated_from_artifact_id"], ["artifacts.id"], name="fk_extract_schemas_generated_from_artifact_id_artifacts", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["generated_from_crawl_id"], ["crawls.id"], name="fk_extract_schemas_generated_from_crawl_id_crawls", ondelete="SET NULL"),
        sa.UniqueConstraint("identity_key", name="uq_extract_schemas_identity_key"),
    )
    op.create_index("ix_extract_schemas_domain", "extract_schemas", ["domain"])
    op.create_index("ix_extract_schemas_enabled", "extract_schemas", ["enabled"])
    op.create_index("ix_extract_schemas_match", "extract_schemas", ["match"])
    op.create_index("ix_extract_schemas_prompt_hash", "extract_schemas", ["prompt_hash"])
    op.create_index("ix_extract_schemas_schema_type", "extract_schemas", ["schema_type"])

    op.add_column("task_runs", sa.Column("extract_schema_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_task_runs_extract_schema_id_extract_schemas",
        "task_runs",
        "extract_schemas",
        ["extract_schema_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "crawl_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("match", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_crawl_policies_enabled", "crawl_policies", ["enabled"])
    op.create_index("ix_crawl_policies_match", "crawl_policies", ["match"])


def downgrade() -> None:
    op.drop_index("ix_crawl_policies_match", table_name="crawl_policies")
    op.drop_index("ix_crawl_policies_enabled", table_name="crawl_policies")
    op.drop_table("crawl_policies")

    op.drop_constraint("fk_task_runs_extract_schema_id_extract_schemas", "task_runs", type_="foreignkey")
    op.drop_column("task_runs", "extract_schema_id")

    op.drop_index("ix_extract_schemas_schema_type", table_name="extract_schemas")
    op.drop_index("ix_extract_schemas_prompt_hash", table_name="extract_schemas")
    op.drop_index("ix_extract_schemas_match", table_name="extract_schemas")
    op.drop_index("ix_extract_schemas_enabled", table_name="extract_schemas")
    op.drop_index("ix_extract_schemas_domain", table_name="extract_schemas")
    op.drop_table("extract_schemas")

    op.drop_index("ix_task_run_artifacts_task_run_id", table_name="task_run_artifacts")
    op.drop_index("ix_task_run_artifacts_role", table_name="task_run_artifacts")
    op.drop_index("ix_task_run_artifacts_artifact_id", table_name="task_run_artifacts")
    op.drop_table("task_run_artifacts")

    op.drop_index("ix_task_run_crawls_task_run_id", table_name="task_run_crawls")
    op.drop_index("ix_task_run_crawls_role", table_name="task_run_crawls")
    op.drop_index("ix_task_run_crawls_crawl_id", table_name="task_run_crawls")
    op.drop_table("task_run_crawls")

    op.drop_index("ix_artifacts_url_id", table_name="artifacts")
    op.drop_index("ix_artifacts_sha256", table_name="artifacts")
    op.drop_index("ix_artifacts_invalidated_at", table_name="artifacts")
    op.drop_index("ix_artifacts_input_hash", table_name="artifacts")
    op.drop_index("ix_artifacts_crawl_id", table_name="artifacts")
    op.drop_constraint("fk_artifacts_task_run_id_task_runs", "artifacts", type_="foreignkey")
    op.drop_constraint("fk_artifacts_url_id_urls", "artifacts", type_="foreignkey")
    op.drop_constraint("fk_artifacts_crawl_id_crawls", "artifacts", type_="foreignkey")
    op.add_column("artifacts", sa.Column("cache_key", sa.Text(), nullable=True))
    op.alter_column("artifacts", "task_run_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.create_foreign_key(
        "fk_artifacts_task_run_id_task_runs",
        "artifacts",
        "task_runs",
        ["task_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("artifacts", "invalidated_reason")
    op.drop_column("artifacts", "invalidated_at")
    op.drop_column("artifacts", "warnings_json")
    op.drop_column("artifacts", "extracted")
    op.drop_column("artifacts", "input_hash")
    op.drop_column("artifacts", "sha256")
    op.drop_column("artifacts", "size_bytes")
    op.drop_column("artifacts", "content_type")
    op.drop_column("artifacts", "url_id")
    op.drop_column("artifacts", "crawl_id")
    op.create_index("ix_artifacts_cache_key", "artifacts", ["cache_key"])

    op.drop_index("ix_crawls_url_id", table_name="crawls")
    op.drop_index("ix_crawls_task_run_id", table_name="crawls")
    op.drop_index("ix_crawls_success", table_name="crawls")
    op.drop_index("ix_crawls_status_code", table_name="crawls")
    op.drop_index("ix_crawls_started_at", table_name="crawls")
    op.drop_index("ix_crawls_input_hash", table_name="crawls")
    op.drop_table("crawls")

    op.drop_index("ix_urls_path", table_name="urls")
    op.drop_index("ix_urls_host", table_name="urls")
    op.drop_index("ix_urls_domain", table_name="urls")
    op.drop_table("urls")
