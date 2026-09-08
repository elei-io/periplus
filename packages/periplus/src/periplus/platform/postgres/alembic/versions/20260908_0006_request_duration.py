"""Remove execution-only deadline fields from reusable definition intent."""
from alembic import op

revision = "20260908_0006"
down_revision = "20260908_0005"
branch_labels = None
depends_on = None


def upgrade():
    # Definitions previously carried a mandatory-null execution field. Actual
    # request deadlines remain immutable execution data, outside reusable intent.
    op.execute("UPDATE request_definitions SET specification = specification - 'deadline_at'")


def downgrade():
    raise RuntimeError("Reset disposable control state instead of restoring the old intent contract")
