"""Target, range and publication control for ClickHouse rebuilds."""
# Keep historical DDL independent of current ORM models and their schemas.
from alembic import op
revision = '20260915_0021'
down_revision = '20260915_0020'
branch_labels = None
depends_on = None


def upgrade():
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
    op.execute("""CREATE TABLE material_publications (
    api_version VARCHAR(32) NOT NULL,
    build_id UUID NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (api_version),
    FOREIGN KEY(build_id) REFERENCES material_builds (id)
)""")


def downgrade():
    for name in ("material_publications", "material_build_ranges", "material_builds"):
        op.drop_table(name)
