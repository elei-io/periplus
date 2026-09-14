"""Public SQL syntax and namespace validation."""
import re
from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlglot.dialects.duckdb import DuckDB
from periplus.platform.catalogue.public import PUBLIC_SCHEMA, public_objects

class _QueryDuckDB(DuckDB):
    # SQLGlot's shared keyword table treats ?:: as a distinct operator. DuckDB
    # treats it as an anonymous parameter followed by a cast. Correct lexical
    # recognition only; the original SQL and parameters still execute unchanged.
    class Parser(DuckDB.Parser):
        # SEARCH is a catalogue macro, not SQLGlot's two-argument search expression.
        FUNCTIONS = {key: value for key, value in DuckDB.Parser.FUNCTIONS.items() if key != "SEARCH"}

    class Tokenizer(DuckDB.Tokenizer):
        KEYWORDS = {key: value for key, value in DuckDB.Tokenizer.KEYWORDS.items() if key != "?::"}


_MAX_ROWS = 10_000
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


def _bounded_query(sql: str, *, max_rows: int = _MAX_ROWS, schema: str = PUBLIC_SCHEMA) -> str:
    source = sql.strip()
    normalized = source.removesuffix(";").rstrip()
    explain_prefix = _EXPLAIN_PREFIX.match(normalized)
    if explain_prefix is not None:
        explained = normalized[explain_prefix.end() :].strip()
        statement = _one_statement(explained)
        if not isinstance(statement, exp.Query):
            raise ValueError("EXPLAIN accepts one read-only query")
        _validate_catalogue_access(statement, schema=schema)
        return normalized

    statement = _one_statement(source)
    if isinstance(statement, exp.Query):
        _validate_catalogue_access(statement, schema=schema)
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
        _validate_catalogue_access(statement, schema=schema)
        return normalized
    if isinstance(statement, exp.Show):
        source_schema = statement.args.get("from_")
        if (
            str(statement.this).upper() == "TABLES"
            and isinstance(source_schema, exp.Table)
            and not source_schema.db
            and source_schema.name.lower() == schema
        ):
            return normalized
        raise ValueError(
            f"SHOW is limited to SHOW TABLES FROM {schema}"
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


def _validate_catalogue_access(statement: exp.Expression, *, schema: str = PUBLIC_SCHEMA) -> None:
    ctes = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    for table in statement.find_all(exp.Table):
        if table.catalog:
            raise ValueError("SQL console does not accept explicit catalog names")
        if table.db and table.db.lower() != schema:
            raise ValueError(f"SQL console may only read {schema}.*")
        name = (table.this.name if isinstance(table.this, exp.Func) else table.name).lower()
        if not table.db and name in ctes:
            continue
        if name.startswith(_FORBIDDEN_RELATION_PREFIXES):
            raise ValueError("SQL console may not read system relations")
        if name not in {item.name for item in public_objects(schema)}:
            raise ValueError(f"Unknown {schema} relation; use a documented public relation")
    for dot in statement.find_all(exp.Dot):
        if isinstance(dot.expression, exp.Func):
            if not isinstance(dot.this, exp.Identifier) or dot.this.name.lower() != schema:
                raise ValueError(f"SQL console may only call helpers in {schema}.*")
            if dot.expression.name.lower() not in {item.name for item in public_objects(schema)}:
                raise ValueError(f"Unknown {schema} helper")
    for function in statement.find_all(exp.Func):
        if function.name.lower() in _FORBIDDEN_FUNCTIONS:
            raise ValueError(f"SQL console may not call {function.name}")
