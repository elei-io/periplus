"""Visit membership committed atomically with every registered projection's output."""
import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.registry import PartitionTransform, ProjectionColumn, ProjectionSpec


def project(context: VisitBatchContext) -> pa.Table:
    # All selected visits participate, including failed captures and non-HTML
    # documents. Zero applicable structural rows is a completed projection result.
    return table_from_rows(PROJECTION.arrow_schema, [
        (str(visit_id), finished_at)
        for visit_id, _document_id, _url, finished_at in context.visits
    ])


PROJECTION = ProjectionSpec(
    name="visit_readiness",
    ownership_grain="visit",
    columns=(
        ProjectionColumn("visit_id", pa.string(), "UUID", "Observation processed by this projection generation.", False),
        ProjectionColumn("finished_at", pa.timestamp("us", tz="UTC"), "TIMESTAMPTZ",
                         "Immutable observation completion time used for partitioning.", False),
    ),
    partitioning=(PartitionTransform("month", "finished_at"),),
    sort_order=("visit_id ASC",),
    projector=project,
    description="Per-observation processing proof; files commit atomically with all registered projection outputs.",
    identity_columns=("visit_id",),
    validation_queries=("""
        SELECT count(*) FROM material.visit_readiness proof
        LEFT JOIN ingest.visits visit USING (visit_id)
        WHERE visit.visit_id IS NULL OR proof.finished_at <> visit.finished_at
    """,),
)
