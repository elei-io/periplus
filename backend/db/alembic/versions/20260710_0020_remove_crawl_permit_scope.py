"""Remove redundant crawl permit scope key."""

from alembic import op
import sqlalchemy as sa

revision = "20260710_0020"
down_revision = "20260710_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("crawl_permits", "scope_key")


def downgrade() -> None:
    op.add_column("crawl_permits", sa.Column("scope_key", sa.Text(), nullable=False, server_default="global"))
