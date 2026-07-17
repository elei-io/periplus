"""add crawl graph schedules

Revision ID: 20260717_0010
Revises: 20260717_0009
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0010"
down_revision: str | None = "20260717_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crawl_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("graph_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timing", postgresql.JSONB(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maximum_run_count", sa.Integer(), nullable=True),
        sa.Column("root_urls", postgresql.JSONB(), nullable=False),
        sa.Column("overlap_policy", sa.Text(), nullable=False),
        sa.Column("misfire_policy", sa.Text(), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_occurrence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "maximum_run_count IS NULL OR maximum_run_count >= 1",
            name="ck_crawl_schedules_maximum_run_count",
        ),
        sa.CheckConstraint(
            "run_count >= 0", name="ck_crawl_schedules_run_count"
        ),
        sa.CheckConstraint(
            "overlap_policy IN ('skip', 'allow')",
            name="ck_crawl_schedules_overlap_policy",
        ),
        sa.CheckConstraint(
            "misfire_policy IN ('skip', 'run_once')",
            name="ck_crawl_schedules_misfire_policy",
        ),
        sa.ForeignKeyConstraint(
            ["graph_id"], ["crawl_graphs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "graph_id", "name", name="uq_crawl_schedules_graph_name"
        ),
    )
    op.create_index(
        "ix_crawl_schedules_graph_id",
        "crawl_schedules",
        ["graph_id"],
    )
    op.create_index(
        "ix_crawl_schedules_next_run_at",
        "crawl_schedules",
        ["next_run_at"],
    )
    op.create_index(
        "ix_crawl_schedules_due",
        "crawl_schedules",
        ["next_run_at"],
        postgresql_where=sa.text("enabled AND next_run_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("crawl_schedules")
