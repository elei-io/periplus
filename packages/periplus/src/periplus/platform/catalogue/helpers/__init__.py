"""Portable public search over immutable page-word postings."""
from periplus.platform.catalogue.public import CatalogueObject

HELPERS = (
    CatalogueObject(
        kind="table_macro", name="html_search", resource="helpers/html_search.sql",
        columns=("content_id", "node_indexes", "score"), arguments_sql="['robot']",
        parameters=(("terms", "VARCHAR[]: at most 32 Unicode case-folded, NFC-normalized word keys"),),
        comment="Find content matching any requested page word, with containing elements and a simple word-coverage score.",
        column_comments=(
            ("content_id", "SHA-256 identity of captured bytes matching at least one requested term."),
            ("node_indexes", "Sorted distinct element indexes containing any matched page word, including enclosing ancestors."),
            ("score", "Number of distinct requested term keys matched by this content, as DOUBLE; not frequency, BM25 or phrase relevance."),
        ),
        notes=(
            "Inputs are exact index keys, not a free-text query string. ICU page words are case folded and NFC normalized at materialization.",
            "Duplicate, null and empty term keys do not add score. An empty or null list returns no rows.",
            "Matching is any-word. Node indexes are the union of matching elements; a listed element need not contain every matched term.",
            "Use ORDER BY score DESC, content_id and LIMIT for deterministic top results. Query API resource and truncation limits still apply.",
        ),
        examples=("SELECT * FROM html_search(['robot', 'science']) ORDER BY score DESC, content_id LIMIT 20",),
        errors=("html_search accepts at most 32 term keys",),
        requires_relations=frozenset({"material.html_terms"}),
        content_local=True,
    ),
)
