"""persist catalogue compiler authoring advisories

Revision ID: 20260724_0019
Revises: 20260724_0018
"""

from alembic import op
import sqlalchemy as sa

revision = "20260724_0019"
down_revision = "20260724_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalogue_query_revisions",
        sa.Column("compiler_outcome", sa.Text(), nullable=True),
    )
    for table_name in (
        "catalogue_view_references",
        "catalogue_scalar_macros",
        "catalogue_table_macros",
    ):
        op.add_column(
            table_name,
            sa.Column("compiler_outcome", sa.Text(), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("compiler_diagnostics", sa.JSON(), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("compiler_dependencies", sa.JSON(), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("compiler_version", sa.Text(), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("catalogue_definition_revision", sa.Text(), nullable=True),
        )
    op.add_column(
        "catalogue_query_revisions",
        sa.Column("compiler_diagnostics", sa.JSON(), nullable=True),
    )
    op.add_column(
        "catalogue_query_revisions",
        sa.Column("compiler_dependencies", sa.JSON(), nullable=True),
    )
    op.add_column(
        "catalogue_query_revisions",
        sa.Column("compiler_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "catalogue_query_revisions",
        sa.Column("catalogue_definition_revision", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE catalogue_query_revisions
            SET compiler_outcome = 'unsupported',
                compiler_diagnostics = CAST('[]' AS JSON),
                compiler_dependencies = CAST('[]' AS JSON),
                compiler_version = 'unknown'
            WHERE compiler_outcome IS NULL
            """
        )
    )
    for column_name in (
        "compiler_outcome",
        "compiler_diagnostics",
        "compiler_dependencies",
        "compiler_version",
    ):
        op.alter_column(
            "catalogue_query_revisions",
            column_name,
            nullable=False,
        )


def downgrade() -> None:
    for table_name in (
        "catalogue_table_macros",
        "catalogue_scalar_macros",
        "catalogue_view_references",
    ):
        op.drop_column(table_name, "catalogue_definition_revision")
        op.drop_column(table_name, "compiler_version")
        op.drop_column(table_name, "compiler_dependencies")
        op.drop_column(table_name, "compiler_diagnostics")
        op.drop_column(table_name, "compiler_outcome")
    op.drop_column("catalogue_query_revisions", "catalogue_definition_revision")
    op.drop_column("catalogue_query_revisions", "compiler_version")
    op.drop_column("catalogue_query_revisions", "compiler_dependencies")
    op.drop_column("catalogue_query_revisions", "compiler_diagnostics")
    op.drop_column("catalogue_query_revisions", "compiler_outcome")
