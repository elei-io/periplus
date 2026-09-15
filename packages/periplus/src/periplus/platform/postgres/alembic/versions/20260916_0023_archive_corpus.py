"""Archive-authoritative corpus; discard disposable old material execution state."""

# Keep historical DDL independent of current ORM models and their schemas.
from alembic import op
import sqlalchemy as sa
revision = "20260916_0023"
down_revision = "20260915_0022"
branch_labels = depends_on = None


def upgrade():
    for name in (
        "material_publications",
        "material_build_ranges",
        "material_builds",
        "materialization_applied_batches",
        "materialization_batches",
        "materialization_state",
        "materialization_runs",
        "retention_objects",
    ):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {name} CASCADE"))
    op.rename_table("lake_write_claims", "write_claims")
    op.execute("""CREATE TABLE collection_results (
    id UUID NOT NULL,
    collection_id UUID NOT NULL,
    capture_id UUID NOT NULL,
    requested_url TEXT NOT NULL,
    mode TEXT NOT NULL,
    outcome TEXT NOT NULL,
    selection_context JSONB NOT NULL,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
    archived_at TIMESTAMP WITH TIME ZONE,
    archive_shard INTEGER,
    archive_sequence BIGINT,
    PRIMARY KEY (id),
    FOREIGN KEY(collection_id) REFERENCES collections (id)
)""")
    op.execute("""CREATE INDEX ix_collection_results_capture ON collection_results (capture_id)""")
    op.execute("""CREATE INDEX ix_collection_results_page ON collection_results (collection_id, recorded_at, id)""")
    op.execute("""CREATE INDEX ix_collection_results_collection_id ON collection_results (collection_id)""")
    op.execute("""CREATE TABLE material_builds (
    id UUID NOT NULL,
    phase VARCHAR(24) NOT NULL,
    recipe VARCHAR(64) NOT NULL,
    manifest_key VARCHAR(256),
    material_database VARCHAR(64) NOT NULL,
    query_database VARCHAR(64) NOT NULL,
    revision INTEGER NOT NULL,
    page_size INTEGER NOT NULL,
    paused BOOLEAN NOT NULL,
    protected BOOLEAN NOT NULL,
    blocker VARCHAR(1000),
    verification_heads JSON,
    verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    drain_after TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    UNIQUE (material_database),
    UNIQUE (query_database)
)""")
    op.execute("""CREATE TABLE material_build_ranges (
    build_id UUID NOT NULL,
    shard INTEGER NOT NULL,
    upper BIGINT NOT NULL,
    cursor BIGINT NOT NULL,
    live_cursor BIGINT NOT NULL,
    processed BIGINT NOT NULL,
    PRIMARY KEY (build_id, shard),
    FOREIGN KEY(build_id) REFERENCES material_builds (id)
)""")
    op.execute("""CREATE TABLE material_batches (
    id UUID NOT NULL,
    build_id UUID NOT NULL,
    shard INTEGER NOT NULL,
    lane VARCHAR(16) NOT NULL,
    start BIGINT NOT NULL,
    "end" BIGINT NOT NULL,
    build_revision INTEGER NOT NULL,
    status VARCHAR(16) NOT NULL,
    published_at TIMESTAMP WITH TIME ZONE,
    owner UUID,
    lease_until TIMESTAMP WITH TIME ZONE,
    worker_id VARCHAR(128),
    attempts INTEGER NOT NULL,
    error VARCHAR(1000),
    PRIMARY KEY (id),
    CONSTRAINT uq_material_active_range UNIQUE (build_id, shard, lane),
    FOREIGN KEY(build_id) REFERENCES material_builds (id)
)""")
    op.execute("""CREATE INDEX ix_material_batches_build_id ON material_batches (build_id)""")
    op.execute("""CREATE TABLE material_publications (
    api_version VARCHAR(32) NOT NULL,
    build_id UUID NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (api_version),
    FOREIGN KEY(build_id) REFERENCES material_builds (id)
)""")
    op.execute("""CREATE TABLE capture_retirements (
    capture_id UUID NOT NULL,
    requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    error TEXT,
    PRIMARY KEY (capture_id)
)""")


def downgrade():
    raise RuntimeError(
        "Greenfield contract change; restore a backup to return to the previous system"
    )
