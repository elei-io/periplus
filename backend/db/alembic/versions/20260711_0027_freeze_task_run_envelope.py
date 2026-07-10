"""freeze task-run primitive and crawl-policy execution envelope

Revision ID: 20260711_0027
Revises: 20260711_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260711_0027"
down_revision: str | None = "20260711_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task_runs", sa.Column("primitive", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE task_runs AS run
        SET primitive = task.primitive
        FROM tasks AS task
        WHERE task.id = run.task_id
        """
    )
    op.alter_column("task_runs", "primitive", nullable=False)
    op.create_index("ix_task_runs_primitive", "task_runs", ["primitive"])

    op.add_column(
        "task_runs",
        sa.Column(
            "crawl_policy_snapshots_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE task_runs
        SET crawl_policy_snapshots_json = (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'id', policy.id,
                        'revision', policy.revision,
                        'metric_slug', policy.metric_slug,
                        'domain_group', policy.domain_group,
                        'match', matcher.scheme || '://' || matcher.host || matcher.path_pattern,
                        'config', policy.config,
                        'matcher', jsonb_build_object(
                            'scheme', matcher.scheme,
                            'host', matcher.host,
                            'path_pattern', matcher.path_pattern,
                            'match_type', matcher.match_type,
                            'priority', matcher.priority
                        )
                    )
                    ORDER BY policy.id
                ),
                '[]'::jsonb
            )
            FROM crawl_policies AS policy
            JOIN url_matches AS matcher ON matcher.id = policy.url_match_id
            WHERE policy.enabled IS TRUE AND matcher.enabled IS TRUE
        )
        WHERE status IN ('queued', 'running')
        """
    )

    # A frozen policy can be edited or deleted while its run is queued. Permits use
    # the frozen UUID as a logical capacity key and must not depend on the mutable row.
    op.drop_constraint("crawl_permits_policy_id_fkey", "crawl_permits", type_="foreignkey")


def downgrade() -> None:
    op.execute(
        """
        UPDATE crawl_permits AS permit
        SET policy_id = NULL
        WHERE policy_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM crawl_policies AS policy WHERE policy.id = permit.policy_id
          )
        """
    )
    op.create_foreign_key(
        "crawl_permits_policy_id_fkey",
        "crawl_permits",
        "crawl_policies",
        ["policy_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("task_runs", "crawl_policy_snapshots_json")
    op.drop_index("ix_task_runs_primitive", table_name="task_runs")
    op.drop_column("task_runs", "primitive")
