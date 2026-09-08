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
    ("depth", "Number of parent edges from the document root; root is zero."),
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
        ("encoding", "Detected character encoding when meaningful."),
        ("request_ids", "Sorted unique coverage request IDs supplied with this capture; empty until membership evidence arrives."),
    ), "Acquisitions with retained HTML, including retained HTML HTTP error responses."),
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
    _view("html_form", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source form node position."),
        ("id", "Declared id attribute."),
        ("name", "Declared name attribute."),
        ("action", "Declared action, without URL resolution or defaults."),
        ("method", "Declared method, without normalization or defaults."),
        ("enctype", "Declared enctype attribute."),
        ("target", "Declared target attribute."),
    ), "HTML form elements with declared attributes.", ("material.html_elements",)),
    _view("html_form_control", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source native form-control node position."),
        ("form_node_index", "Form owner reconstructed from explicit form reference or nearest ancestor; null when unowned."),
        ("tag", "Source input, button, select, textarea, fieldset, output or object tag."),
        ("type", "Declared type attribute, without defaults."),
        ("name", "Declared name attribute."),
        ("value", "Parsed textarea child text, otherwise the declared value attribute; not live state."),
        ("required", "Whether the required attribute is present."),
        ("disabled", "Whether the disabled attribute is present on this element."),
        ("readonly", "Whether the readonly attribute is present."),
        ("multiple", "Whether the multiple attribute is present."),
    ), "Native HTML form controls, including controls without a form owner.",
        ("material.html_nodes", "material.html_elements")),
    _view("html_select_option", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source option node position."),
        ("select_node_index", "Nearest owning select node position."),
        ("option_index", "Zero-based source position within the select, across optgroups."),
        ("value", "Declared value attribute; no text fallback."),
        ("text", "Ordered descendant text without normalization."),
        ("selected", "Whether the selected attribute is present; not live selectedness."),
        ("disabled", "Whether disabled is present on this option; not inherited state."),
    ), "HTML options owned by select elements, including optgroup descendants.",
        ("material.html_nodes", "material.html_elements")),
    _view("html_list", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source ul or ol node position."),
        ("ordered", "True for ol."),
        ("start_number", "Effective ordered-list starting number; null for ul."),
        ("reversed", "True when an ol has the reversed attribute."),
    ), "HTML ordered and unordered lists, including empty lists.", ("material.html_elements",)),
    _view("html_list_item", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source li node position."),
        ("list_node_index", "Direct owning ul or ol node position."),
        ("item_index", "Zero-based position among the list's direct HTML li children."),
        ("ordinal", "Effective ordered-list number after start, reversed and value; null for ul."),
        ("text", "Ordered descendant text excluding nested ul/ol lists; empty for an empty item."),
    ), "Direct HTML list items with source identity and effective numbering.",
        ("material.html_nodes", "material.html_elements")),
    _view("html_jsonld", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source application/ld+json script node position."),
        ("value", "Complete document parsed as DuckDB JSON; SQL null on parse failure."),
        ("parse_error", "Empty JSON-LD script or Invalid JSON syntax; null on successful parsing."),
    ), "Embedded JSON-LD declarations, including invalid scripts, without semantic expansion.",
        ("material.html_nodes", "material.html_elements")),
    _view("html_image", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source img node position."),
        ("src", "Declared src value; relative references remain relative."),
        ("srcset", "Declared srcset value without parsing or candidate selection."),
        ("sizes", "Declared sizes value."),
        ("alt", "Declared alternative text; empty and missing remain distinct."),
        ("width", "Declared width as a source string, not a measured dimension."),
        ("height", "Declared height as a source string, not a measured dimension."),
    ), "HTML img elements with original parsed attributes, including images without src.",
        ("material.html_elements",)),
    _view("html_metadata", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source metadata element node position."),
        ("kind", "Declaration kind: title, meta_name, meta_property, meta_http_equiv, meta_charset, link_rel or html_attribute."),
        ("name", "Declared metadata name or relation token; title, charset and lang use fixed names."),
        ("value", "Parsed declared value without normalization; null when the value attribute is absent."),
    ), "Explicit HTML metadata declarations, preserving source nodes and repeated declarations.",
        ("material.html_nodes", "material.html_elements")),
    _view("html_section", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("heading_node_index", "Heading that starts this passage."),
        ("parent_heading_node_index", "Nearest preceding heading of higher rank; null when absent."),
        ("start_node_index", "Inclusive passage start immediately after the heading subtree."),
        ("end_node_index", "Exclusive end at the next heading of equal/higher rank, or document end."),
    ), "Heading-delimited source passages; inferred ranges, not semantic or CSS sections.",
        ("material.html_nodes", "material.html_elements")),
    _view("html_code", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source code element node position."),
        ("block", "True when the code element has an HTML pre ancestor; not CSS display state."),
        ("text", "Ordered descendant text preserving whitespace and line breaks; empty for empty code."),
    ), "HTML code elements with source identity and complete descendant text.",
        ("material.html_nodes", "material.html_elements")),
    _view("html_heading", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source h1 through h6 node position."),
        ("level", "Declared HTML heading level, from 1 through 6."),
        ("text", "Ordered descendant text without normalization; empty for an empty heading."),
    ), "HTML headings with source identity and complete descendant text.",
        ("material.html_nodes", "material.html_elements")),
    _view("html_table", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("node_index", "Source table node position."),
        ("caption_node_index", "First direct caption node; null when absent."),
        ("caption", "Caption descendant text excluding nested tables; null when absent."),
    ), "HTML tables, including empty and nested tables.", ("material.html_nodes", "material.html_elements")),
    _view("html_table_cell", (
        ("content_id", "SHA-256 identity of captured bytes."),
        ("table_node_index", "Owning table node position."),
        ("row_node_index", "Owning tr node position."),
        ("node_index", "Source td or th node position."),
        ("row_index", "Zero-based row in parsed source order, including empty rows."),
        ("column_index", "Zero-based starting grid column, accounting for spans."),
        ("row_span", "Effective row span, bounded by the source row group."),
        ("column_span", "Effective column span."),
        ("is_header", "True for a th source element."),
        ("text", "Ordered descendant text excluding nested tables; empty for an empty cell."),
    ), "One source HTML cell per row with span-aware grid positions, computed on demand.",
        ("material.html_nodes", "material.html_elements")),
    _view("link", (
        ("capture_id", "Capture in which the hyperlink was resolved."),
        ("node_index", "Anchor node position in that capture's content."),
        ("raw_href", "Original parsed href attribute value."),
        ("resolved_url", "Resolved normalized HTTP(S) URL."),
    ), "HTTP(S) anchor occurrences resolved in capture context.", ("material.link_occurrences",)),
    *HELPERS,
)
