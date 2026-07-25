"""persist fixture-authored materialization SQL

Revision ID: 20260725_0023
Revises: 20260725_0022
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0023"
down_revision = "20260725_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalogue_materializations",
        sa.Column("fixture_source_sql", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalogue_materializations", "fixture_source_sql")
