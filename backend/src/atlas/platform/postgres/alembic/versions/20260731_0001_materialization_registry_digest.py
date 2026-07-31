"""freeze the materialization registry digest per rebuild

Revision ID: 20260731_0001
Revises: 20260726_0001
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260731_0001"
down_revision: str | None = "20260726_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "materialization_runs",
        sa.Column(
            "registry_digest",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.alter_column(
        "materialization_runs",
        "registry_digest",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("materialization_runs", "registry_digest")
