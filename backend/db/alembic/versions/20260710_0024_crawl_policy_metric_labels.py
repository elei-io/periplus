"""add bounded metric labels to crawl policies

Revision ID: 20260710_0024
Revises: 20260710_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_0024"
down_revision: str | None = "20260710_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("crawl_policies", sa.Column("metric_slug", sa.Text(), nullable=True))
    op.add_column(
        "crawl_policies",
        sa.Column("domain_group", sa.Text(), nullable=False, server_default="unclassified"),
    )
    op.execute(
        """
        UPDATE crawl_policies
        SET metric_slug = 'policy-' || left(replace(id::text, '-', ''), 12)
        WHERE metric_slug IS NULL
        """
    )
    op.alter_column("crawl_policies", "metric_slug", nullable=False)
    op.create_unique_constraint(
        "uq_crawl_policies_metric_slug", "crawl_policies", ["metric_slug"]
    )
    op.alter_column("crawl_policies", "domain_group", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_crawl_policies_metric_slug", "crawl_policies", type_="unique")
    op.drop_column("crawl_policies", "domain_group")
    op.drop_column("crawl_policies", "metric_slug")
