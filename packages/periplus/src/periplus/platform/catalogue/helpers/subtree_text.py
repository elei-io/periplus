"""One declaration owns subtree-text installation and discovery documentation."""
from periplus.platform.catalogue.public import CatalogueObject

SUBTREE_TEXT = CatalogueObject(
    kind="table_macro",
    schema="content",
    name="subtree_text",
    resource="helpers/subtree_text.sql",
    columns=("text", "truncated", "total_chars", "element_count"),
    arguments_sql="'missing-content', 0",
    parameters=(("source_content_id", "VARCHAR"), ("root_element_index", "INTEGER"),
                ("max_chars", "BIGINT = 20000"), ("max_elements", "BIGINT = 10000")),
    comment="Retrieve faithful DOM text from one immutable HTML subtree in document order.",
    column_comments=(
        ("text", "Prefix of the subtree text, preserving existing whitespace."),
        ("truncated", "True when max_chars omitted part of the text."),
        ("total_chars", "Complete subtree character count before truncation."),
        ("element_count", "Number of elements in the selected subtree."),
    ),
    requires_relations=frozenset({"material.html_elements"}),
    notes=(
        "Pass a content_id and element_index from the same retained content; never combine a fixed element index with an arbitrary latest observation.",
        "Preserves direct text and descendant tails in DOM order; excludes the root's tail. No trimming, whitespace collapsing, inserted separators, CSS visibility filtering, deduplication or summarization. Script/style text remains included.",
        "Missing content or root returns zero rows; an existing empty subtree returns one row with empty text. NULL content_id has no matching root.",
        "max_chars must be an integer from 0 to 100000 (default 20000). Truncation is explicit. max_elements must be an integer from 1 to 10000 (default 10000); a larger subtree raises an error instead of returning incomplete text.",
        "Select a meaningful bounded paragraph, code block, section or listing root. This extracts faithful source text, not a cleaned article or an inferred business record.",
    ),
    errors=(
        "element_index must be non-negative",
        "max_chars must be an integer from 0 to 100000",
        "max_elements must be an integer from 1 to 10000",
        "subtree exceeds max_elements; select a smaller root",
    ),
    examples=(
        "SELECT * FROM content.subtree_text(?, ?, max_chars := 20000)",
        "WITH roots AS (SELECT content_id, element_index FROM content.html_element WHERE content_id = ? AND tag = 'pre' ORDER BY element_index LIMIT 5) SELECT r.content_id, r.element_index, t.* FROM roots r, LATERAL content.subtree_text(r.content_id, r.element_index) t",
    ),
)
