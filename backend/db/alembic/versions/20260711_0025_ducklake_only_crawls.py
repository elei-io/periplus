"""remove Postgres crawl and artifact history

Revision ID: 20260711_0025
Revises: 20260710_0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0025"
down_revision: str | None = "20260710_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
