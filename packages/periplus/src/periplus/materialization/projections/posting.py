"""One term/content row with frequency, token positions and text-node provenance."""

import pyarrow as pa

from periplus.materialization.document_projection import (
    VisitBatchContext,
    table_from_rows,
)
from periplus.materialization.registry import ProjectionColumn, ProjectionSpec


def project(context: VisitBatchContext) -> pa.Table:
    return table_from_rows(
        PROJECTION.arrow_schema,
        (
            (
                term,
                content_id,
                len(items),
                [item.position for item in items],
                [list(item.node_indexes) for item in items],
            )
            for content_id in sorted(context.content_output_hashes)
            for term, items in sorted(
                context.search_text(content_id).occurrences.items()
            )
        ),
    )


PROJECTION = ProjectionSpec(
    name="posting",
    ownership_grain="content",
    columns=(
        ProjectionColumn(
            "text", pa.string(), "VARCHAR", "Normalized ICU term.", False
        ),
        ProjectionColumn(
            "content_sha256",
            pa.string(),
            "VARCHAR",
            "Immutable content identity.",
            False,
        ),
        ProjectionColumn(
            "frequency",
            pa.int64(),
            "BIGINT",
            "Token occurrences in this content.",
            False,
        ),
        ProjectionColumn(
            "positions",
            pa.list_(pa.int64()),
            "BIGINT[]",
            "Ascending document token ordinals; structural boundaries leave gaps.",
            False,
        ),
        ProjectionColumn(
            "node_indexes",
            pa.list_(pa.list_(pa.int32())),
            "INTEGER[][]",
            "Contributing text-node IDs for each corresponding position.",
            False,
        ),
    ),
    partitioning=(),
    sort_order=("text ASC", "content_sha256 ASC"),
    projector=project,
    description="Complete parsed-text positional index; excludes attributes and comments.",
    identity_columns=("content_sha256", "text"),
    implementation_dependencies=(
        "periplus.materialization.search_text",
        "periplus.materialization.tokenization",
    ),
    validation_queries=(
        "SELECT count(*) FROM material.posting WHERE frequency <= 0 OR frequency IS NULL OR positions IS NULL OR node_indexes IS NULL OR frequency <> len(positions) OR frequency <> len(node_indexes)",
        "SELECT count(*) FROM material.posting WHERE positions <> list_sort(list_distinct(positions)) OR list_min(positions) < 0",
        "SELECT count(*) FROM (SELECT unnest(node_indexes) AS owners FROM material.posting) WHERE owners IS NULL OR len(owners)=0 OR owners <> list_sort(list_distinct(owners))",
        "SELECT count(*) FROM (SELECT content_sha256, unnest(flatten(node_indexes)) node_index FROM material.posting) p ANTI JOIN (SELECT content_sha256,node_index FROM material.html_nodes WHERE node_type='text') n USING(content_sha256,node_index)",
    ),
)
