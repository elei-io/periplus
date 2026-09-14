"""Compare installed view semantics without CREATE syntax or identifier quoting."""

from sqlglot import exp, parse_one


def normalized_view(sql: str) -> exp.Expression:
    statement = parse_one(sql, read="duckdb").expression
    for identifier in statement.find_all(exp.Identifier):
        identifier.set("quoted", False)
        identifier.set("this", identifier.this.lower())
    # DuckDB serializes explicit INNER JOIN and extra parentheses. Compare ASTs,
    # not reparsed unparenthesized SQL: operator grouping must remain authoritative.
    for parenthesis in list(statement.find_all(exp.Paren)):
        parenthesis.replace(parenthesis.this)
    for join in statement.find_all(exp.Join):
        if join.args.get("kind") == "INNER":
            join.set("kind", None)
    return statement
