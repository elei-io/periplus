"""enforce one catalogue materialization per query or view

Revision ID: 20260712_0037
Revises: 20260711_0036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0037"
down_revision: str | None = "20260711_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_catalogue_query_revision_identity",
        "catalogue_query_revisions",
        ["query_id", "id"],
    )

    for constraint in (
        "ck_materialized_views_one_source",
        "ck_materialized_views_refresh_mode",
        "ck_materialized_views_document_scope",
        "ck_materialized_views_mode_state",
        "ck_materialized_views_positive_backfill_rate",
    ):
        op.drop_constraint(constraint, "materialized_views", type_="check")
    op.drop_constraint(
        "materialized_views_query_revision_id_fkey",
        "materialized_views",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_materialized_views_source_view_reference",
        "materialized_views",
        type_="foreignkey",
    )
    op.drop_index("ix_materialized_views_archived_at", table_name="materialized_views")

    op.rename_table("materialized_views", "catalogue_materializations")
    op.execute(
        "ALTER TABLE catalogue_materializations "
        "RENAME CONSTRAINT materialized_views_pkey TO catalogue_materializations_pkey"
    )
    op.execute(
        "ALTER TABLE catalogue_materializations "
        "RENAME CONSTRAINT materialized_views_name_key "
        "TO catalogue_materializations_name_key"
    )
    op.execute(
        """
        DO $$
        DECLARE existing_constraint record;
        BEGIN
            FOR existing_constraint IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'catalogue_materializations'::regclass
                  AND conname LIKE 'materialized_views_%_not_null'
            LOOP
                EXECUTE format(
                    'ALTER TABLE catalogue_materializations RENAME CONSTRAINT %I TO %I',
                    existing_constraint.conname,
                    replace(
                        existing_constraint.conname,
                        'materialized_views_',
                        'catalogue_materializations_'
                    )
                );
            END LOOP;
        END $$
        """
    )
    op.alter_column(
        "catalogue_materializations",
        "query_revision_id",
        new_column_name="active_query_revision_id",
    )
    op.alter_column(
        "catalogue_materializations",
        "source_view_reference_id",
        new_column_name="view_reference_id",
    )
    op.add_column(
        "catalogue_materializations",
        sa.Column("query_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "catalogue_materializations",
        sa.Column(
            "bound_ducklake_view_uuid",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    op.execute(
        """
        UPDATE catalogue_materializations AS materialization
        SET query_id = revision.query_id
        FROM catalogue_query_revisions AS revision
        WHERE revision.id = materialization.active_query_revision_id
        """
    )
    op.execute(
        """
        UPDATE catalogue_materializations AS materialization
        SET bound_ducklake_view_uuid = view_reference.ducklake_view_uuid
        FROM catalogue_view_references AS view_reference
        WHERE view_reference.id = materialization.view_reference_id
        """
    )

    op.create_foreign_key(
        "fk_catalogue_materializations_query",
        "catalogue_materializations",
        "catalogue_queries",
        ["query_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_catalogue_materializations_active_query_revision",
        "catalogue_materializations",
        "catalogue_query_revisions",
        ["query_id", "active_query_revision_id"],
        ["query_id", "id"],
    )
    op.create_foreign_key(
        "fk_catalogue_materializations_view_reference",
        "catalogue_materializations",
        "catalogue_view_references",
        ["view_reference_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_one_source",
        "catalogue_materializations",
        "(query_id IS NOT NULL) <> (view_reference_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_source_shape",
        "catalogue_materializations",
        "(query_id IS NOT NULL AND active_query_revision_id IS NOT NULL "
        "AND view_reference_id IS NULL AND bound_ducklake_view_uuid IS NULL) "
        "OR (query_id IS NULL AND active_query_revision_id IS NULL "
        "AND view_reference_id IS NOT NULL AND bound_ducklake_view_uuid IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_refresh_mode",
        "catalogue_materializations",
        "refresh_mode IN ('full', 'scope_incremental')",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_document_scope",
        "catalogue_materializations",
        "scope_kind IS NULL OR scope_kind = 'document'",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_mode_state",
        "catalogue_materializations",
        "(refresh_mode = 'full' AND scope_kind IS NULL AND scope_column IS NULL "
        "AND activation_snapshot IS NULL AND NOT live_enabled AND NOT backfill_enabled) "
        "OR (refresh_mode = 'scope_incremental' "
        "AND active_query_revision_id IS NOT NULL AND scope_kind = 'document' "
        "AND scope_column IS NOT NULL AND activation_snapshot IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_positive_backfill_rate",
        "catalogue_materializations",
        "backfill_scopes_per_minute > 0",
    )
    op.create_index(
        "ix_catalogue_materializations_archived_at",
        "catalogue_materializations",
        ["archived_at"],
    )
    op.create_index(
        "uq_catalogue_materializations_active_query",
        "catalogue_materializations",
        ["query_id"],
        unique=True,
        postgresql_where=sa.text("query_id IS NOT NULL AND archived_at IS NULL"),
    )
    op.create_index(
        "uq_catalogue_materializations_active_view",
        "catalogue_materializations",
        ["view_reference_id"],
        unique=True,
        postgresql_where=sa.text(
            "view_reference_id IS NOT NULL AND archived_at IS NULL"
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Catalogue materializations are a direct greenfield contract and are not downgraded."
    )
