"""add per-run crawl budgets to schedules

Revision ID: 20260721_0016
Revises: 20260721_0015
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0016"
down_revision: str | None = "20260721_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crawl_schedules",
        sa.Column(
            "max_crawls",
            sa.Integer(),
            nullable=False,
            server_default="1000",
        ),
    )
    op.create_check_constraint(
        "ck_crawl_schedules_max_crawls",
        "crawl_schedules",
        "max_crawls >= 1 AND max_crawls <= 1000000",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_crawl_schedules_max_crawls",
        "crawl_schedules",
        type_="check",
    )
    op.drop_column("crawl_schedules", "max_crawls")
