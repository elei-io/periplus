"""replace acquisition profiles with content policy

Revision ID: 20260716_0007
Revises: 20260714_0006
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0007"
down_revision: str | None = "20260714_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_policies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("host_match", sa.Text(), nullable=False),
        sa.Column("maximum_concurrency", sa.Integer(), nullable=False),
        sa.Column("minimum_request_interval_seconds", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("maximum_concurrency >= 1", name="ck_domain_policies_concurrency"),
        sa.CheckConstraint("minimum_request_interval_seconds >= 0", name="ck_domain_policies_interval"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host_match"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_domain_policies_enabled", "domain_policies", ["enabled"])
    op.add_column(
        "crawl_policies",
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(
                "jsonb_build_object("
                "'accepted_content_types', jsonb_build_array('text/html', 'application/xhtml+xml'), "
                "'response_rules', jsonb_build_object("
                "'http_status', jsonb_build_array("
                "jsonb_build_object('minimum', 408, 'maximum', 408, 'outcome', 'retry'), "
                "jsonb_build_object('minimum', 425, 'maximum', 425, 'outcome', 'retry'), "
                "jsonb_build_object('minimum', 429, 'maximum', 429, 'outcome', 'retry'), "
                "jsonb_build_object('minimum', 500, 'maximum', 599, 'outcome', 'retry'), "
                "jsonb_build_object('minimum', 400, 'maximum', 499, 'outcome', 'fail')), "
                "'unsupported_content_type', 'skip'), "
                "'completion', jsonb_build_object("
                "'wait_dynamic', jsonb_build_object("
                "'enabled', true, 'maximum_wait_ms', 8000, "
                "'sample_interval_ms', 250, 'stable_samples', 3), "
                "'wait_fixed', jsonb_build_object("
                "'enabled', false, 'duration_ms', 0), "
                "'scroll', jsonb_build_object("
                "'enabled', true, 'maximum_iterations', 30, "
                "'viewport_ratio', 0.85, 'wait_ms', 250, "
                "'stable_bottom_samples', 3), "
                "'expand', jsonb_build_object("
                "'enabled', true, 'maximum_actions', 10, 'wait_ms', 500)))"
            ),
        ),
    )
    op.drop_constraint("crawl_policies_profile_id_fkey", "crawl_policies", type_="foreignkey")
    op.drop_constraint("ck_crawl_policies_concurrency", "crawl_policies", type_="check")
    op.drop_column("crawl_policies", "profile_id")
    op.drop_column("crawl_policies", "max_concurrency")
    op.drop_index("uq_crawl_profiles_trial_cost", table_name="crawl_profiles")
    op.drop_table("crawl_profiles")


def downgrade() -> None:
    raise RuntimeError("Content-policy acquisition is a direct contract cut and cannot be downgraded")
