"""drop persisted catalogue compiler advisories

Revision ID: 20260725_0022
Revises: 20260724_0021
"""

from alembic import op
import sqlalchemy as sa

revision = "20260725_0022"
down_revision = "20260724_0021"
branch_labels = None
depends_on = None

_COMPILER_COLUMNS = (
    "catalogue_definition_revision",
    "compiler_version",
    "compiler_dependencies",
    "compiler_diagnostics",
    "compiler_outcome",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in (
        "catalogue_query_revisions",
        "catalogue_view_references",
        "catalogue_scalar_macros",
        "catalogue_table_macros",
    ):
        existing = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column_name in _COMPILER_COLUMNS:
            if column_name in existing:
                op.drop_column(table_name, column_name)


def downgrade() -> None:
    # Derived compiler advisories are intentionally not persisted.
    pass
