"""url matches

Revision ID: 20260709_0013
Revises: 20260709_0012
Create Date: 2026-07-09 08:45:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260709_0013"
down_revision: str | None = "20260709_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "url_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scheme", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("path_pattern", sa.Text(), nullable=False),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("query_policy", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("created_by_task_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_task_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["created_by_task_run_id"],
            ["task_runs.id"],
            name="fk_url_matches_created_by_task_run_id_task_runs",
            use_alter=True,
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_task_run_id"],
            ["task_runs.id"],
            name="fk_url_matches_updated_by_task_run_id_task_runs",
            use_alter=True,
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "scheme",
            "host",
            "path_pattern",
            "match_type",
            "query_policy",
            name="uq_url_matches_identity",
        ),
    )
    op.create_index("ix_url_matches_domain", "url_matches", ["domain"])
    op.create_index("ix_url_matches_enabled", "url_matches", ["enabled"])
    op.create_index("ix_url_matches_host", "url_matches", ["host"])
    op.create_index("ix_url_matches_path_pattern", "url_matches", ["path_pattern"])

    op.add_column("query_schemas", sa.Column("url_match_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_query_schemas_url_match_id_url_matches",
        "query_schemas",
        "url_matches",
        ["url_match_id"],
        ["id"],
        use_alter=True,
        ondelete="SET NULL",
    )
    op.create_index("ix_query_schemas_url_match_id", "query_schemas", ["url_match_id"])

    op.execute(
        """
        INSERT INTO url_matches (
            id,
            scheme,
            host,
            domain,
            path_pattern,
            match_type,
            query_policy,
            enabled,
            priority,
            created_by_task_run_id,
            updated_by_task_run_id,
            created_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            split_part(match, '://', 1),
            split_part(split_part(match, '://', 2), '/', 1),
            regexp_replace(split_part(split_part(match, '://', 2), '/', 1), '^www\\.', ''),
            '/' || substring(split_part(match, '://', 2) from position('/' in split_part(match, '://', 2)) + 1),
            'exact',
            'ignore',
            enabled,
            priority,
            generated_by_task_run_id,
            generated_by_task_run_id,
            created_at,
            updated_at
        FROM query_schemas
        WHERE match LIKE '%://%/%'
        ON CONFLICT (scheme, host, path_pattern, match_type, query_policy) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE query_schemas
        SET url_match_id = url_matches.id
        FROM url_matches
        WHERE url_matches.scheme = split_part(query_schemas.match, '://', 1)
          AND url_matches.host = split_part(split_part(query_schemas.match, '://', 2), '/', 1)
          AND url_matches.path_pattern = '/' || substring(
                split_part(query_schemas.match, '://', 2)
                from position('/' in split_part(query_schemas.match, '://', 2)) + 1
              )
          AND url_matches.match_type = 'exact'
          AND url_matches.query_policy = 'ignore'
        """
    )

    op.alter_column("url_matches", "created_at", server_default=None)
    op.alter_column("url_matches", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_query_schemas_url_match_id", table_name="query_schemas")
    op.drop_constraint("fk_query_schemas_url_match_id_url_matches", "query_schemas", type_="foreignkey")
    op.drop_column("query_schemas", "url_match_id")

    op.drop_index("ix_url_matches_path_pattern", table_name="url_matches")
    op.drop_index("ix_url_matches_host", table_name="url_matches")
    op.drop_index("ix_url_matches_enabled", table_name="url_matches")
    op.drop_index("ix_url_matches_domain", table_name="url_matches")
    op.drop_table("url_matches")
