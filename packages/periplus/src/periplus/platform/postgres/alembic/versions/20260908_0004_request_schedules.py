"""Reusable request definitions and schedules; no execution history mirror."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = "20260908_0004"
down_revision = "20260907_0003"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("request_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("name", sa.Text(), nullable=False),
        sa.Column("specification", JSONB(), nullable=False), sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("request_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("definition_id", sa.Uuid(), sa.ForeignKey("request_definitions.id"), nullable=False),
        sa.Column("configuration", JSONB(), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("execution_count", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True)), sa.Column("last_request_id", sa.Uuid()),
        sa.Column("last_tick_at", sa.DateTime(timezone=True)), sa.Column("last_result", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_request_schedule_due", "request_schedules", ["enabled", "next_at"])

def downgrade():
    op.drop_table("request_schedules")
    op.drop_table("request_definitions")
