"""Move Periplus lake coordination into control Postgres."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '20260908_0012'
down_revision = '20260908_0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('lake_write_claims',
        sa.Column('kind', sa.Text(), primary_key=True),
        sa.Column('identity', sa.Text(), primary_key=True),
        sa.Column('owner', UUID(as_uuid=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_lake_write_claims_expires_at', 'lake_write_claims', ['expires_at'])
    op.create_table('retired_evidence',
        sa.Column('kind', sa.Text(), primary_key=True),
        sa.Column('identity', sa.Text(), primary_key=True),
        sa.Column('retired_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('retention_objects',
        sa.Column('object_key', sa.Text(), primary_key=True),
        sa.Column('content_sha256', sa.Text(), nullable=False),
        sa.Column('stored_bytes', sa.BigInteger(), nullable=False),
        sa.Column('retired_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('retired_snapshot', sa.BigInteger(), nullable=False),
        sa.Column('snapshots_cleared_at', sa.DateTime(timezone=True)),
        sa.Column('retirement_id', UUID(as_uuid=True), nullable=False, unique=True))
    op.create_index('ix_retention_objects_content_sha256', 'retention_objects', ['content_sha256'])
    op.create_index('ix_retention_objects_retired_at', 'retention_objects', ['retired_at'])
    op.create_table('materialization_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('generation_id', UUID(as_uuid=True), nullable=False),
        sa.Column('covered_snapshot', sa.BigInteger(), nullable=False),
        sa.Column('batch_size', sa.Integer(), nullable=False),
        sa.Column('registry_digest', sa.Text(), nullable=False),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_materialization_state_singleton'))
    op.create_table('materialization_applied_batches',
        sa.Column('batch_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('run_id', UUID(as_uuid=True), nullable=False),
        sa.Column('source_snapshot', sa.BigInteger(), nullable=False),
        sa.Column('source_items', sa.BigInteger(), nullable=False),
        sa.Column('source_bytes', sa.BigInteger(), nullable=False),
        sa.Column('output_rows', sa.BigInteger(), nullable=False),
        sa.Column('output_bytes', sa.BigInteger(), nullable=False),
        sa.Column('committed_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_materialization_applied_batches_run_id', 'materialization_applied_batches', ['run_id'])


def downgrade():
    for name in ('materialization_applied_batches', 'materialization_state',
                 'retention_objects', 'retired_evidence', 'lake_write_claims'):
        op.drop_table(name)
