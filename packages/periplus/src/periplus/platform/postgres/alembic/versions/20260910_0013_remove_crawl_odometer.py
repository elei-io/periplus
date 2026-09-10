"""Remove lifetime crawl allowances without changing work or evidence."""
from alembic import op

revision = '20260910_0013'
down_revision = '20260908_0012'
branch_labels = None
depends_on = None

COLUMNS = ('attempt_allowance', 'capture_time_allowance_ms', 'reserved_attempts',
           'started_attempts', 'reserved_capture_ms', 'charged_capture_ms')


def upgrade():
    op.drop_constraint('ck_frontier_attempt_counters', 'frontier_control', type_='check')
    op.drop_constraint('ck_frontier_attempt_allowances', 'frontier_control', type_='check')
    for name in COLUMNS:
        op.drop_column('frontier_control', name)
    op.create_check_constraint('ck_frontier_capture_timeout', 'frontier_control',
                               'capture_timeout_ms BETWEEN 1000 AND 3600000')


def downgrade():
    # Removed lifetime usage cannot be reconstructed. Rolling back requires an
    # explicit operator decision; never silently restore allowances with zero usage.
    raise RuntimeError('Lifetime crawl usage was removed; restore a pre-upgrade control backup to downgrade')
