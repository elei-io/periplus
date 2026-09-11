"""SQLAlchemy dialect for public Periplus queries and bounded reflection."""
from __future__ import annotations

from typing import Any

from sqlalchemy import exc, types
from sqlalchemy.engine import default
from sqlalchemy.engine.reflection import cache
from sqlalchemy.sql.compiler import IdentifierPreparer

from . import dbapi


class SQLType(types.UserDefinedType):
    """Preserve DuckDB type names, including nested types, during reflection."""

    cache_ok = True

    def __init__(self, name: str):
        self.name = name

    def get_col_spec(self, **kw: Any) -> str:
        return self.name

    @property
    def python_type(self) -> type:
        if self.name in dbapi._INTEGER_TYPES:
            return int
        if self.name in dbapi._FLOAT_TYPES:
            return float
        if self.name == "BOOLEAN":
            return bool
        if self.name.startswith("DECIMAL("):
            return dbapi.Decimal
        if self.name == "DATE":
            return dbapi.date
        if self.name in dbapi._TIMESTAMP_TYPES:
            return dbapi.datetime
        if self.name in dbapi._TIME_TYPES:
            return dbapi.time
        if self.name == "BLOB":
            return bytes
        return str


class PeriplusDialect(default.DefaultDialect):
    # The server speaks DuckDB SQL; this enables the correct notebook SQL dialect.
    name = "duckdb"
    driver = "periplus"
    supports_statement_cache = False
    supports_sane_rowcount = False
    supports_sane_multi_rowcount = False
    supports_native_decimal = True
    default_paramstyle = "qmark"
    preparer = IdentifierPreparer

    @classmethod
    def import_dbapi(cls):
        return dbapi

    def create_connect_args(self, url):
        if url.username or url.password or url.host or url.port:
            raise exc.ArgumentError("Use periplus:///periplus with base_url and mode in connect_args.")
        if url.query:
            raise exc.ArgumentError("Pass connection options in connect_args, not URL query parameters.")
        return [], {}

    def initialize(self, connection):
        self.default_schema_name = connection.connection.dbapi_connection.schema_version

    def do_rollback(self, dbapi_connection):
        # SQLAlchemy resets pooled connections this way. There is no remote
        # transaction: each read already completed in its own server snapshot.
        pass

    def do_begin(self, dbapi_connection):
        pass

    def do_commit(self, dbapi_connection):
        dbapi_connection.commit()

    def _schema(self, connection, schema):
        current = connection.connection.dbapi_connection.schema_version
        if schema is not None and schema != current:
            raise exc.InvalidRequestError(f"Only the configured public schema {current!r} is available.")
        return current

    @cache
    def get_schema_names(self, connection, **kw):
        return [self._schema(connection, None)]

    def _complete(self, result):
        raw = result.cursor.result
        try:
            rows = result.fetchall()
            if raw.truncated:
                raise exc.InvalidRequestError("Catalogue discovery was truncated by public query limits; refusing an incomplete schema.")
            return rows
        finally:
            result.close()

    @cache
    def get_view_names(self, connection, schema=None, **kw):
        schema = self._schema(connection, schema)
        result = connection.exec_driver_sql(f"SHOW TABLES FROM {self.identifier_preparer.quote_identifier(schema)}")
        return [row[0] for row in self._complete(result)]

    @cache
    def get_table_names(self, connection, schema=None, **kw):
        self._schema(connection, schema)
        # All queryable public catalogue relations are views.
        return []

    @cache
    def has_table(self, connection, table_name, schema=None, **kw):
        return table_name in self.get_view_names(connection, schema)

    @cache
    def get_columns(self, connection, table_name, schema=None, **kw):
        schema = self._schema(connection, schema)
        quote = self.identifier_preparer.quote_identifier
        result = connection.exec_driver_sql(f"DESCRIBE {quote(schema)}.{quote(table_name)}")
        return [{"name": row[0], "type": SQLType(row[1]), "nullable": row[2] != "NO",
                 "default": row[4]} for row in self._complete(result)]

    @cache
    def get_pk_constraint(self, connection, table_name, schema=None, **kw):
        self._schema(connection, schema)
        return {"name": None, "constrained_columns": []}

    @cache
    def get_foreign_keys(self, connection, table_name, schema=None, **kw):
        self._schema(connection, schema)
        return []

    @cache
    def get_indexes(self, connection, table_name, schema=None, **kw):
        self._schema(connection, schema)
        return []
