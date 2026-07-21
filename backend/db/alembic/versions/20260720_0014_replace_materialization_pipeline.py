"""replace scoped materialization pipeline with CDC event consumers

Revision ID: 20260720_0014
Revises: 20260720_0013
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260720_0014"
down_revision: str | None = "20260720_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Atlas is greenfield and materialization state is derived. Carrying old
    # scope/revision/coverage state into the new contract would be incorrect.
    active = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM catalogue_materializations "
            "WHERE archived_at IS NULL"
        )
    ).scalar_one()
    if active:
        raise RuntimeError(
            "Dematerialize or reset the disposable Atlas catalogue before "
            "installing the event-driven materialization contract."
        )
    op.drop_table("catalogue_materializations")
    op.create_table(
        "catalogue_materializations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_sql", sa.Text(), nullable=False),
        sa.Column(
            "view_reference_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source_view_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_table", sa.Text(), nullable=False),
        sa.Column("source_table_id", sa.BigInteger(), nullable=False),
        sa.Column("source_table_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("control_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("source_schema_version", sa.Integer(), nullable=True),
        sa.Column("desired_state", sa.Text(), nullable=False),
        sa.Column("observed_state", sa.Text(), nullable=False),
        sa.Column("nats_consumer_name", sa.Text(), nullable=False),
        sa.Column("refresh_delay_seconds", sa.Float(), nullable=False),
        sa.Column("partition_column", sa.Text(), nullable=True),
        sa.Column("target_table_id", sa.BigInteger(), nullable=True),
        sa.Column("ducklake_table_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bootstrap_snapshot", sa.BigInteger(), nullable=True),
        sa.Column("processed_snapshot", sa.BigInteger(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "desired_state IN ('live', 'paused', 'deleting')",
            name="ck_catalogue_materializations_desired_state",
        ),
        sa.CheckConstraint(
            "observed_state IN "
            "('creating', 'live', 'paused', 'deleting', 'blocked_schema', 'failed')",
            name="ck_catalogue_materializations_observed_state",
        ),
        sa.CheckConstraint(
            "refresh_delay_seconds >= 0",
            name="ck_catalogue_materializations_refresh_delay",
        ),
        sa.ForeignKeyConstraint(
            ["view_reference_id"], ["catalogue_view_references.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nats_consumer_name"),
    )
    op.create_index(
        "ix_catalogue_materializations_archived_at",
        "catalogue_materializations",
        ["archived_at"],
    )
    op.create_index(
        "uq_catalogue_materializations_active_name",
        "catalogue_materializations",
        ["name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "uq_catalogue_materializations_active_view",
        "catalogue_materializations",
        ["view_reference_id"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("catalogue_materializations")
