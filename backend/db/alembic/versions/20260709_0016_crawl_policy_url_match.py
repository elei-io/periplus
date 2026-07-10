"""crawl policy url match

Revision ID: 20260709_0016
Revises: 20260709_0015
Create Date: 2026-07-09 15:10:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260709_0016"
down_revision: str | None = "20260709_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("crawl_policies", sa.Column("url_match_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_crawl_policies_url_match_id_url_matches",
        "crawl_policies",
        "url_matches",
        ["url_match_id"],
        ["id"],
        use_alter=True,
        ondelete="SET NULL",
    )
    op.create_index("ix_crawl_policies_url_match_id", "crawl_policies", ["url_match_id"])


def downgrade() -> None:
    op.drop_index("ix_crawl_policies_url_match_id", table_name="crawl_policies")
    op.drop_constraint("fk_crawl_policies_url_match_id_url_matches", "crawl_policies", type_="foreignkey")
    op.drop_column("crawl_policies", "url_match_id")
