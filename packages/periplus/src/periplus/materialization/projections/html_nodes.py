"""Complete content-owned parsed HTML nodes."""
from dataclasses import astuple

import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.registry import PartitionTransform, ProjectionColumn, ProjectionSpec


def project(context: VisitBatchContext) -> pa.Table:
    return table_from_rows(PROJECTION.arrow_schema, [
        (content_id, *astuple(node))
        for content_id in sorted(context.content_output_hashes)
        for node in context.parsed_nodes_by_content.get(content_id, ())
    ])


PROJECTION = ProjectionSpec(
    name="html_nodes",
    ownership_grain="content",
    columns=(
        ProjectionColumn("content_sha256", pa.string(), "VARCHAR", "Identity of retained HTML bytes.", False),
        ProjectionColumn("node_index", pa.int32(), "INTEGER", "Depth-first node position.", False),
        ProjectionColumn("parent_index", pa.int32(), "INTEGER", "Parent node, null for document root."),
        ProjectionColumn("subtree_end_index", pa.int32(), "INTEGER", "Exclusive subtree end.", False),
        ProjectionColumn("sibling_index", pa.int32(), "INTEGER", "Position among all sibling nodes.", False),
        ProjectionColumn("node_type", pa.string(), "VARCHAR", "HTML5 parsed node kind.", False),
        ProjectionColumn("name", pa.string(), "VARCHAR", "Local element name, doctype name or instruction target."),
        ProjectionColumn("namespace", pa.string(), "VARCHAR", "Namespace URI when applicable."),
        ProjectionColumn("value", pa.string(), "VARCHAR", "Text, comment or instruction value."),
        ProjectionColumn("depth", pa.int32(), "INTEGER", "Number of parent edges from the document root; root is zero.", False),
    ),
    partitioning=(PartitionTransform("bucket", "content_sha256", buckets=8),),
    sort_order=("content_sha256 ASC", "node_index ASC"),
    projector=project,
    description="Complete HTML5 document tree, including text and comments.",
    identity_columns=("content_sha256", "node_index"),
    content_presence_predicate="node_index = 0",
)
