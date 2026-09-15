"""Bounded operator archive import jobs."""
from alembic import op
import sqlalchemy as sa

revision = "20260915_0022"
down_revision = "20260915_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("archive_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.String(1000)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_archive_imports_status", "archive_imports", ["status"])


def downgrade():
    op.drop_table("archive_imports")
