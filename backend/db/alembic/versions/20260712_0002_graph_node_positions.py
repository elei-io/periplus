"""graph node display positions

Revision ID: 20260712_0002
Revises: 20260712_0001
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260712_0002"
down_revision: str | None = "20260712_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("crawl_graph_nodes", sa.Column("position_x", sa.Float(), nullable=True))
    op.add_column("crawl_graph_nodes", sa.Column("position_y", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("crawl_graph_nodes", "position_y")
    op.drop_column("crawl_graph_nodes", "position_x")
