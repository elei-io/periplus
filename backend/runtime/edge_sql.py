"""Classification helpers for bounded crawl-graph edge SQL."""

from sqlglot import exp, parse_one

_CATALOGUE_DOM_FUNCTIONS = frozenset(
    {
        "get_attribute",
        "has_attribute",
        "inner_html",
        "readable_text",
        "text_content",
    }
)


def edge_uses_catalogue(sql: str) -> bool:
    """Return whether an edge reads anything beyond its current page package."""

    statement = parse_one(sql, dialect="duckdb")
    if any(
        function.name.lower() in _CATALOGUE_DOM_FUNCTIONS
        for function in statement.find_all(exp.Func)
    ):
        return True
    cte_names = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    for table in statement.find_all(exp.Table):
        if table.name.lower() in cte_names and not table.db:
            continue
        if table.db.lower() == "edge" and table.name.lower() == "page_links":
            continue
        return True
    return False
