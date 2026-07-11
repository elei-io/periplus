"""enforce materialization mode invariants

Revision ID: 20260711_0035
Revises: 20260711_0034
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260711_0035"
down_revision: str | None = "20260711_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_materialized_views_refresh_mode",
        "materialized_views",
        "refresh_mode IN ('full', 'scope_incremental')",
    )
    op.create_check_constraint(
        "ck_materialized_views_document_scope",
        "materialized_views",
        "scope_kind IS NULL OR scope_kind = 'document'",
    )
    op.create_check_constraint(
        "ck_materialized_views_mode_state",
        "materialized_views",
        "(refresh_mode = 'full' AND scope_kind IS NULL AND scope_column IS NULL "
        "AND activation_snapshot IS NULL AND NOT live_enabled AND NOT backfill_enabled) "
        "OR (refresh_mode = 'scope_incremental' AND query_revision_id IS NOT NULL "
        "AND scope_kind = 'document' AND scope_column IS NOT NULL "
        "AND activation_snapshot IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_materialized_views_positive_backfill_rate",
        "materialized_views",
        "backfill_scopes_per_minute > 0",
    )


def downgrade() -> None:
    raise RuntimeError("Materialization invariants are not downgraded.")
