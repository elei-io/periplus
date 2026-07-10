"""remove Postgres crawl and artifact history

Revision ID: 20260711_0025
Revises: 20260710_0024
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0025"
down_revision: str | None = "20260710_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _require_legacy_storage_drop_acknowledgement()
    op.add_column(
        "data_schemas",
        sa.Column("generated_from_document_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "query_schemas",
        sa.Column("generated_from_document_id", sa.Text(), nullable=True),
    )

    op.execute(
        "ALTER TABLE data_schemas DROP CONSTRAINT IF EXISTS "
        "fk_data_schemas_generated_from_artifact_id_artifacts"
    )
    op.execute(
        "ALTER TABLE data_schemas DROP CONSTRAINT IF EXISTS "
        "fk_extract_schemas_generated_from_artifact_id_artifacts"
    )
    op.execute(
        "ALTER TABLE data_schemas DROP CONSTRAINT IF EXISTS "
        "fk_data_schemas_generated_from_crawl_id_crawls"
    )
    op.execute(
        "ALTER TABLE data_schemas DROP CONSTRAINT IF EXISTS "
        "fk_extract_schemas_generated_from_crawl_id_crawls"
    )
    op.execute(
        "ALTER TABLE query_schemas DROP CONSTRAINT IF EXISTS "
        "fk_query_schemas_generated_from_crawl_id_crawls"
    )
    op.drop_column("data_schemas", "generated_from_artifact_id")

    op.drop_table("task_run_artifacts")
    op.drop_table("task_run_crawls")
    op.drop_table("artifacts")
    op.drop_table("crawls")
    op.drop_column("crawl_permits", "url_id")
    op.execute("DROP TABLE IF EXISTS query_params")
    op.execute("DROP TABLE IF EXISTS paths")
    op.execute("DROP TABLE IF EXISTS domains")
    op.drop_table("urls")


def downgrade() -> None:
    raise RuntimeError(
        "The DuckLake-only crawl ownership cut is intentionally irreversible."
    )


def _require_legacy_storage_drop_acknowledgement() -> None:
    """Refuse to silently destroy legacy history during an existing installation upgrade."""

    connection = op.get_bind()
    inspector = sa.inspect(connection)
    populated: dict[str, int] = {}
    for table_name in (
        "task_run_artifacts",
        "task_run_crawls",
        "artifacts",
        "crawls",
        "query_params",
        "paths",
        "domains",
        "urls",
    ):
        if not inspector.has_table(table_name):
            continue
        count = int(
            connection.scalar(sa.text(f'SELECT count(*) FROM "{table_name}"')) or 0
        )
        if count:
            populated[table_name] = count
    if not populated:
        return
    acknowledged = os.getenv("ATLAS_ALLOW_LEGACY_STORAGE_DROP", "").strip().lower()
    if acknowledged in {"1", "true", "yes"}:
        return
    counts = ", ".join(f"{table}={count}" for table, count in populated.items())
    raise RuntimeError(
        "Migration 20260711_0025 would irreversibly drop populated legacy crawl/artifact "
        f"storage ({counts}). Stop Atlas, export or back up the control-plane database, "
        "then rerun with ATLAS_ALLOW_LEGACY_STORAGE_DROP=true to acknowledge the cutover."
    )
