"""Physical DuckLake operations for whole-table materializations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from repository.catalogue.client import Catalogue
from repository.catalogue.query import classify_select


MATERIALIZED_SCHEMA = "_atlas_materializations"
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class MaterializationError(ValueError):
    pass


class MaterializationConflictError(MaterializationError):
    pass


class MaterializationSchemaChangeError(MaterializationError):
    pass


@dataclass(frozen=True, slots=True)
class DuckLakeTableIdentity:
    table_id: int
    table_uuid: UUID
    schema_name: str
    table_name: str
    begin_snapshot: int


@dataclass(frozen=True, slots=True)
class MaterializationTable:
    table_id: int
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

    def table_identity(
        self, name: str, *, schema_name: str | None = None
    ) -> DuckLakeTableIdentity:
        schema_name = schema_name or self.catalogue.config.schema
        metadata = _quote_identifier(
            f"__ducklake_metadata_{self.catalogue.config.alias}"
        )
        metadata_schema = _quote_identifier(self.catalogue.metadata_schema)
        row = self.catalogue.connection.execute(
            f"""
            SELECT t.table_id, t.table_uuid, s.schema_name, t.table_name,
                   t.begin_snapshot
            FROM {metadata}.{metadata_schema}.ducklake_table AS t
            JOIN {metadata}.{metadata_schema}.ducklake_schema AS s USING (schema_id)
            WHERE t.end_snapshot IS NULL AND s.end_snapshot IS NULL
              AND s.schema_name = ? AND t.table_name = ?
            """,
            [schema_name, name],
        ).fetchone()
        if row is None:
            raise MaterializationError(
                f"DuckLake table {schema_name}.{name} does not exist."
            )
        return DuckLakeTableIdentity(
            table_id=int(row[0]),
            table_uuid=UUID(str(row[1])),
            schema_name=str(row[2]),
            table_name=str(row[3]),
            begin_snapshot=int(row[4]),
        )

    def create_full(self, *, name: str, sql: str) -> tuple[MaterializationTable, int]:
        _validate_name(name)
        classify_select(sql)
        try:
            self.table_identity(name, schema_name=MATERIALIZED_SCHEMA)
        except MaterializationError:
            pass
        else:
            raise MaterializationConflictError(
                f"Table {MATERIALIZED_SCHEMA}.{name} already exists."
            )
        query = sql.strip().removesuffix(";")
        self._use_main()
        with self.catalogue.lake.transaction():
            source_snapshot = self.catalogue.latest_snapshot()
            if source_snapshot is None:
                raise MaterializationError("DuckLake has no source snapshot.")
            self.catalogue.connection.execute(
                f"CREATE TABLE {_qualified(self.catalogue, name)} AS "
                f"SELECT * FROM ({query}) AS materialized_source"
            )
        return self.inspect(name), source_snapshot

    def refresh_full(self, *, name: str, expected_uuid: UUID, sql: str) -> MaterializationTable:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializationConflictError(
                "The materialized table identity changed; refresh stopped."
            )
        classify_select(sql)
        query = sql.strip().removesuffix(";")
        self._use_main()
        with self.catalogue.lake.transaction():
            self.catalogue.connection.execute(
                f"DELETE FROM {_qualified(self.catalogue, name)}"
            )
            try:
                self.catalogue.connection.execute(
                    f"INSERT INTO {_qualified(self.catalogue, name)} "
                    f"SELECT * FROM ({query}) AS materialized_source"
                )
            except Exception as exc:
                raise MaterializationSchemaChangeError(
                    "The materialized query no longer matches its durable table schema. "
                    "Dematerialize and create it again."
                ) from exc
        return self.inspect(name)

    def drop(self, *, name: str, expected_uuid: UUID) -> None:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializationConflictError(
                "The materialized table identity changed; removal stopped."
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

    def inspect(self, name: str) -> MaterializationTable:
        try:
            info = self.catalogue.lake.table.info(
                name,
                schema_name=MATERIALIZED_SCHEMA,
                include_summary=False,
                include_row_count=True,
                include_snapshots=False,
            )
            identity = self.table_identity(name, schema_name=MATERIALIZED_SCHEMA)
        except Exception as exc:
            raise MaterializationConflictError(
                f"Materialized table {name} is unavailable."
            ) from exc
        metadata = _quote_identifier(
            f"__ducklake_metadata_{self.catalogue.config.alias}"
        )
        metadata_schema = _quote_identifier(self.catalogue.metadata_schema)
        file_stats = self.catalogue.connection.execute(
            f"""
            SELECT count(*), coalesce(sum(f.file_size_bytes), 0)
            FROM {metadata}.{metadata_schema}.ducklake_data_file AS f
            WHERE f.end_snapshot IS NULL AND f.table_id = ?
            """,
            [identity.table_id],
        ).fetchone()
        partition_rows = self.catalogue.connection.execute(
            f"""
            SELECT pc.transform, c.column_name
            FROM {metadata}.{metadata_schema}.ducklake_partition_info AS pi
            JOIN {metadata}.{metadata_schema}.ducklake_partition_column AS pc
              ON pc.partition_id = pi.partition_id AND pc.table_id = pi.table_id
            JOIN {metadata}.{metadata_schema}.ducklake_column AS c
              ON c.table_id = pc.table_id AND c.column_id = pc.column_id
            WHERE pi.end_snapshot IS NULL AND c.end_snapshot IS NULL
              AND pi.table_id = ?
            ORDER BY pc.partition_key_index
            """,
            [identity.table_id],
        ).fetchall()
        return MaterializationTable(
            table_id=identity.table_id,
            table_uuid=identity.table_uuid,
            name=name,
            row_count=int(info.row_count or 0),
            columns=tuple(
                (column.name, column.data_type, column.nullable)
                for column in info.columns
            ),
            active_file_count=int(file_stats[0] if file_stats else 0),
            active_storage_bytes=int(file_stats[1] if file_stats else 0),
            partitioning=tuple(f"{row[0]}({row[1]})" for row in partition_rows),
        )

    def _use_main(self) -> None:
        self.catalogue.connection.execute(
            f'USE "{self.catalogue.config.alias}"."{self.catalogue.config.schema}"'
        )


def _validate_name(value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise MaterializationError(
            "Name must use lower-case letters, numbers, and underscores."
        )


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(
        _quote_identifier(part)
        for part in (catalogue.config.alias, MATERIALIZED_SCHEMA, name)
    )


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'
