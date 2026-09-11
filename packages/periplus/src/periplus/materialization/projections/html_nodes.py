"""Complete content-owned parsed HTML nodes."""

import pyarrow as pa
from periplus.platform.catalogue.schema_types import MapType

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.registry import PartitionTransform, ProjectionColumn, ProjectionSpec


def project(context: VisitBatchContext) -> pa.Table:
    elements = {key: {e.element_index: e for e in rows}
                for key, rows in context.parsed_elements_by_content.items()}
    return table_from_rows(
        PROJECTION.arrow_schema,
        (
            (
                content_id,
                node.node_index,
                node.parent_index,
                node.subtree_end_index,
                node.sibling_index,
                node.node_type,
                node.name,
                node.namespace,
                node.value,
                node.depth,
                elements[content_id][node.node_index].attributes if node.node_type == "element" else None,
                elements[content_id][node.node_index].text_direct if node.node_type == "element" else None,
            )
            for content_id in sorted(context.content_output_hashes)
            for node in context.parsed_nodes_by_content.get(content_id, ())
        ),
    )


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
        ProjectionColumn("attributes", pa.map_(pa.string(), pa.string()), MapType("VARCHAR", "VARCHAR"), "Element attributes; null on other nodes."),
        ProjectionColumn("text_direct", pa.string(), "VARCHAR", "Ordered immediate element text; null on other nodes."),
    ),
    partitioning=(PartitionTransform("bucket", "content_sha256", buckets=8),),
    sort_order=("content_sha256 ASC", "node_index ASC"),
    projector=project,
    description="Complete HTML5 document tree, including text and comments.",
    identity_columns=("content_sha256", "node_index"),
    content_presence_predicate="node_index = 0",
)
