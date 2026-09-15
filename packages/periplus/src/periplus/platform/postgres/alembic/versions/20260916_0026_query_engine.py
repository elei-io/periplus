"""Name query diagnostics for the active engine."""
from alembic import op
revision='20260916_0026'
down_revision='20260916_0025'
branch_labels=depends_on=None

def upgrade():
    op.alter_column('query_executions','duckdb_version',new_column_name='engine_version')

def downgrade():
    op.alter_column('query_executions','engine_version',new_column_name='duckdb_version')
