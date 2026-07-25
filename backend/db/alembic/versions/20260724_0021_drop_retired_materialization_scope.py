"""drop retired materialization scope state

Revision ID: 20260724_0021
Revises: 20260724_0020
"""

from alembic import op
import sqlalchemy as sa

revision = "20260724_0021"
down_revision = "20260724_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "catalogue_materializations"
        )
    }
    if "scope_relations" in columns:
        op.drop_column(
            "catalogue_materializations",
            "scope_relations",
        )


def downgrade() -> None:
    # The superseded scope pipeline is intentionally not restored.
    pass
