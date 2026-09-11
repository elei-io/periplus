"""Page discovery contract; matching and ranking are owned by Periplus."""

from periplus.platform.catalogue.public import CatalogueObject

SEARCH = CatalogueObject(
    kind="table_macro",
    name="search",
    resource="helpers/search.sql",
    columns=("content_id", "title", "url", "snippet", "score"),
    arguments_sql="'missing-content'",
    parameters=(("query", "VARCHAR"),),
    comment="Find up to 100 relevant unique contents, with a representative capture URL.",
    column_comments=(
        ("content_id", "Retained HTML content identity; one result per content."),
        ("title", "First parsed HTML title in document order; NULL when absent."),
        (
            "url",
            "Effective URL of the newest capture, falling back to requested URL; capture ID breaks time ties.",
        ),
        (
            "snippet",
            "At most 240 characters from a matching field, with normalized whitespace; may begin or end mid-word.",
        ),
        (
            "score",
            "Relative relevance within this query; higher ranks first. Ranking may evolve.",
        ),
    ),
    requires_relations=frozenset({"material.prose", "material.html_nodes"}),
    notes=(
        "Initial matching is literal substring search across body prose, HTML titles and meta name=description content. No wildcard syntax: percent, underscore, quotes and backslashes are literal. No stemming, token AND, semantic search or JSON-LD field selection.",
        "Query and fields collapse ASCII whitespace runs to one space, trim spaces, normalize NFC and use Unicode lowercasing. This is not full case folding or accent removal. NULL, empty and whitespace-only queries return no rows. Queries longer than 256 characters raise an error.",
        "Initial scores use the strongest matching field: title 6, description 3, body 1. Repeated declarations and captures do not increase the score. Snippets use the strongest matching field, then source node order to break ties; start up to 60 characters before the match.",
        "Results are ordered by score descending, then content_id ascending and limited to 100 before caller joins. Use an outer ORDER BY to retain ordering in composed SQL. Scores and relevance policy may evolve.",
        "Body coverage uses internal prose and excludes script/style/template/noscript. This discovery scope cannot safely accelerate arbitrary HTML text predicates. Search currently scans fields; its result cap does not bound scan cost.",
    ),
    errors=("search query must be at most 256 characters",),
    examples=(
        "SELECT * FROM search('monkeys in the zoo')",
        "SELECT * FROM search(?) ORDER BY score DESC, content_id",
    ),
)
