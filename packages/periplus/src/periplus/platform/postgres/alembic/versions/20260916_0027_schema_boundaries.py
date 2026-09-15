"""Separate customer control from bounded execution state."""
from alembic import op

revision = "20260916_0027"
down_revision = "20260916_0026"
branch_labels = depends_on = None

TABLES = {
    "collection_results": "control",
    "collections": "control",
    "content_policies": "control",
    "domain_policies": "control",
    "public_access": "control",
    "request_definitions": "control",
    "request_schedules": "control",
    "archive_imports": "state",
    "capture_retirements": "state",
    "frontier_acquisitions": "state",
    "frontier_control": "state",
    "frontier_interests": "state",
    "frontier_outbox": "state",
    "material_batches": "state",
    "material_build_ranges": "state",
    "material_builds": "state",
    "material_publications": "state",
    "query_executions": "state",
    "write_claims": "state",
}


INDEXES = (
    (
        "control",
        "ix_collection_results_collection_id",
        "ix_control_collection_results_collection_id",
    ),
    (
        "state",
        "ix_frontier_acquisitions_domain",
        "ix_state_frontier_acquisitions_domain",
    ),
    (
        "state",
        "ix_frontier_interests_acquisition_id",
        "ix_state_frontier_interests_acquisition_id",
    ),
    (
        "state",
        "ix_frontier_interests_collection_id",
        "ix_state_frontier_interests_collection_id",
    ),
    (
        "state",
        "ix_frontier_outbox_acquisition_id",
        "ix_state_frontier_outbox_acquisition_id",
    ),
    (
        "state",
        "ix_frontier_outbox_collection_id",
        "ix_state_frontier_outbox_collection_id",
    ),
    (
        "state",
        "ix_lake_write_claims_expires_at",
        "ix_state_write_claims_expires_at",
    ),
    (
        "state",
        "ix_material_batches_build_id",
        "ix_state_material_batches_build_id",
    ),
    (
        "state",
        "ix_archive_imports_status",
        "ix_state_archive_imports_status",
    ),
)


def upgrade():
    for schema in ("control", "state"):
        op.execute(f'CREATE SCHEMA {schema}')
    for table, schema in TABLES.items():
        op.execute(f'ALTER TABLE public.{table} SET SCHEMA {schema}')
    for schema, old, new in INDEXES:
        op.execute(f'ALTER INDEX {schema}.{old} RENAME TO {new}')


def downgrade():
    for schema, old, new in INDEXES:
        op.execute(f'ALTER INDEX {schema}.{new} RENAME TO {old}')
    for table, schema in reversed(TABLES.items()):
        op.execute(f'ALTER TABLE {schema}.{table} SET SCHEMA public')
    for schema in ("state", "control"):
        op.execute(f'DROP SCHEMA {schema}')
