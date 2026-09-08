"""Private preparation evidence retained with terminal query operations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260908_0011"
down_revision = "20260908_0010"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in [("plan", sa.Text()), ("plan_truncated", sa.Boolean()),
                       ("plan_fingerprint", sa.String(64)), ("diagnostics", JSONB()),
                       ("duckdb_version", sa.String(80)), ("compiler_version", sa.String(80)),
                       ("effective_limits", JSONB())]:
        op.add_column("query_executions", sa.Column(name, kind, nullable=True))


def downgrade():
    for name in ["effective_limits", "compiler_version", "duckdb_version", "diagnostics",
                 "plan_fingerprint", "plan_truncated", "plan"]:
        op.drop_column("query_executions", name)
