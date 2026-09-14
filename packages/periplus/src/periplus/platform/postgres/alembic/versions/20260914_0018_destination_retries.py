"""Persist destination name-not-found retry accounting."""
from alembic import op
import sqlalchemy as sa

revision = "20260914_0018"
down_revision = "20260912_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("frontier_acquisitions", sa.Column("dns_not_found_count", sa.Integer(),
                                                    nullable=False, server_default="0"))


def downgrade():
    op.drop_column("frontier_acquisitions", "dns_not_found_count")
