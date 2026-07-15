"""scope crawl graph roots to their owning graph

Revision ID: 20260712_0004
Revises: 20260712_0003
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260712_0004"
down_revision: str | None = "20260712_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_crawl_graphs_root_node", "crawl_graphs", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_crawl_graphs_root_node",
        "crawl_graphs",
        "crawl_graph_nodes",
        ["id", "root_node_id"],
        ["graph_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_crawl_graphs_root_node", "crawl_graphs", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_crawl_graphs_root_node",
        "crawl_graphs",
        "crawl_graph_nodes",
        ["root_node_id"],
        ["id"],
        ondelete="SET NULL",
    )
