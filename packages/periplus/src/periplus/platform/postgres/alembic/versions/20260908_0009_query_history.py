"""Private best-effort query execution history, retained for 30 days."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = "20260908_0009"
down_revision = "20260908_0008"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('query_executions',
        sa.Column('execution_id', sa.Uuid(), primary_key=True),
        sa.Column('request_id', sa.Uuid(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('operation', sa.String(16), nullable=False),
        sa.Column('sql_text', sa.Text(), nullable=False),
        sa.Column('parameters', JSONB(), nullable=False),
        sa.Column('query_template', sa.Text(), nullable=True),
        sa.Column('query_fingerprint', sa.String(64), nullable=True),
        sa.Column('fingerprint_version', sa.String(80), nullable=False),
        sa.Column('relations', JSONB(), nullable=False),
        sa.Column('functions', JSONB(), nullable=False),
        sa.Column('features', JSONB(), nullable=False),
        sa.Column('outcome', sa.String(16), nullable=False),
        sa.Column('error_code', sa.String(80), nullable=True),
        sa.Column('elapsed_ms', sa.Float(), nullable=False),
        sa.Column('result_rows', sa.BigInteger(), nullable=True),
        sa.Column('result_bytes', sa.BigInteger(), nullable=True),
        sa.Column('truncated', sa.Boolean(), nullable=True),
        sa.Column('source_snapshot', sa.BigInteger(), nullable=True),
        sa.Column('service_version', sa.String(200), nullable=True))
    op.create_index('ix_query_executions_started', 'query_executions', ['started_at'])
    op.create_index('ix_query_executions_pattern_started', 'query_executions', ['query_fingerprint', 'started_at'])

def downgrade():
    op.drop_table('query_executions')
