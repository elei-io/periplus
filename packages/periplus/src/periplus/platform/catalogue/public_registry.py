"""Authoritative manifest for the narrow public evidence catalogue."""

from periplus.platform.catalogue.public import CatalogueObject
from periplus.platform.catalogue.helpers import HELPERS


def _comments(
    *items: tuple[str, str],
) -> tuple[tuple[str, str], ...]:
    return items


def _view(name, columns, comment, requires=()):
    return CatalogueObject(
        "view", name, f"views/{name}.sql", tuple(column for column, _ in columns),
        comment=comment, column_comments=columns, requires_relations=frozenset(requires),
    )


_TREE_COLUMNS = (
    ("content_id", "SHA-256 identity of captured bytes."),
    ("node_index", "Zero-based depth-first node position, scoped to the catalogue snapshot."),
    ("parent_index", "Parent node position; null for the document root."),
    ("subtree_end_index", "Exclusive end of this node's subtree."),
    ("sibling_index", "Zero-based position among all sibling nodes."),
)

PUBLIC_OBJECTS = (
    _view("capture", (
        ("capture_id", "Acquisition identity with retained content."),
        ("requested_url", "Normalized requested URL."),
        ("effective_url", "Final URL after navigation."),
        ("captured_at", "Time the page content was captured."),
        ("http_status_code", "HTTP response status when known; retained error bodies qualify."),
        ("content_id", "SHA-256 identity of retained bytes."),
        ("byte_length", "Length of logical bytes before storage compression."),
        ("representation", "Meaning of the captured representation."),
        ("media_type", "Detected media type."),
        ("encoding", "Detected character encoding when meaningful."),
        ("request_ids", "Sorted unique coverage request IDs supplied with this capture; empty until membership evidence arrives."),
    ), "Acquisitions with retained content, including retained HTTP error responses."),
    _view("html_node", (*_TREE_COLUMNS,
        ("node_type", "document, doctype, element, text, comment or processing_instruction."),
        ("name", "Local element/doctype name or processing instruction target."),
        ("namespace", "Namespace URI when applicable."),
        ("value", "Text, comment or processing instruction content."),
    ), "Complete HTML5 parsed document nodes.", ("material.html_nodes",)),
    _view("html_element", (*_TREE_COLUMNS,
        ("tag", "Local element tag name."),
        ("namespace", "Namespace URI when applicable."),
        ("attributes", "Attribute map; namespaced keys use {namespace-uri}local-name."),
        ("text_direct", "Immediate child text concatenated in order, without normalization."),
    ), "HTML elements sharing identity and positions with html_node.", ("material.html_elements",)),
    _view("link_occurrence", (
        ("capture_id", "Capture in which the hyperlink was resolved."),
        ("node_index", "Anchor node position in that capture's content."),
        ("raw_href", "Original parsed href attribute value."),
        ("resolved_url", "Resolved normalized HTTP(S) URL."),
    ), "HTTP(S) anchor occurrences resolved in capture context.", ("material.link_occurrences",)),
    *HELPERS,
)
