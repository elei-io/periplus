"""Authoritative manifest for the narrow public evidence catalogue."""

from periplus.platform.catalogue.public import CatalogueObject
from periplus.platform.catalogue.helpers import HELPERS


def _view(name, columns, comment, requires=(), *, content_local=False):
    return CatalogueObject(
        "view", name, f"views/{name}.sql", tuple(column for column, _ in columns),
        comment=comment, column_comments=columns, requires_relations=frozenset(requires),
        content_local=content_local,
    )


_TREE_COLUMNS = (
    ("content_id", "SHA-256 identity of captured bytes."),
    ("node_index", "Zero-based depth-first node position, scoped to the catalogue snapshot."),
    ("parent_index", "Nearest parent element position; null for the document element."),
    ("subtree_end_index", "Exclusive end of this node's subtree."),
    ("sibling_index", "Zero-based position among projected element siblings."),
    ("depth", "Element-parent depth; document element is zero."),
)

INTERNAL_OBJECTS: tuple[CatalogueObject, ...] = ()

PUBLIC_OBJECTS = (
    _view("capture", (
        ("capture_id", "Acquisition identity with retained content."),
        ("page_url", "Requested page URL; references page.url."),
        ("effective_url", "Final URL after navigation."),
        ("captured_at", "Time the page content was captured."),
        ("http_status_code", "HTTP response status when known; retained error bodies qualify."),
        ("content_id", "SHA-256 identity of retained bytes."),
        ("byte_length", "Length of logical bytes before storage compression."),
        ("encoding", "Detected character encoding when meaningful."),
    ), "Acquisitions with retained HTML, including retained HTML HTTP error responses."),
    _view("html_element", (*_TREE_COLUMNS,
        ("tag", "Local element tag name."),
        ("namespace", "Namespace URI when applicable."),
        ("attributes", "Attribute map; namespaced keys use {namespace-uri}local-name."),
        ("text_direct", "Immediate child text concatenated in order, without normalization."),
        ("text", "All descendant text nodes concatenated in document order; empty when absent. Preserves whitespace, includes template fragments and script/style/title text, inserts no separators, ignores comments and CSS visibility."),
    ), "Parsed HTML elements with materialized complete text.", ("material.html_elements",), content_local=True),
    _view("html_jsonld", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source application/ld+json script node position."),
        ("value", "Complete document parsed as DuckDB JSON; SQL null on parse failure."),
        ("parse_error", "Empty JSON-LD script or Invalid JSON syntax; null on successful parsing."),
    ), "Materialized embedded JSON-LD declarations, including invalid scripts, without semantic expansion.",
        ("material.html_jsonld",), content_local=True),
    _view("html_metadata", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source metadata element node position."),
        ("kind", "Declaration kind: title, meta_name, meta_property, meta_http_equiv, meta_charset, link_rel or html_attribute."),
        ("name", "Declared metadata name or relation token; title, charset and lang use fixed names."),
        ("value", "Parsed declared value without normalization; null when the value attribute is absent."),
    ), "Explicit HTML metadata declarations, preserving source nodes and repeated declarations.",
        ("material.html_elements",), content_local=True),
    _view("link", (
        ("capture_id", "Capture in which the hyperlink was resolved."),
        ("node_index", "Anchor node position in that capture's content."),
        ("target_url", "Resolved normalized HTTP(S) destination; references page.url."),
        ("raw_href", "Original parsed href attribute value."),
    ), "HTTP(S) anchor occurrences resolved in capture context.", ("material.link_occurrences",)),
    _view("page", (("url", "Normalized URL identity, including uncaptured link destinations."),),
        "Distinct requested, effective and linked URLs in retained HTML evidence.",
        ("material.link_occurrences",)),
    *HELPERS,
)
