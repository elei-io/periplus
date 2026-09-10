"""Separate public request admission from continuous crawler execution."""
from alembic import op
import sqlalchemy as sa

revision = '20260910_0014'
down_revision = '20260910_0013'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_frontier_limits', 'frontier_control', type_='check')
    op.drop_constraint('ck_frontier_rates', 'frontier_control', type_='check')
    for name in ('collection_limit', 'interest_limit', 'acquisition_limit', 'admission_limit',
                 'captures_per_minute', 'next_dispatch_at', 'last_dispatch_at'):
        op.drop_column('frontier_control', name)
    op.create_check_constraint('ck_frontier_limits', 'frontier_control', 'dispatch_limit > 0')
    op.add_column('frontier_interests', sa.Column('completed_status', sa.Text(), nullable=True))
    op.add_column('frontier_interests', sa.Column('completed_evidence', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('frontier_interests', 'completed_evidence', server_default=None)
    op.alter_column('frontier_interests', 'acquisition_id', nullable=True)
    op.alter_column('frontier_interests', 'context', nullable=True)
    op.create_check_constraint('ck_frontier_interest_completion', 'frontier_interests',
        "acquisition_id IS NOT NULL OR (status IN ('settled', 'cancelled') AND completed_status IS NOT NULL)")
    op.execute("UPDATE public_access SET configuration = jsonb_set(configuration, '{crawl,queue_limit}', '10000'::jsonb)")
    op.execute("UPDATE collections SET waiting_reason = NULL WHERE waiting_reason = 'frontier_admission_capacity'")


def downgrade():
    raise RuntimeError('Reclaimed acquisition references cannot be reconstructed; use a coordinated pre-upgrade backup')
