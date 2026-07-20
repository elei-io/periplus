"""Physical DuckLake operations for catalogue materializations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from repository.catalogue.client import Catalogue
from repository.catalogue.query import classify_select
from repository.catalogue.schema import (
    INTERNAL_SCHEMA,
    MATERIALIZATION_COVERAGE_TABLE,
)
from repository.catalogue.views import DuckLakeView

MATERIALIZED_SCHEMA = "_atlas_materializations"
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class MaterializationError(ValueError):
    pass


class MaterializationConflictError(MaterializationError):
    pass


class MaterializationSchemaChangeError(MaterializationError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializationTable:
    table_uuid: UUID
    name: str
    row_count: int
    columns: tuple[tuple[str, str, bool], ...]
    active_file_count: int
    active_storage_bytes: int
    partitioning: tuple[str, ...]


class MaterializationStore:
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def create_empty_scoped(
        self, *, name: str, sql: str, parameters: dict[str, object]
    ) -> MaterializationTable:
        """Create only the result schema; scoped jobs populate rows later."""

        _validate_name(name)
        classify_select(sql)
        if any(
            item.table_name == name
            for item in self.catalogue.lake.table.list(
                schema_name=MATERIALIZED_SCHEMA
            )
        ):
            raise MaterializationConflictError(
                f"Table {MATERIALIZED_SCHEMA}.{name} already exists."
            )
        self._use_main()
        query = sql.strip().removesuffix(";")
        self.catalogue.connection.execute(
            f"CREATE TABLE {_qualified(self.catalogue, name)} AS "
            f"SELECT * FROM ({query}) AS scoped_result WHERE false",
            parameters,
        )
        return self.inspect(name)

    def validate_scoped_schema(
        self, *, name: str, sql: str, parameters: dict[str, object]
    ) -> None:
        """Reject an online scoped rebuild before changing its active definition."""

        current = self.inspect(name)
        classify_select(sql)
        self._use_main()
        query = sql.strip().removesuffix(";")
        cursor = self.catalogue.connection.execute(
            f"SELECT * FROM ({query}) AS scoped_result WHERE false",
            parameters,
        )
        proposed = tuple((str(item[0]), str(item[1])) for item in cursor.description)
        existing = tuple((column, data_type) for column, data_type, _ in current.columns)
        if proposed != existing:
            raise MaterializationSchemaChangeError(
                "The new query revision changes the durable schema. "
                "Dematerialize and create it again to accept that schema change."
            )

    def drop(self, *, name: str, expected_uuid: UUID) -> None:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializationConflictError("The materialized table changed; refresh the page before dropping it.")
        self.catalogue.connection.execute(f"DROP TABLE {_qualified(self.catalogue, name)}")

    def drop_managed(
        self, *, name: str, expected_uuid: UUID, materialization_id: UUID
    ) -> None:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializationConflictError(
                "The materialized table changed; dematerialization has been stopped."
            )
        coverage = ".".join(
            f'"{part}"'
            for part in (
                self.catalogue.config.alias,
                INTERNAL_SCHEMA,
                MATERIALIZATION_COVERAGE_TABLE,
            )
        )
        with self.catalogue.lake.transaction():
            self.catalogue.connection.execute(
                f"DELETE FROM {coverage} WHERE materialization_id = ?",
                [materialization_id],
            )
            self.catalogue.connection.execute(
                f"DROP TABLE {_qualified(self.catalogue, name)}"
            )

    def set_daily_partition(self, *, name: str, column: str) -> MaterializationTable:
        _validate_name(name)
        _validate_name(column)
        current = self.inspect(name)
        data_type = next(
            (data_type for item, data_type, _ in current.columns if item == column),
            None,
        )
        if data_type is None:
            raise MaterializationError(
                f"Partition column {column!r} is not present in the result."
            )
        if "DATE" not in data_type and "TIMESTAMP" not in data_type:
            raise MaterializationError(
                "Daily partitioning requires a DATE or TIMESTAMP result column."
            )
        self.catalogue.connection.execute(
            f"ALTER TABLE {_qualified(self.catalogue, name)} SET PARTITIONED BY "
            f"(year({_quote_identifier(column)}), month({_quote_identifier(column)}), "
            f"day({_quote_identifier(column)}))"
        )
        return self.inspect(name)

    def set_scope_sort(
        self,
        *,
        name: str,
        scope_column: str,
        partition_column: str | None,
    ) -> MaterializationTable:
        """Keep every bounded scope contiguous, then order its temporal rows."""

        _validate_name(name)
        _validate_name(scope_column)
        columns = [scope_column]
        if partition_column is not None and partition_column != scope_column:
            _validate_name(partition_column)
            columns.append(partition_column)
        available = {column for column, _data_type, _nullable in self.inspect(name).columns}
        missing = set(columns) - available
        if missing:
            raise MaterializationError(
                f"Sort columns are absent from the materialized result: {sorted(missing)!r}"
            )
        expressions = ", ".join(_quote_identifier(value) for value in columns)
        self.catalogue.connection.execute(
            f"ALTER TABLE {_qualified(self.catalogue, name)} "
            f"SET SORTED BY ({expressions})"
        )
        return self.inspect(name)

    def inspect(self, name: str) -> MaterializationTable:
        try:
            info = self.catalogue.lake.table.info(
                name,
                schema_name=MATERIALIZED_SCHEMA,
                include_summary=False,
                include_row_count=True,
                include_snapshots=False,
            )
        except Exception as exc:
            raise MaterializationConflictError(f"Materialized table {name} is unavailable.") from exc
        metadata_catalog = f'"__ducklake_metadata_{self.catalogue.config.alias}"'
        metadata_schema = _quote_identifier(self.catalogue.metadata_schema)
        identity = self.catalogue.connection.execute(
            f"""
            SELECT t.table_uuid
            FROM {metadata_catalog}.{metadata_schema}.ducklake_table AS t
            JOIN {metadata_catalog}.{metadata_schema}.ducklake_schema AS s USING (schema_id)
            WHERE t.end_snapshot IS NULL AND s.end_snapshot IS NULL
              AND t.table_name = ? AND s.schema_name = ?
            """,
            [name, MATERIALIZED_SCHEMA],
        ).fetchone()
        if identity is None:
            raise MaterializationConflictError(f"Materialized table {name} has no DuckLake identity.")
        file_stats = self.catalogue.connection.execute(
            f"""
            SELECT count(*), coalesce(sum(f.file_size_bytes), 0)
            FROM {metadata_catalog}.{metadata_schema}.ducklake_data_file AS f
            JOIN {metadata_catalog}.{metadata_schema}.ducklake_table AS t
              ON t.table_id = f.table_id
            JOIN {metadata_catalog}.{metadata_schema}.ducklake_schema AS s
              ON s.schema_id = t.schema_id
            WHERE f.end_snapshot IS NULL AND t.end_snapshot IS NULL
              AND s.end_snapshot IS NULL AND s.schema_name = ? AND t.table_name = ?
            """,
            [MATERIALIZED_SCHEMA, name],
        ).fetchone()
        partition_rows = self.catalogue.connection.execute(
            f"""
            SELECT pc.transform, c.column_name
            FROM {metadata_catalog}.{metadata_schema}.ducklake_partition_info AS pi
            JOIN {metadata_catalog}.{metadata_schema}.ducklake_partition_column AS pc
              ON pc.partition_id = pi.partition_id AND pc.table_id = pi.table_id
            JOIN {metadata_catalog}.{metadata_schema}.ducklake_table AS t
              ON t.table_id = pi.table_id
            JOIN {metadata_catalog}.{metadata_schema}.ducklake_schema AS s
              ON s.schema_id = t.schema_id
            JOIN {metadata_catalog}.{metadata_schema}.ducklake_column AS c
              ON c.table_id = pc.table_id AND c.column_id = pc.column_id
            WHERE pi.end_snapshot IS NULL AND t.end_snapshot IS NULL
              AND s.end_snapshot IS NULL AND c.end_snapshot IS NULL
              AND s.schema_name = ? AND t.table_name = ?
            ORDER BY pc.partition_key_index
            """,
            [MATERIALIZED_SCHEMA, name],
        ).fetchall()
        return MaterializationTable(
            table_uuid=UUID(str(identity[0])),
            name=name,
            row_count=int(info.row_count or 0),
            columns=tuple((column.name, column.data_type, column.nullable) for column in info.columns),
            active_file_count=int(file_stats[0] if file_stats else 0),
            active_storage_bytes=int(file_stats[1] if file_stats else 0),
            partitioning=tuple(f"{row[0]}({row[1]})" for row in partition_rows),
        )

    def _use_main(self) -> None:
        self.catalogue.connection.execute(
            f'USE "{self.catalogue.config.alias}"."{self.catalogue.config.schema}"'
        )


def scoped_view_query(
    catalogue: Catalogue, view: DuckLakeView, *, scope_kind: str, scope_column: str
) -> str:
    """Wrap a view in one safely bound materialization scope."""

    if scope_column not in view.columns:
        raise MaterializationError(
            f"Scope column {scope_column!r} is not an output of {view.qualified_name}."
        )
    qualified_view = ".".join(
        _quote_identifier(value)
        for value in (catalogue.config.alias, view.schema_name, view.view_name)
    )
    return (
        f"SELECT * FROM {qualified_view} "
        f"WHERE {_quote_identifier(scope_column)} = ${scope_kind}_id"
    )


def scoped_select(sql: str, *, scope_kind: str, scope_column: str) -> str:
    """Bound a stored view definition by its declared output discriminator."""

    classify_select(sql)
    query = sql.strip().removesuffix(";")
    return (
        f"SELECT * FROM ({query}) AS atlas_view_definition "
        f"WHERE {_quote_identifier(scope_column)} = ${scope_kind}_id"
    )


def _validate_name(value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise MaterializationError("Name must use lower-case letters, numbers, and underscores.")


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(f'"{part}"' for part in (catalogue.config.alias, MATERIALIZED_SCHEMA, name))


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'
