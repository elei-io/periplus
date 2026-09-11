"""Content-owned searchable body text, derived from the shared parsed tree."""
from __future__ import annotations

import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.dom.nodes import NodeRow
from periplus.materialization.registry import ProjectionColumn, ProjectionSpec

from periplus.materialization.search_text import body_parts


def _body_text(nodes: tuple[NodeRow, ...]) -> str:
    return " ".join("".join(text for text, _ in body_parts(nodes)).split())


def project(context: VisitBatchContext) -> pa.Table:
    return table_from_rows(PROJECTION.arrow_schema, (
        (content_id, context.search_text(content_id).prose)
        for content_id in sorted(context.content_output_hashes)
    ))


PROJECTION = ProjectionSpec(
    name="prose",
    ownership_grain="content",
    columns=(
        ProjectionColumn("content_sha256", pa.string(), "VARCHAR",
                         "Identity of projected immutable HTML bytes.", False),
        ProjectionColumn("text", pa.string(), "VARCHAR",
                         "Body text with normalized whitespace and source block separators.", False),
    ),
    partitioning=(),
    sort_order=("content_sha256 ASC",),
    projector=project,
    description="One searchable body-text row per unique HTML content; no CSS visibility inference.",
    identity_columns=("content_sha256",),
)
