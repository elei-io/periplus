"""Content-owned searchable body text, derived from the shared parsed tree."""
from __future__ import annotations

import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.dom.nodes import NodeRow
from periplus.materialization.registry import ProjectionColumn, ProjectionSpec

_HTML = "http://www.w3.org/1999/xhtml"
_EXCLUDED = frozenset({"script", "style", "template", "noscript"})
# Source-tag boundaries, independent of CSS display or visibility.
_BLOCKS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "details",
    "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr",
    "li", "main", "menu", "nav", "ol", "p", "pre", "section", "summary",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
})


def _body_text(nodes: tuple[NodeRow, ...]) -> str:
    body = next((n for n in nodes if n.node_type == "element"
                 and n.namespace == _HTML and n.name == "body"), None)
    if body is None:
        return ""
    parts: list[str] = []
    ends: list[int] = []
    skip_until = body.node_index + 1
    for node in nodes:
        if node.node_index < skip_until:
            continue
        if node.node_index >= body.subtree_end_index:
            break
        while ends and ends[-1] <= node.node_index:
            parts.append(" ")
            ends.pop()
        if node.node_type == "element":
            if node.name in _EXCLUDED:
                skip_until = node.subtree_end_index
                continue
            if node.namespace == _HTML and node.name in _BLOCKS:
                parts.append(" ")
                ends.append(node.subtree_end_index)
        elif node.node_type == "text":
            parts.append(node.value or "")
    return " ".join("".join(parts).split())


def project(context: VisitBatchContext) -> pa.Table:
    return table_from_rows(PROJECTION.arrow_schema, (
        (content_id, _body_text(context.parsed_nodes_by_content[content_id]))
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
