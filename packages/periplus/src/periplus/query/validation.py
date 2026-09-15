"""Parse a corpus seed statement in the public ClickHouse dialect."""

from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlglot.dialects.clickhouse import ClickHouse


class QueryClickHouse(ClickHouse):
    class Tokenizer(ClickHouse.Tokenizer):
        KEYWORDS = {
            key: value
            for key, value in ClickHouse.Tokenizer.KEYWORDS.items()
            if key != "?::"
        }


def _one_statement(sql: str) -> exp.Expression:
    try:
        statements = parse(sql, read=QueryClickHouse)
    except ParseError as exc:
        raise ValueError("SQL could not be parsed") from exc
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("Submit exactly one statement")
    return statements[0]
