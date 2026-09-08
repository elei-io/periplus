"""Bounded page-local SQL selection, independent of graph and catalogue execution."""
from threading import Timer

import duckdb
import pyarrow as pa
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from periplus.urls import normalize_url
from periplus.platform.config.duckdb import connection_limits

MAX_SELECTION_ROWS = 1000
MAX_SELECTION_BYTES = 2 * 1024 * 1024


def selected_urls(values) -> tuple[str, ...]:
    urls = []
    size = 0
    for index, value in enumerate(values):
        if index >= MAX_SELECTION_ROWS:
            raise ValueError("selection exceeded its 1,000-row limit")
        if not isinstance(value, str) or len(value) > 8192:
            raise ValueError("selection requires HTTP(S) URL strings up to 8,192 characters")
        size += len(value.encode())
        if size > MAX_SELECTION_BYTES:
            raise ValueError("selection exceeded its output-byte limit")
        urls.append(normalize_url(value))
    return tuple(dict.fromkeys(urls))


def validate_follow_sql(sql: str) -> str:
    try:
        statements = parse(sql, dialect="duckdb")
    except ParseError as exc:
        raise ValueError("follow SQL could not be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("follow SQL requires exactly one SELECT query")
    statement = statements[0]
    ctes = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    reads_navigation = False
    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise ValueError("follow SQL cannot call table functions")
        if not table.catalog and not table.db and table.name.lower() in ctes:
            continue
        if not table.catalog and table.db.lower() == "nav" and table.name.lower() == "links":
            reads_navigation = True
            continue
        raise ValueError("follow SQL can read only nav.links")
    if not reads_navigation:
        raise ValueError("follow SQL must read nav.links")
    if any(True for _ in statement.find_all(exp.Parameter, exp.Placeholder)):
        raise ValueError("follow SQL cannot contain runtime parameters")
    if any(function.name.lower() == "getenv" for function in statement.find_all(exp.Anonymous)):
        raise ValueError("follow SQL cannot access environment variables")
    if statement.named_selects != ["url"]:
        raise ValueError("follow SQL must return exactly one column named url")
    return statement.sql(dialect="duckdb")


def select_links(sql: str, navigation: bytes, *, timeout_seconds: float = 5) -> tuple[str, ...]:
    statement = validate_follow_sql(sql)
    if not 0 < timeout_seconds <= 20:
        raise ValueError("selection deadline outside bounds")
    if len(navigation) > 16 * 1024 * 1024:
        raise ValueError("navigation input exceeds 16 MiB")
    table = pa.ipc.open_file(pa.BufferReader(navigation)).read_all()
    with duckdb.connect(":memory:", config=connection_limits({"memory_limit": "128MB", "threads": "1"})) as connection:
        connection.execute("SET enable_external_access = false")
        connection.execute("SET autoinstall_known_extensions = false")
        connection.execute("SET autoload_known_extensions = false")
        connection.register("navigation_input", table)
        connection.execute("CREATE SCHEMA nav")
        connection.execute("CREATE VIEW nav.links AS SELECT * FROM navigation_input")
        connection.execute("SET lock_configuration = true")
        timer = Timer(timeout_seconds, connection.interrupt)
        timer.daemon = True
        timer.start()
        try:
            cursor = connection.execute(statement)
            names = [column[0] for column in cursor.description]
            if names.count("url") != 1:
                raise ValueError("follow SQL must return exactly one url column")
            index = names.index("url")
            rows = cursor.fetchmany(MAX_SELECTION_ROWS + 1)
            return selected_urls(row[index] for row in rows)
        except duckdb.Error as exc:
            raise ValueError("follow SQL failed validation or exceeded its execution limits") from exc
        finally:
            timer.cancel()
            timer.join()
