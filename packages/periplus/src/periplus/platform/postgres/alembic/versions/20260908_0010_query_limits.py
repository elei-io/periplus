"""Public SQL execution limits in the existing access policy."""
from alembic import op

revision = "20260908_0010"
down_revision = "20260908_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""UPDATE public_access SET configuration = jsonb_set(configuration, '{sql}',
        configuration->'sql' || '{"max_rows": 1000, "max_duration_seconds": 20, "max_result_bytes": 8388608}'::jsonb),
        version = version + 1 WHERE id = 1""")


def downgrade():
    op.execute("""UPDATE public_access SET configuration = jsonb_set(configuration, '{sql}',
        (configuration->'sql') - 'max_rows' - 'max_duration_seconds' - 'max_result_bytes'),
        version = version + 1 WHERE id = 1""")
