"""Physical DuckLake operations for stable materialization tables."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from sqlglot import exp

from atlas_sql import (
    AtlasCompiler,
    FullMaterializationPurpose,
    KeyedMaterializationPurpose,
    ScalarMacroDefinition,
    ScalarFunctionDefinition,
    CompilationResult,
    TableMacroDefinition,
    ViewDefinition,
)
from repository.catalogue.client import Catalogue
from repository.catalogue.query import classify_select


MATERIALIZED_SCHEMA = "_atlas_materializations"
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_CHANGED_KEYS_TABLE = "_atlas_materialization_changed_keys"
_PHYSICAL_NAME_PREFIX = "m_"
_BOOTSTRAP_KEYS_PER_PARTITION = 1


class MaterializationError(ValueError):
    pass


class MaterializationConflictError(MaterializationError):
    pass


class MaterializationSchemaChangeError(MaterializationError):
    pass


class MaterializationAppendOnlyViolation(MaterializationError):
    pass


def physical_materialization_name(materialization_id: UUID) -> str:
    """Return the private table name owned by one immutable incarnation."""

    return f"{_PHYSICAL_NAME_PREFIX}{materialization_id.hex}"


@dataclass(frozen=True, slots=True)
class DuckLakeTableIdentity:
    table_id: int
    table_uuid: UUID
    schema_name: str
    table_name: str


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
    def __init__(
        self,
        catalogue: Catalogue,
        *,
        scalar_macros: tuple[ScalarMacroDefinition, ...] = (),
        table_macros: tuple[TableMacroDefinition, ...] = (),
        views: tuple[ViewDefinition, ...] = (),
        scalar_functions: tuple[ScalarFunctionDefinition, ...] = (),
    ) -> None:
        self.catalogue = catalogue
        self.scalar_macros = scalar_macros
        self.table_macros = table_macros
        self.views = views
        self.scalar_functions = scalar_functions

    def table_identity(
        self, name: str, *, schema_name: str | None = None
    ) -> DuckLakeTableIdentity:
        schema_name = schema_name or self.catalogue.config.schema
        alias = _quote_literal(self.catalogue.config.alias)
        rows = self.catalogue.trusted_remote_rows(
            f"""
            SELECT tables.table_id,
                   tables.table_uuid,
                   names.table_schema,
                   tables.table_name
            FROM ducklake_table_info({alias}) AS tables
            JOIN information_schema.tables AS names USING (table_name)
            WHERE names.table_catalog = {alias}
              AND names.table_schema = {_quote_literal(schema_name)}
              AND tables.table_name = {_quote_literal(name)}
            """
        )
        if len(rows) != 1:
            raise MaterializationError(
                f"DuckLake table {schema_name}.{name} does not exist."
            )
        row = rows[0]
        return DuckLakeTableIdentity(
            table_id=int(row[0]),
            table_uuid=UUID(str(row[1])),
            schema_name=str(row[2]),
            table_name=str(row[3]),
        )

    def validate_refresh_strategy(
        self,
        *,
        source_table: str,
        sql: str,
        refresh_strategy: str,
        key_columns: tuple[str, ...],
        coverage_source: str | None = None,
    ) -> None:
        if refresh_strategy not in {"keyed", "append", "full"}:
            raise MaterializationError(
                f"Unknown refresh strategy {refresh_strategy!r}."
            )
        if refresh_strategy == "full":
            if key_columns:
                raise MaterializationError(
                    "Full refresh materializations do not use key columns."
                )
            self._compile_full_query(
                sql,
                coverage_source=coverage_source,
            )
            return
        if not key_columns:
            raise MaterializationError(
                f"{refresh_strategy.title()} materializations require key columns."
            )
        if len(set(key_columns)) != len(key_columns):
            raise MaterializationError("Key columns must not contain duplicates.")
        for column in key_columns:
            _validate_name(column)
        self._scope_incremental_query(
            sql,
            source_table=source_table,
            refresh_strategy=refresh_strategy,
            key_columns=key_columns,
            coverage_source=coverage_source,
        )
        source_columns = self._relation_columns(
            _qualified_source(self.catalogue, source_table)
        )
        result_columns = self._query_columns(sql)
        missing_source = set(key_columns) - source_columns
        missing_result = set(key_columns) - result_columns
        if missing_source:
            raise MaterializationError(
                "Key columns missing from the driving table: "
                + ", ".join(sorted(missing_source))
                + "."
            )
        if missing_result:
            raise MaterializationError(
                "Key columns missing from the view result: "
                + ", ".join(sorted(missing_result))
                + "."
            )

    def create_full(
        self,
        *,
        name: str,
        sql: str,
        append_key_columns: tuple[str, ...] = (),
    ) -> tuple[MaterializationTable, int]:
        _validate_name(name)
        try:
            self.table_identity(name, schema_name=MATERIALIZED_SCHEMA)
        except MaterializationError:
            pass
        else:
            raise MaterializationConflictError(
                f"Table {MATERIALIZED_SCHEMA}.{name} already exists."
            )
        query = self._compile_full_query(sql)
        self._use_main()
        with self.catalogue.remote_transaction():
            source_snapshot = self.catalogue.latest_snapshot()
            if source_snapshot is None:
                raise MaterializationError("DuckLake has no source snapshot.")
            self.catalogue.trusted_remote_execute(
                f"CREATE TABLE {_qualified(self.catalogue, name)} AS "
                f"SELECT * FROM ({query}) AS materialized_source"
            )
            if append_key_columns and self._has_duplicate_keys(
                relation=_qualified(self.catalogue, name),
                key_columns=append_key_columns,
            ):
                raise MaterializationAppendOnlyViolation(
                    "Append key columns must uniquely identify every result row."
                )
        return self.inspect(name), source_snapshot

    def create_empty(
        self,
        *,
        name: str,
        sql: str,
    ) -> tuple[MaterializationTable, int]:
        """Create only the durable result shape for a batched bootstrap."""

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
        with self.catalogue.remote_transaction():
            source_snapshot = self.catalogue.latest_snapshot()
            if source_snapshot is None:
                raise MaterializationError("DuckLake has no source snapshot.")
            self.catalogue.trusted_remote_execute(
                f"CREATE TABLE {_qualified(self.catalogue, name)} AS "
                f"SELECT * FROM ({query}) AS materialized_source LIMIT 0"
            )
        return self.inspect(name), source_snapshot

    def bootstrap_partition_count(
        self,
        *,
        source_table: str,
        key_columns: tuple[str, ...],
    ) -> int:
        """Size stable hash partitions from the current driving-key cardinality."""

        columns = ", ".join(_quote_identifier(column) for column in key_columns)
        rows = self.catalogue.trusted_remote_rows(
            "SELECT count(*) FROM ("
            f"SELECT DISTINCT {columns} "
            f"FROM {_qualified_source(self.catalogue, source_table)}"
            ") AS bootstrap_keys"
        )
        key_count = int(rows[0][0] if rows else 0)
        return max(
            1,
            (key_count + _BOOTSTRAP_KEYS_PER_PARTITION - 1)
            // _BOOTSTRAP_KEYS_PER_PARTITION,
        )

    def backfill_keyed_partition(
        self,
        *,
        name: str,
        expected_uuid: UUID,
        sql: str,
        source_table: str,
        key_columns: tuple[str, ...],
        partition: int,
        partition_count: int,
    ) -> MaterializationTable:
        current = self._checked_target(name, expected_uuid)
        self._prepare_backfill_keys(
            source_table=source_table,
            key_columns=key_columns,
            partition=partition,
            partition_count=partition_count,
        )
        try:
            if not self._has_changed_keys():
                return current
            query = self._scope_incremental_query(
                sql,
                source_table=source_table,
                refresh_strategy="keyed",
                key_columns=key_columns,
                bind_key_rows=True,
            )
            target = _qualified(self.catalogue, name)
            target_match = _key_match(
                "materialized_target", "changed", key_columns
            )
            source_match = _key_match(
                "materialized_source", "changed", key_columns
            )
            with self.catalogue.remote_transaction():
                self.catalogue.trusted_remote_execute(
                    f"DELETE FROM {target} AS materialized_target "
                    "WHERE EXISTS (SELECT 1 FROM "
                    "_atlas_materialization_changed_keys AS changed "
                    f"WHERE {target_match})"
                )
                self.catalogue.trusted_remote_execute(
                    f"INSERT INTO {target} "
                    f"SELECT materialized_source.* FROM ({query}) "
                    "AS materialized_source "
                    "WHERE EXISTS (SELECT 1 FROM "
                    "_atlas_materialization_changed_keys AS changed "
                    f"WHERE {source_match})"
                )
        finally:
            self._drop_changes()
        return self.inspect(name)

    def backfill_append_partition(
        self,
        *,
        name: str,
        expected_uuid: UUID,
        sql: str,
        source_table: str,
        key_columns: tuple[str, ...],
        partition: int,
        partition_count: int,
    ) -> MaterializationTable:
        current = self._checked_target(name, expected_uuid)
        self._prepare_backfill_keys(
            source_table=source_table,
            key_columns=key_columns,
            partition=partition,
            partition_count=partition_count,
        )
        try:
            if not self._has_changed_keys():
                return current
            query = self._scope_incremental_query(
                sql,
                source_table=source_table,
                refresh_strategy="append",
                key_columns=key_columns,
                bind_key_rows=True,
            )
            target = _qualified(self.catalogue, name)
            source_match = _key_match(
                "materialized_source", "changed", key_columns
            )
            target_match = _key_match(
                "materialized_target", "candidate", key_columns
            )
            candidates = (
                f"SELECT materialized_source.* FROM ({query}) "
                "AS materialized_source "
                "WHERE EXISTS (SELECT 1 FROM "
                "_atlas_materialization_changed_keys AS changed "
                f"WHERE {source_match})"
            )
            if self._has_duplicate_keys(
                relation=f"({candidates})",
                key_columns=key_columns,
            ):
                raise MaterializationAppendOnlyViolation(
                    "Append key columns must uniquely identify every result row."
                )
            with self.catalogue.remote_transaction():
                self.catalogue.trusted_remote_execute(
                    f"INSERT INTO {target} "
                    f"SELECT candidate.* FROM ({candidates}) AS candidate "
                    f"WHERE NOT EXISTS (SELECT 1 FROM {target} "
                    f"AS materialized_target WHERE {target_match})"
                )
        finally:
            self._drop_changes()
        return self.inspect(name)

    def refresh_full(self, *, name: str, expected_uuid: UUID, sql: str) -> MaterializationTable:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializationConflictError(
                "The materialized table identity changed; refresh stopped."
            )
        query = self._compile_full_query(sql)
        self._use_main()
        with self.catalogue.remote_transaction():
            self.catalogue.trusted_remote_execute(
                f"DELETE FROM {_qualified(self.catalogue, name)}"
            )
            try:
                self.catalogue.trusted_remote_execute(
                    f"INSERT INTO {_qualified(self.catalogue, name)} "
                    f"SELECT * FROM ({query}) AS materialized_source"
                )
            except Exception as exc:
                raise MaterializationSchemaChangeError(
                    "The materialized query no longer matches its durable table schema. "
                    "Dematerialize and create it again."
                ) from exc
        return self.inspect(name)

    def refresh_keyed(
        self,
        *,
        name: str,
        expected_uuid: UUID,
        sql: str,
        source_table: str,
        source_table_id: int,
        from_snapshot: int,
        to_snapshot: int,
        key_columns: tuple[str, ...],
    ) -> MaterializationTable:
        current = self._checked_target(name, expected_uuid)
        self._use_main()
        try:
            self._prepare_changes(
                source_table_id=source_table_id,
                from_snapshot=from_snapshot,
                to_snapshot=to_snapshot,
                key_columns=key_columns,
            )
            if not self._has_changed_keys():
                return current
            query = self._scope_incremental_query(
                sql,
                source_table=source_table,
                refresh_strategy="keyed",
                key_columns=key_columns,
                bind_key_rows=True,
            )
            target = _qualified(self.catalogue, name)
            target_match = _key_match("materialized_target", "changed", key_columns)
            source_match = _key_match("materialized_source", "changed", key_columns)
            with self.catalogue.remote_transaction():
                self.catalogue.trusted_remote_execute(
                    f"DELETE FROM {target} AS materialized_target "
                    "WHERE EXISTS (SELECT 1 FROM _atlas_materialization_changed_keys "
                    f"AS changed WHERE {target_match})"
                )
                try:
                    self.catalogue.trusted_remote_execute(
                        f"INSERT INTO {target} "
                        f"SELECT materialized_source.* FROM ({query}) "
                        "AS materialized_source "
                        "WHERE EXISTS (SELECT 1 FROM "
                        "_atlas_materialization_changed_keys AS changed "
                        f"WHERE {source_match})"
                    )
                except Exception as exc:
                    raise MaterializationSchemaChangeError(
                        "The materialized query no longer matches its durable "
                        "table schema. Dematerialize and create it again."
                    ) from exc
        finally:
            self._drop_changes()
        return self.inspect(name)

    def refresh_append(
        self,
        *,
        name: str,
        expected_uuid: UUID,
        sql: str,
        source_table: str,
        source_table_id: int,
        from_snapshot: int,
        to_snapshot: int,
        key_columns: tuple[str, ...],
    ) -> MaterializationTable:
        current = self._checked_target(name, expected_uuid)
        self._use_main()
        try:
            self._prepare_changes(
                source_table_id=source_table_id,
                from_snapshot=from_snapshot,
                to_snapshot=to_snapshot,
                key_columns=key_columns,
            )
            mutations = self.catalogue.trusted_remote_rows(
                "SELECT change_type FROM _atlas_materialization_changes "
                "WHERE change_type <> 'insert' LIMIT 1"
            )
            if mutations:
                raise MaterializationAppendOnlyViolation(
                    "The driving table emitted a non-insert change. "
                    "Dematerialize and choose keyed or full refresh."
                )
            if not self._has_changed_keys():
                return current
            query = self._scope_incremental_query(
                sql,
                source_table=source_table,
                refresh_strategy="append",
                key_columns=key_columns,
                bind_key_rows=True,
            )
            target = _qualified(self.catalogue, name)
            source_match = _key_match("materialized_source", "changed", key_columns)
            target_match = _key_match("materialized_target", "candidate", key_columns)
            candidates = (
                f"SELECT materialized_source.* FROM ({query}) AS materialized_source "
                "WHERE EXISTS (SELECT 1 FROM _atlas_materialization_changed_keys "
                f"AS changed WHERE {source_match})"
            )
            if self._has_duplicate_keys(
                relation=f"({candidates})",
                key_columns=key_columns,
            ):
                raise MaterializationAppendOnlyViolation(
                    "Append key columns must uniquely identify every result row."
                )
            try:
                with self.catalogue.remote_transaction():
                    self.catalogue.trusted_remote_execute(
                        f"INSERT INTO {target} "
                        f"SELECT candidate.* FROM ({candidates}) AS candidate "
                        f"WHERE NOT EXISTS (SELECT 1 FROM {target} "
                        f"AS materialized_target WHERE {target_match})"
                    )
            except MaterializationAppendOnlyViolation:
                raise
            except Exception as exc:
                raise MaterializationSchemaChangeError(
                    "The materialized query no longer matches its durable table "
                    "schema. Dematerialize and create it again."
                ) from exc
        finally:
            self._drop_changes()
        return self.inspect(name)

    def drop(self, *, name: str, expected_uuid: UUID) -> None:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializationConflictError(
                "The materialized table identity changed; removal stopped."
            )
        self.catalogue.trusted_remote_execute(
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
        self.catalogue.trusted_remote_execute(
            f"ALTER TABLE {_qualified(self.catalogue, name)} SET PARTITIONED BY "
            f"(year({_quote_identifier(column)}), month({_quote_identifier(column)}), "
            f"day({_quote_identifier(column)}))"
        )
        return self.inspect(name)

    def inspect(self, name: str) -> MaterializationTable:
        try:
            identity = self.table_identity(name, schema_name=MATERIALIZED_SCHEMA)
            relation = _qualified(self.catalogue, name)
            columns = self.catalogue.trusted_remote_rows(f"DESCRIBE {relation}")
            counts = self.catalogue.trusted_remote_rows(
                f"SELECT count(*) FROM {relation}"
            )
            alias = _quote_literal(self.catalogue.config.alias)
            stats = self.catalogue.trusted_remote_rows(
                "SELECT file_count, file_size_bytes "
                f"FROM ducklake_table_info({alias}) "
                f"WHERE table_id = {identity.table_id}"
            )
        except Exception as exc:
            raise MaterializationConflictError(
                f"Materialized table {name} is unavailable."
            ) from exc
        file_count, storage_bytes = stats[0] if stats else (0, 0)
        return MaterializationTable(
            table_id=identity.table_id,
            table_uuid=identity.table_uuid,
            name=name,
            row_count=int(counts[0][0] if counts else 0),
            columns=tuple(
                (
                    str(column[0]),
                    str(column[1]),
                    str(column[2]).upper() == "YES",
                )
                for column in columns
            ),
            active_file_count=int(file_count or 0),
            active_storage_bytes=int(storage_bytes or 0),
            partitioning=(),
        )

    def _use_main(self) -> None:
        # Basin sessions start in the selected lake's main schema. All
        # Atlas-owned mutation targets are fully qualified.
        return

    def _checked_target(
        self, name: str, expected_uuid: UUID
    ) -> MaterializationTable:
        current = self.inspect(name)
        if current.table_uuid != expected_uuid:
            raise MaterializationConflictError(
                "The materialized table identity changed; refresh stopped."
            )
        return current

    def _prepare_changes(
        self,
        *,
        source_table_id: int,
        from_snapshot: int,
        to_snapshot: int,
        key_columns: tuple[str, ...],
    ) -> None:
        if from_snapshot > to_snapshot:
            raise MaterializationError("The CDC snapshot range is reversed.")
        columns = ", ".join(_quote_identifier(column) for column in key_columns)
        alias = _quote_literal(self.catalogue.config.alias)
        schema = _quote_literal(self.catalogue.config.schema)
        source = self.table_identity_from_id(source_table_id)
        self.catalogue.trusted_remote_execute(
            "CREATE OR REPLACE TEMP TABLE _atlas_materialization_changes AS "
            f"SELECT change_type, {columns} FROM ducklake_table_changes("
            f"{alias}, {schema}, {_quote_literal(source.table_name)}, "
            f"{int(from_snapshot)}, {int(to_snapshot)})"
        )
        self.catalogue.trusted_remote_execute(
            "CREATE OR REPLACE TEMP TABLE _atlas_materialization_changed_keys AS "
            f"SELECT DISTINCT {columns} FROM _atlas_materialization_changes"
        )

    def _prepare_backfill_keys(
        self,
        *,
        source_table: str,
        key_columns: tuple[str, ...],
        partition: int,
        partition_count: int,
    ) -> None:
        if partition_count < 1 or partition < 0 or partition >= partition_count:
            raise MaterializationError("The bootstrap partition is invalid.")
        columns = ", ".join(_quote_identifier(column) for column in key_columns)
        hash_arguments = ", ".join(
            _quote_identifier(column) for column in key_columns
        )
        self.catalogue.trusted_remote_execute(
            "CREATE OR REPLACE TEMP TABLE "
            "_atlas_materialization_changed_keys AS "
            f"SELECT DISTINCT {columns} "
            f"FROM {_qualified_source(self.catalogue, source_table)} "
            f"WHERE hash({hash_arguments}) % {int(partition_count)} "
            f"= {int(partition)}"
        )

    def _drop_changes(self) -> None:
        self.catalogue.trusted_remote_execute(
            "DROP TABLE IF EXISTS _atlas_materialization_changed_keys"
        )
        self.catalogue.trusted_remote_execute(
            "DROP TABLE IF EXISTS _atlas_materialization_changes"
        )

    def _has_changed_keys(self) -> bool:
        rows = self.catalogue.trusted_remote_rows(
            "SELECT EXISTS(SELECT 1 FROM _atlas_materialization_changed_keys)"
        )
        return bool(rows and rows[0][0])

    def _scope_incremental_query(
        self,
        sql: str,
        *,
        source_table: str,
        refresh_strategy: str,
        key_columns: tuple[str, ...],
        bind_key_rows: bool = False,
        coverage_source: str | None = "materialization_execution",
    ) -> str:
        """Restrict every physical driving-table scan before evaluating the query."""

        if refresh_strategy in {"keyed", "append"}:
            key_rows = (
                tuple(
                    tuple(row)
                    for row in self.catalogue.trusted_remote_rows(
                        "SELECT DISTINCT "
                        + ", ".join(
                            _quote_identifier(column)
                            for column in key_columns
                        )
                        + " FROM "
                        + _quote_identifier(_CHANGED_KEYS_TABLE)
                    )
                )
                if bind_key_rows
                else None
            )
            result = AtlasCompiler.embedded().compile(
                sql,
                purpose=KeyedMaterializationPurpose(
                    source_table=source_table,
                    key_columns=key_columns,
                    changed_keys_relation=_CHANGED_KEYS_TABLE,
                    key_rows=key_rows,
                    scalar_macros=self.scalar_macros,
                    table_macros=self.table_macros,
                    views=self.views,
                    scalar_functions=self.scalar_functions,
                ),
                coverage_source=coverage_source,
            )
            return _require_materialization_sql(result)

        raise RuntimeError(f"Unknown refresh strategy {refresh_strategy!r}.")

    def _compile_full_query(
        self,
        sql: str,
        *,
        coverage_source: str | None = "materialization_execution",
    ) -> str:
        """Resolve full-refresh SQL without applying changed-key policy."""

        result = AtlasCompiler.embedded().compile(
            sql,
            purpose=FullMaterializationPurpose(
                scalar_macros=self.scalar_macros,
                table_macros=self.table_macros,
                views=self.views,
                scalar_functions=self.scalar_functions,
            ),
            coverage_source=coverage_source,
        )
        return _require_materialization_sql(result)

    def _has_duplicate_keys(
        self, *, relation: str, key_columns: tuple[str, ...]
    ) -> bool:
        columns = ", ".join(_quote_identifier(column) for column in key_columns)
        rows = self.catalogue.trusted_remote_rows(
            f"SELECT EXISTS(SELECT 1 FROM {relation} AS keyed_rows "
            f"GROUP BY {columns} HAVING count(*) > 1)"
        )
        return bool(rows and rows[0][0])

    def _relation_columns(self, relation: str) -> set[str]:
        self._use_main()
        rows = self.catalogue.trusted_remote_rows(f"DESCRIBE {relation}")
        return {str(row[0]) for row in rows}

    def _query_columns(self, sql: str) -> set[str]:
        classify_select(sql)
        query = sql.strip().removesuffix(";")
        self._use_main()
        rows = self.catalogue.trusted_remote_rows(
            f"DESCRIBE SELECT * FROM ({query}) AS materialized_source"
        )
        return {str(row[0]) for row in rows}

    def table_identity_from_id(self, table_id: int) -> DuckLakeTableIdentity:
        alias = _quote_literal(self.catalogue.config.alias)
        rows = self.catalogue.trusted_remote_rows(
            f"""
            SELECT tables.table_id,
                   tables.table_uuid,
                   names.table_schema,
                   tables.table_name
            FROM ducklake_table_info({alias}) AS tables
            JOIN information_schema.tables AS names USING (table_name)
            WHERE names.table_catalog = {alias}
              AND tables.table_id = {int(table_id)}
            """
        )
        if len(rows) != 1:
            raise MaterializationError(
                f"DuckLake table ID {table_id} does not exist."
            )
        row = rows[0]
        return DuckLakeTableIdentity(
            table_id=int(row[0]),
            table_uuid=UUID(str(row[1])),
            schema_name=str(row[2]),
            table_name=str(row[3]),
        )


def _validate_name(value: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise MaterializationError(
            "Name must use lower-case letters, numbers, and underscores."
        )


def _require_materialization_sql(result: CompilationResult) -> str:
    if result.materialization_eligible and result.executable_sql is not None:
        return result.executable_sql
    reason = (
        result.diagnostics[-1].message
        if result.diagnostics
        else "The compiler could not prove this query safe."
    )
    raise MaterializationError(
        f"This SQL cannot be materialized: {reason}"
    )


def _qualified(catalogue: Catalogue, name: str) -> str:
    return ".".join(
        _quote_identifier(part)
        for part in (catalogue.config.alias, MATERIALIZED_SCHEMA, name)
    )


def _qualified_source(catalogue: Catalogue, name: str) -> str:
    return ".".join(
        _quote_identifier(part)
        for part in (catalogue.config.alias, catalogue.config.schema, name)
    )


def _key_match(left: str, right: str, key_columns: tuple[str, ...]) -> str:
    return " AND ".join(
        f"{left}.{_quote_identifier(column)} IS NOT DISTINCT FROM "
        f"{right}.{_quote_identifier(column)}"
        for column in key_columns
    )


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
