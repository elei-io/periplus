"""Public SQL syntax and namespace validation."""
import re
from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlglot.dialects.duckdb import DuckDB
from periplus.platform.catalogue.public import PUBLIC_SCHEMAS

class _QueryDuckDB(DuckDB):
    # SQLGlot's shared keyword table treats ?:: as a distinct operator. DuckDB
    # treats it as an anonymous parameter followed by a cast. Correct lexical
    # recognition only; the original SQL and parameters still execute unchanged.
    class Tokenizer(DuckDB.Tokenizer):
        KEYWORDS = {key: value for key, value in DuckDB.Tokenizer.KEYWORDS.items() if key != "?::"}


_MAX_ROWS = 10_000
_READABLE_SCHEMAS = frozenset(PUBLIC_SCHEMAS)
_EXPLAIN_PREFIX = re.compile(r"^EXPLAIN\s+(?:ANALYZE\s+)?", re.IGNORECASE)
_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "attach",
        "current_setting",
        "duckdb_secrets",
        "duckdb_settings",
        "getenv",
        "glob",
        "http_get",
        "http_post",
        "parquet_scan",
        "postgres_query",
        "postgres_scan",
        "query",
        "query_table",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_parquet",
        "read_text",
        "which_secret",
    }
)
_FORBIDDEN_RELATION_PREFIXES = (
    "duckdb_",
    "pg_",
    "pragma_",
    "sqlite_",
)


def _bounded_query(sql: str, *, max_rows: int = _MAX_ROWS) -> str:
    source = sql.strip()
    normalized = source.removesuffix(";").rstrip()
    explain_prefix = _EXPLAIN_PREFIX.match(normalized)
    if explain_prefix is not None:
        explained = normalized[explain_prefix.end() :].strip()
        statement = _one_statement(explained)
        if not isinstance(statement, exp.Query):
            raise ValueError("EXPLAIN accepts one read-only query")
        _validate_catalogue_access(statement)
        return normalized

    statement = _one_statement(source)
    if isinstance(statement, exp.Query):
        _validate_catalogue_access(statement)
        return (
            f"SELECT * FROM ({normalized}) AS periplus_console_query "
            f"LIMIT {max_rows + 1}"
        )
    if isinstance(statement, (exp.Describe, exp.Summarize)):
        target = statement.this
        if not isinstance(target, (exp.Query, exp.Table)):
            raise ValueError(
                f"{statement.key.upper()} requires a public relation "
                "or read-only query"
            )
        _validate_catalogue_access(statement)
        return normalized
    if isinstance(statement, exp.Show):
        source_schema = statement.args.get("from_")
        if (
            str(statement.this).upper() == "TABLES"
            and isinstance(source_schema, exp.Table)
            and not source_schema.db
            and source_schema.name.lower() in _READABLE_SCHEMAS
        ):
            return normalized
        raise ValueError(
            "SHOW is limited to SHOW TABLES FROM web or SHOW TABLES FROM content"
        )
    raise ValueError(
        "SQL console accepts one read-only query or public inspection statement"
    )


def _one_statement(sql: str) -> exp.Expression:
    try:
        statements = parse(sql, read=_QueryDuckDB)
    except ParseError as exc:
        raise ValueError(str(exc)) from exc
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("SQL console accepts exactly one statement")
    return statements[0]


def _validate_catalogue_access(statement: exp.Expression) -> None:
    ctes = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    for table in statement.find_all(exp.Table):
        if table.catalog:
            raise ValueError("SQL console does not accept explicit catalog names")
        if table.db and table.db.lower() not in _READABLE_SCHEMAS:
            raise ValueError("SQL console may only read web.* or content.*")
        name = table.name.lower()
        if name in ctes:
            continue
        if name.startswith(_FORBIDDEN_RELATION_PREFIXES):
            raise ValueError("SQL console may not read system relations")
        if not table.db:
            raise ValueError(
                "catalogue relations must be qualified with web or content"
            )
    for function in statement.find_all(exp.Func):
        if function.name.lower() in _FORBIDDEN_FUNCTIONS:
            raise ValueError(f"SQL console may not call {function.name}")
