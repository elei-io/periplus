"""align provenance constraints with the model registry

Revision ID: 20260710_0023
Revises: 20260710_0022
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260710_0023"
down_revision: str | None = "20260710_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_query_schemas_url_match_id", table_name="query_schemas")
    op.create_foreign_key(
        "fk_query_schemas_generated_from_crawl_id_crawls",
        "query_schemas",
        "crawls",
        ["generated_from_crawl_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_query_schemas_generated_by_task_run_id_task_runs",
        "query_schemas",
        "task_runs",
        ["generated_by_task_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_url_matches_created_by_task_run_id_task_runs",
        "url_matches",
        "task_runs",
        ["created_by_task_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_url_matches_updated_by_task_run_id_task_runs",
        "url_matches",
        "task_runs",
        ["updated_by_task_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_url_matches_updated_by_task_run_id_task_runs", "url_matches", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_url_matches_created_by_task_run_id_task_runs", "url_matches", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_query_schemas_generated_by_task_run_id_task_runs",
        "query_schemas",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_query_schemas_generated_from_crawl_id_crawls",
        "query_schemas",
        type_="foreignkey",
    )
    op.create_index("ix_query_schemas_url_match_id", "query_schemas", ["url_match_id"])
