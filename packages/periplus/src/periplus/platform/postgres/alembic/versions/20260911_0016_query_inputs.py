"""Bounded notebook query inputs in the authoritative access policy."""
from alembic import op

revision = "20260911_0016"
down_revision = "20260910_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""UPDATE public_access SET configuration = jsonb_set(configuration, '{sql}',
        configuration->'sql' || '{"max_request_bytes": 4194304, "max_parameter_values": 100000}'::jsonb),
        version = version + 1 WHERE id = 1""")


def downgrade():
    op.execute("""UPDATE public_access SET configuration = jsonb_set(configuration, '{sql}',
        (configuration->'sql') - 'max_request_bytes' - 'max_parameter_values'),
        version = version + 1 WHERE id = 1""")
