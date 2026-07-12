"""crawl graph edge deduplication mode

Revision ID: 20260712_0003
Revises: 20260712_0002
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0003"
down_revision: str | None = "20260712_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


dedupe_mode = postgresql.ENUM(
    "graph", "crawl", "document", name="crawl_graph_edge_dedupe_mode"
)


def upgrade() -> None:
    dedupe_mode.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "crawl_graph_edges",
        sa.Column(
            "dedupe_mode",
            dedupe_mode,
            server_default="graph",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("crawl_graph_edges", "dedupe_mode")
    dedupe_mode.drop(op.get_bind(), checkfirst=True)
