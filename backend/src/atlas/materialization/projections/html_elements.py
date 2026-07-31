"""Append-only structural HTML projection."""

from __future__ import annotations

import pyarrow as pa

from atlas.materialization.document_projection import (
    VisitBatchContext,
    table_from_rows,
)
from atlas.materialization.registry import (
    PartitionTransform,
    ProjectionColumn,
    ProjectionSpec,
)
from atlas.platform.catalogue.schema_types import MapType

_NAMESPACE_NAMES = {
    None: "NONE",
    "http://www.w3.org/1999/xhtml": "HTML",
    "http://www.w3.org/2000/svg": "SVG",
    "http://www.w3.org/1998/Math/MathML": "MATHML",
}


def project(context: VisitBatchContext) -> pa.Table:
    rows: list[tuple[object, ...]] = []
    for content_sha256 in sorted(context.content_output_hashes):
        for element in context.parsed_elements_by_content.get(
            content_sha256,
            (),
        ):
            rows.append(
                (
                    content_sha256,
                    element.element_index,
                    element.parent_index,
                    element.subtree_end_index,
                    element.depth,
                    element.child_index,
                    element.tag.lower(),
                    _NAMESPACE_NAMES.get(
                        element.namespace_uri,
                        element.namespace_uri or "NONE",
                    ),
                    list(element.attributes.items()),
                    element.text_direct,
                    element.text_tail,
                )
            )
    return table_from_rows(PROJECTION.arrow_schema, rows)


PROJECTION = ProjectionSpec(
    name="html_elements",
    ownership_grain="content",
    columns=(
        ProjectionColumn(
            "content_sha256", pa.string(), "VARCHAR",
            "Identity of projected immutable HTML bytes.", False,
        ),
        ProjectionColumn(
            "element_index", pa.int32(), "INTEGER",
            "Zero-based depth-first document position.", False,
        ),
        ProjectionColumn(
            "parent_index", pa.int32(), "INTEGER",
            "Parent element index, null for the root.",
        ),
        ProjectionColumn(
            "subtree_end_index", pa.int32(), "INTEGER",
            "Exclusive end of this element subtree.", False,
        ),
        ProjectionColumn(
            "depth", pa.int32(), "INTEGER",
            "Element depth from the root.", False,
        ),
        ProjectionColumn(
            "child_index", pa.int32(), "INTEGER",
            "Zero-based position among element siblings.", False,
        ),
        ProjectionColumn(
            "tag", pa.string(), "VARCHAR",
            "Normalized local tag name.", False,
        ),
        ProjectionColumn(
            "namespace", pa.string(), "VARCHAR",
            "Normalized element namespace.", False,
        ),
        ProjectionColumn(
            "attributes",
            pa.map_(pa.string(), pa.string()),
            MapType("VARCHAR", "VARCHAR"),
            "Attribute names and string values.",
            False,
        ),
        ProjectionColumn(
            "text_direct", pa.string(), "VARCHAR",
            "Text directly inside this element before child elements.", False,
        ),
        ProjectionColumn(
            "text_tail", pa.string(), "VARCHAR",
            "Text following this element within its parent.", False,
        ),
    ),
    partitioning=(PartitionTransform("bucket", "content_sha256", buckets=8),),
    sort_order=("content_sha256 ASC", "element_index ASC"),
    projector=project,
    description="Append-only structural projections of immutable HTML content.",
    identity_columns=("content_sha256", "element_index"),
    content_presence_predicate="element_index = 0",
)
