"""Archive-authoritative corpus; discard disposable old material execution state."""

from alembic import op
import sqlalchemy as sa
from periplus.materialization.rebuilds.models import (
    BuildRecord,
    RangeRecord,
    BatchRecord,
    PublicationRecord,
)
from periplus.crawl.control.collections.models import CollectionResultRecord

from periplus.retention.models import CaptureRetirementRecord

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
    for model in (
        CollectionResultRecord,
        BuildRecord,
        RangeRecord,
        BatchRecord,
        PublicationRecord,
        CaptureRetirementRecord,
    ):
        model.__table__.create(op.get_bind())


def downgrade():
    raise RuntimeError(
        "Greenfield contract change; restore a backup to return to the previous system"
    )
