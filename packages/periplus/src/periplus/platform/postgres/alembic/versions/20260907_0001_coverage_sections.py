"""Persist explicit URL-section limits for coverage requests."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260907_0001"
down_revision = "20260906_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("coverage_requests", sa.Column("allowed_sections", JSONB(), nullable=False, server_default="[]"))
    op.alter_column("coverage_requests", "allowed_sections", server_default=None)


def downgrade():
    op.drop_column("coverage_requests", "allowed_sections")
