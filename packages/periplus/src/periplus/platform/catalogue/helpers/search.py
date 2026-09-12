"""Query-API discovery; the stored macro provides only the typed signature."""

from periplus.platform.catalogue.public import CatalogueObject

SEARCH = CatalogueObject(
    kind="table_macro",
    name="search",
    resource="helpers/search.sql",
    columns=("content_id", "matches", "score"),
    arguments_sql="'missing-content'",
    parameters=(("query", "VARCHAR"),),
    comment="Query-API search: up to 100 unique contents with matched snippets and node IDs.",
    column_comments=(
        (
            "content_id",
            "Retained HTML content identity; one result per content regardless of capture count.",
        ),
        (
            "matches",
            "Up to three {snippet,node_indexes} matches in document order; node IDs identify contributing parsed text nodes.",
        ),
        (
            "score",
            "Plain search: sum of distinct query-term frequencies. Phrase search: matching phrase starts. Ranking may evolve.",
        ),
    ),
    requires_relations=frozenset(
        {"material.posting", "material.html_nodes"}
    ),
    notes=(
        "Executed by the Periplus query API; direct DuckDB execution raises an error. The catalogue macro exposes the typed signature for discovery and DESCRIBE. Ordinary HTML SQL remains portable.",
        "Query and index use pinned ICU root-locale word boundaries, case folding and NFC. Plain queries require every distinct token. One fully double-quoted query requires consecutive token positions. No stemming, substring, prefix, wildcard or semantic expansion. Punctuation is not a token; percent and underscore are not wildcard operators.",
        "Every parsed text-node location is indexed, including title, script, style, template and noscript. Attribute values, meta descriptions and comments are excluded. No CSS visibility inference.",
        "Document positions leave gaps at structural boundaries. A documented set of HTML inline elements is transparent, allowing words split across text nodes. All other elements break token/phrase runs on entry and exit, including custom and foreign elements.",
        "Snippets preserve case, normalize NFC and collapse whitespace, at most 240 characters. Node IDs identify matching occurrences, not surrounding context. Match lists are representative, not exhaustive.",
        "NULL, empty and nonword-only queries return no rows. At most 256 characters and 32 tokens per query; at most four constant search calls per SQL request. Row-dependent search arguments are rejected.",
        "Results rank by score descending then content_id ascending, capped at 100 before caller joins. Apply an outer ORDER BY when composing SQL. Phrase matching happens before this cap. Storage scan cost is not bounded by the result limit.",
    ),
    errors=(
        "search() requires the Periplus query API",
    ),
    examples=(
        "SELECT * FROM search('monkeys zoo') ORDER BY score DESC, content_id",
        "SELECT * FROM search('\"monkeys in the zoo\"')",
        "SELECT s.content_id, m.snippet, m.node_indexes FROM search(?) s, unnest(s.matches) AS matches(m)",
    ),
)
