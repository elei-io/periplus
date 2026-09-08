"""Authoritative public access policy and bounded global admission windows."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = "20260908_0007"
down_revision = "20260908_0006"
branch_labels = None
depends_on = None

def upgrade():
    table = op.create_table("public_access", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("configuration", JSONB(), nullable=False),
        sa.Column("windows", JSONB(), nullable=False))
    op.bulk_insert(table, [{"id": 1, "version": 1, "configuration": {
        "crawl": {"enabled": True, "requests": 6, "window_seconds": 60, "page_budgets": [5,25,100,500,1000], "default_page_budget": 25,
            "max_depths": [0,1,2], "default_max_depth": 1, "retention_seconds": [None,604800,2592000,7776000,31536000], "default_retention_seconds": None},
        "assistant": {"enabled": True, "requests": 10, "window_seconds": 60},
        "sql": {"enabled": True, "requests": 60, "window_seconds": 60}}, "windows": {}}])

def downgrade():
    op.drop_table("public_access")
