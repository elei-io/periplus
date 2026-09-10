"""Public per-page follow-link choices in the existing access policy."""
from alembic import op

revision = "20260910_0015"
down_revision = "20260910_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""UPDATE public_access SET configuration = jsonb_set(configuration, '{crawl}',
        configuration->'crawl' || '{"follow_link_limits": [100, 1000, 5000, 10000],
        "default_follow_link_limit": 1000}'::jsonb), version = version + 1 WHERE id = 1""")


def downgrade():
    op.execute("""UPDATE public_access SET configuration = jsonb_set(configuration, '{crawl}',
        (configuration->'crawl') - 'follow_link_limits' - 'default_follow_link_limit'),
        version = version + 1 WHERE id = 1""")
