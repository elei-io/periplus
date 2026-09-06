"""Link coverage intent to automatic source discovery and crawl execution."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260906_0002"
down_revision = "20260906_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("coverage_request_status", "coverage_requests", type_="check")
    op.create_check_constraint("coverage_request_status", "coverage_requests", "status IN ('pending', 'resolving', 'ongoing', 'completed', 'failed')")
    for column in [
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_urls", JSONB(), nullable=False, server_default="[]"),
        sa.Column("resolution", JSONB(), nullable=False, server_default="{}"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    ]:
        op.add_column("coverage_requests", column)
    for name in ["resolved_urls", "resolution", "attempts"]:
        op.alter_column("coverage_requests", name, server_default=None)
    op.create_unique_constraint("uq_coverage_requests_run_id", "coverage_requests", ["run_id"])


def downgrade():
    op.drop_constraint("uq_coverage_requests_run_id", "coverage_requests", type_="unique")
    for name in ["run_id", "started_at", "resolved_urls", "resolution", "attempts", "retry_at", "error"]:
        op.drop_column("coverage_requests", name)
    op.drop_constraint("coverage_request_status", "coverage_requests", type_="check")
    op.create_check_constraint("coverage_request_status", "coverage_requests", "status IN ('pending', 'ongoing', 'completed')")
