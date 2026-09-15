"""Raw tombstones are the sole corpus retirement authority."""

from alembic import op

revision = "20260916_0025"
down_revision = "20260916_0024"
branch_labels = depends_on = None


def upgrade():
    op.drop_table("retired_evidence")


def downgrade():
    raise RuntimeError("Restore the prior control backup to undo this contract change")
