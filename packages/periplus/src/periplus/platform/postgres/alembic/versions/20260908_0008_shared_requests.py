"""Remove acquisition privacy partitions; request class is frozen intent metadata."""
from alembic import op

revision = "20260908_0008"
down_revision = "20260908_0007"
branch_labels = None
depends_on = None


def upgrade():
    # This changes acquisition identity and immutable evidence contracts. A live
    # frontier must not straddle the cutover; reset disposable prior state.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM frontier_acquisitions)
           OR EXISTS (SELECT 1 FROM collections)
           OR EXISTS (SELECT 1 FROM request_definitions) THEN
            RAISE EXCEPTION 'Shared-request cutover requires fresh disposable control and lake state';
        END IF;
    END $$""")
    op.drop_column('frontier_acquisitions', 'access_context')
    op.drop_column('frontier_acquisitions', 'visibility')


def downgrade():
    raise RuntimeError('Reset disposable state instead of restoring privacy partitions')
