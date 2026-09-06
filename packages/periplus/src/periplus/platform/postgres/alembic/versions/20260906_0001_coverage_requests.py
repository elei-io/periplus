"""Store public coverage intent independently of graph execution."""
from alembic import op
import sqlalchemy as sa

revision = "20260906_0001"
down_revision = "20260731_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coverage_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("link_scope", sa.Text(), nullable=False),
        sa.Column("max_pages", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('url', 'description')", name="coverage_request_kind"),
        sa.CheckConstraint("status IN ('pending', 'ongoing', 'completed')", name="coverage_request_status"),
        sa.CheckConstraint("depth BETWEEN 0 AND 2", name="coverage_request_depth"),
        sa.CheckConstraint("max_pages BETWEEN 1 AND 1000", name="coverage_request_max_pages"),
        sa.CheckConstraint("link_scope IN ('internal', 'external', 'both')", name="coverage_request_scope"),
    )
    op.create_index("ix_coverage_requests_status_created", "coverage_requests", ["status", "created_at", "id"])


def downgrade() -> None:
    op.drop_table("coverage_requests")
