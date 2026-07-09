"""query schemas

Revision ID: 20260709_0012
Revises: 20260709_0011
Create Date: 2026-07-09 08:20:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260709_0012"
down_revision: str | None = "20260709_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_schemas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("match", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("schema_type", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("schema_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_hash", sa.Text(), nullable=False),
        sa.Column("generated_from_crawl_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_by_task_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("inputs_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["generated_from_crawl_id"],
            ["crawls.id"],
            name="fk_query_schemas_generated_from_crawl_id_crawls",
            use_alter=True,
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["generated_by_task_run_id"],
            ["task_runs.id"],
            name="fk_query_schemas_generated_by_task_run_id_task_runs",
            use_alter=True,
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("identity_key", name="uq_query_schemas_identity_key"),
    )
    op.create_index("ix_query_schemas_domain", "query_schemas", ["domain"])
    op.create_index("ix_query_schemas_enabled", "query_schemas", ["enabled"])
    op.create_index("ix_query_schemas_match", "query_schemas", ["match"])
    op.create_index("ix_query_schemas_schema_type", "query_schemas", ["schema_type"])
    op.alter_column("query_schemas", "created_at", server_default=None)
    op.alter_column("query_schemas", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_query_schemas_schema_type", table_name="query_schemas")
    op.drop_index("ix_query_schemas_match", table_name="query_schemas")
    op.drop_index("ix_query_schemas_enabled", table_name="query_schemas")
    op.drop_index("ix_query_schemas_domain", table_name="query_schemas")
    op.drop_table("query_schemas")
