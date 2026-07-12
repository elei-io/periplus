"""allow scoped incremental view materializations

Revision ID: 20260712_0040
Revises: 20260712_0039
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260712_0040"
down_revision: str | None = "20260712_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_catalogue_materializations_mode_state",
        "catalogue_materializations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_catalogue_materializations_mode_state",
        "catalogue_materializations",
        "(refresh_mode = 'full' AND scope_kind IS NULL AND scope_column IS NULL "
        "AND activation_snapshot IS NULL AND NOT live_enabled AND NOT backfill_enabled) "
        "OR (refresh_mode = 'scope_incremental' AND scope_kind = 'document' "
        "AND scope_column IS NOT NULL AND activation_snapshot IS NOT NULL)",
    )


def downgrade() -> None:
    raise RuntimeError("Incremental view materializations are not downgraded.")
