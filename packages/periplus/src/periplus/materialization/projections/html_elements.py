"""Content-owned elements with exact descendant text prepared once."""
import pyarrow as pa
from periplus.platform.catalogue.schema_types import MapType
from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.registry import PartitionTransform, ProjectionColumn, ProjectionSpec


def project(context: VisitBatchContext) -> pa.Table:
    def rows():
        for content_id in sorted(context.content_output_hashes):
            nodes = context.parsed_nodes_by_content[content_id]
            elements = context.parsed_elements_by_content[content_id]
            prefix = [0]
            pieces = []
            for node in nodes:
                value = (node.value or "") if node.node_type == "text" else ""
                pieces.append(value)
                prefix.append(prefix[-1] + len(value))
            text = "".join(pieces)
            element_ids = {element.element_index for element in elements}
            depths = {}
            siblings = {}
            for element in elements:
                node = nodes[element.element_index]
                parent = node.parent_index
                while parent is not None and parent not in element_ids:
                    parent = nodes[parent].parent_index
                depth = 0 if parent is None else depths[parent] + 1
                depths[node.node_index] = depth
                sibling = siblings.get(parent, 0)
                siblings[parent] = sibling + 1
                start, end = prefix[node.node_index], prefix[node.subtree_end_index]
                yield (content_id, node.node_index, parent, node.subtree_end_index,
                       sibling, depth, element.tag, element.namespace_uri,
                       element.attributes, element.text_direct, text[start:end], start, end)
    return table_from_rows(PROJECTION.arrow_schema, rows())


PROJECTION = ProjectionSpec(
    name="html_elements", ownership_grain="content",
    columns=(
        ProjectionColumn("content_sha256", pa.string(), "VARCHAR", "Immutable source identity.", False),
        ProjectionColumn("node_index", pa.int32(), "INTEGER", "Element position in parsed preorder; gaps are intentional.", False),
        ProjectionColumn("parent_index", pa.int32(), "INTEGER", "Nearest enclosing element; null for document element."),
        ProjectionColumn("subtree_end_index", pa.int32(), "INTEGER", "Exclusive parsed-preorder subtree boundary.", False),
        ProjectionColumn("sibling_index", pa.int32(), "INTEGER", "Position among projected element siblings.", False),
        ProjectionColumn("depth", pa.int32(), "INTEGER", "Element-parent depth; document element is zero.", False),
        ProjectionColumn("tag", pa.string(), "VARCHAR", "Parsed local name, preserving foreign-content case.", False),
        ProjectionColumn("namespace", pa.string(), "VARCHAR", "Namespace URI."),
        ProjectionColumn("attributes", pa.map_(pa.string(), pa.string()), MapType("VARCHAR", "VARCHAR"), "Parsed attributes.", False),
        ProjectionColumn("text_direct", pa.string(), "VARCHAR", "Immediate text children without normalization.", False),
        ProjectionColumn("text", pa.string(), "VARCHAR", "Descendant parsed text including template fragments; no added separators.", False),
        ProjectionColumn("text_start", pa.int64(), "BIGINT", "Internal code-point offset in document text.", False),
        ProjectionColumn("text_end", pa.int64(), "BIGINT", "Internal exclusive code-point offset in document text.", False),
    ),
    partitioning=(PartitionTransform("bucket", "content_sha256", buckets=8),),
    sort_order=("content_sha256 ASC", "node_index ASC"), projector=project,
    description="Parsed elements with exact complete descendant and direct text.",
    identity_columns=("content_sha256", "node_index"),
    content_presence_predicate="parent_index IS NULL",
    implementation_dependencies=("periplus.materialization.dom.nodes", "periplus.materialization.dom.lexbor"),
    validation_queries=(
        "SELECT count(*) FROM material.html_elements WHERE subtree_end_index <= node_index OR text_start < 0 OR text_end < text_start OR length(text) <> text_end - text_start",
    ),
)
