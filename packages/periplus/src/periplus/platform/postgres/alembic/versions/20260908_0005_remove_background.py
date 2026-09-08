"""Remove the separate background crawler controls and current execution state."""
from alembic import op
import sqlalchemy as sa
revision = "20260908_0005"
down_revision = "20260908_0004"
branch_labels = None
depends_on = None

CONTROL = ('background_share', 'background_credit', 'background_attempt_allowance',
    'background_capture_time_allowance_ms', 'background_reserved_attempts', 'background_started_attempts',
    'background_reserved_capture_ms', 'background_charged_capture_ms')
ACQUISITION = ('attempt_background', 'background_url_key', 'background_after', 'background_token',
    'background_expires_at', 'background_failures', 'background_error', 'background_selected', 'background_reason')

def upgrade():
    # Stop old workers first. Never silently abandon live background-only captures.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM frontier_acquisitions WHERE background_reason IS NOT NULL AND status IN ('queued','retry','dispatched')")).scalar():
        raise RuntimeError('Drain background acquisitions before removing background execution')
    op.drop_table('frontier_background_checks')
    op.drop_index('ix_frontier_background_parent', table_name='frontier_acquisitions')
    op.drop_constraint('uq_frontier_background_active_url', 'frontier_acquisitions', type_='unique')
    for name in ('ck_frontier_background_counters', 'ck_frontier_background_allowances', 'ck_frontier_rates'):
        op.drop_constraint(name, 'frontier_control', type_='check')
    for name in CONTROL:
        op.drop_column('frontier_control', name)
    for name in ACQUISITION:
        op.drop_column('frontier_acquisitions', name)
    op.create_check_constraint('ck_frontier_rates', 'frontier_control', 'captures_per_minute >= 0')

def downgrade():
    raise RuntimeError('Background execution was removed; reset disposable state instead of restoring it')
