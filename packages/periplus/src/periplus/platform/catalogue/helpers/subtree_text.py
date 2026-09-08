"""One declaration owns subtree-text installation and discovery documentation."""
from periplus.platform.catalogue.public import CatalogueObject

SUBTREE_TEXT = CatalogueObject(
    kind="table_macro",
    schema="public_v1",
    name="subtree_text",
    resource="helpers/subtree_text.sql",
    columns=("text", "truncated", "total_chars", "node_count"),
    arguments_sql="'missing-content', 0",
    parameters=(("source_content_id", "VARCHAR"), ("root_node_index", "INTEGER"),
                ("max_chars", "BIGINT = 20000"), ("max_nodes", "BIGINT = 10000")),
    comment="Retrieve faithful DOM text from one immutable HTML subtree in document order.",
    column_comments=(
        ("text", "Prefix of the subtree text, preserving existing whitespace."),
        ("truncated", "True when max_chars omitted part of the text."),
        ("total_chars", "Complete subtree character count before truncation."),
        ("node_count", "Number of nodes in the selected subtree."),
    ),
    requires_relations=frozenset({"material.html_nodes"}),
    notes=(
        "Pass a content_id and node_index from the same retained content; never combine a fixed element index with an arbitrary latest observation.",
        "Preserves descendant text nodes in document order; excludes comments and text outside the root subtree. No trimming, whitespace collapsing, inserted separators, CSS visibility filtering, deduplication or summarization. Script/style text remains included.",
        "Missing content or root returns zero rows; an existing empty subtree returns one row with empty text. NULL content_id has no matching root.",
        "max_chars must be an integer from 0 to 100000 (default 20000). Truncation is explicit. max_nodes must be an integer from 1 to 10000 (default 10000); a larger subtree raises an error instead of returning incomplete text.",
        "Select a meaningful bounded paragraph, code block, section or listing root. This extracts faithful source text, not a cleaned article or an inferred business record.",
    ),
    errors=(
        "node_index must be non-negative",
        "max_chars must be an integer from 0 to 100000",
        "max_nodes must be an integer from 1 to 10000",
        "subtree exceeds max_nodes; select a smaller root",
    ),
    examples=(
        "SELECT * FROM public_v1.subtree_text(?, ?, max_chars := 20000)",
        "WITH roots AS (SELECT content_id, node_index FROM public_v1.html_element WHERE content_id = ? AND tag = 'pre' ORDER BY node_index LIMIT 5) SELECT r.content_id, r.node_index, t.* FROM roots r, LATERAL public_v1.subtree_text(r.content_id, r.node_index) t",
    ),
)
