"""Physical DuckLake operations for stable materialization tables."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from sqlglot import exp

from repository.catalogue.client import Catalogue
from repository.catalogue.query import classify_select


MATERIALIZED_SCHEMA = "_atlas_materializations"
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_CHANGED_KEYS_TABLE = "_atlas_materialization_changed_keys"
_SCOPED_SOURCE_ALIAS = "_atlas_materialization_source"
_SCOPED_CHANGED_ALIAS = "_atlas_materialization_changed"
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
    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def table_identity(
        self, name: str, *, schema_name: str | None = None
    ) -> DuckLakeTableIdentity:
        schema_name = schema_name or self.catalogue.config.schema
        alias = _quote_literal(self.catalogue.config.alias)
        rows = self.catalogue.remote_rows(
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
        scope_relations: dict[str, tuple[str, ...]],
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
            return
        if not key_columns:
            raise MaterializationError(
                f"{refresh_strategy.title()} materializations require key columns."
            )
        if len(set(key_columns)) != len(key_columns):
            raise MaterializationError("Key columns must not contain duplicates.")
        for column in key_columns:
            _validate_name(column)
        for relation, columns in scope_relations.items():
            _validate_name(relation)
            for column in columns:
                _validate_name(column)
        self._scope_incremental_query(
            sql,
            source_table=source_table,
            key_columns=key_columns,
            scope_relations=scope_relations,
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
        for relation, columns in scope_relations.items():
            missing_source_scope = set(columns) - source_columns
            if missing_source_scope:
                raise MaterializationError(
                    f"Scope columns missing from {source_table}: "
                    + ", ".join(sorted(missing_source_scope))
                    + "."
                )
            relation_columns = self._relation_columns(
                _qualified_source(self.catalogue, relation)
            )
            missing_relation_scope = set(columns) - relation_columns
            if missing_relation_scope:
                raise MaterializationError(
                    f"Scope columns missing from {relation}: "
                    + ", ".join(sorted(missing_relation_scope))
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
            self.catalogue.remote_execute(
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
            self.catalogue.remote_execute(
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
        rows = self.catalogue.remote_rows(
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
        scope_relations: dict[str, tuple[str, ...]],
        partition: int,
        partition_count: int,
    ) -> MaterializationTable:
        current = self._checked_target(name, expected_uuid)
        self._prepare_backfill_keys(
            source_table=source_table,
            key_columns=key_columns,
            scope_relations=scope_relations,
            partition=partition,
            partition_count=partition_count,
        )
        try:
            if not self._has_changed_keys():
                return current
            scope_rows = self._scope_rows(scope_relations)
            query = self._scope_incremental_query(
                sql,
                source_table=source_table,
                key_columns=key_columns,
                scope_relations=scope_relations,
                scope_rows=scope_rows,
            )
            target = _qualified(self.catalogue, name)
            target_match = _key_match(
                "materialized_target", "changed", key_columns
            )
            source_match = _key_match(
                "materialized_source", "changed", key_columns
            )
            with self.catalogue.remote_transaction():
                self.catalogue.remote_execute(
                    f"DELETE FROM {target} AS materialized_target "
                    "WHERE EXISTS (SELECT 1 FROM "
                    "_atlas_materialization_changed_keys AS changed "
                    f"WHERE {target_match})"
                )
                self.catalogue.remote_execute(
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
        scope_relations: dict[str, tuple[str, ...]],
        partition: int,
        partition_count: int,
    ) -> MaterializationTable:
        current = self._checked_target(name, expected_uuid)
        self._prepare_backfill_keys(
            source_table=source_table,
            key_columns=key_columns,
            scope_relations=scope_relations,
            partition=partition,
            partition_count=partition_count,
        )
        try:
            if not self._has_changed_keys():
                return current
            scope_rows = self._scope_rows(scope_relations)
            query = self._scope_incremental_query(
                sql,
                source_table=source_table,
                key_columns=key_columns,
                scope_relations=scope_relations,
                scope_rows=scope_rows,
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
                self.catalogue.remote_execute(
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
        classify_select(sql)
        query = sql.strip().removesuffix(";")
        self._use_main()
        with self.catalogue.remote_transaction():
            self.catalogue.remote_execute(
                f"DELETE FROM {_qualified(self.catalogue, name)}"
            )
            try:
                self.catalogue.remote_execute(
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
        scope_relations: dict[str, tuple[str, ...]],
    ) -> MaterializationTable:
        current = self._checked_target(name, expected_uuid)
        self._use_main()
        try:
            self._prepare_changes(
                source_table_id=source_table_id,
                from_snapshot=from_snapshot,
                to_snapshot=to_snapshot,
                key_columns=key_columns,
                scope_relations=scope_relations,
            )
            if not self._has_changed_keys():
                return current
            query = self._scope_incremental_query(
                sql,
                source_table=source_table,
                key_columns=key_columns,
                scope_relations=scope_relations,
                scope_rows=self._scope_rows(scope_relations),
            )
            target = _qualified(self.catalogue, name)
            target_match = _key_match("materialized_target", "changed", key_columns)
            source_match = _key_match("materialized_source", "changed", key_columns)
            with self.catalogue.remote_transaction():
                self.catalogue.remote_execute(
                    f"DELETE FROM {target} AS materialized_target "
                    "WHERE EXISTS (SELECT 1 FROM _atlas_materialization_changed_keys "
                    f"AS changed WHERE {target_match})"
                )
                try:
                    self.catalogue.remote_execute(
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
        scope_relations: dict[str, tuple[str, ...]],
    ) -> MaterializationTable:
        current = self._checked_target(name, expected_uuid)
        self._use_main()
        try:
            self._prepare_changes(
                source_table_id=source_table_id,
                from_snapshot=from_snapshot,
                to_snapshot=to_snapshot,
                key_columns=key_columns,
                scope_relations=scope_relations,
            )
            mutations = self.catalogue.remote_rows(
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
                key_columns=key_columns,
                scope_relations=scope_relations,
                scope_rows=self._scope_rows(scope_relations),
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
                    self.catalogue.remote_execute(
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
        self.catalogue.remote_execute(
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
        self.catalogue.remote_execute(
            f"ALTER TABLE {_qualified(self.catalogue, name)} SET PARTITIONED BY "
            f"(year({_quote_identifier(column)}), month({_quote_identifier(column)}), "
            f"day({_quote_identifier(column)}))"
        )
        return self.inspect(name)

    def inspect(self, name: str) -> MaterializationTable:
        try:
            identity = self.table_identity(name, schema_name=MATERIALIZED_SCHEMA)
            relation = _qualified(self.catalogue, name)
            columns = self.catalogue.remote_rows(f"DESCRIBE {relation}")
            counts = self.catalogue.remote_rows(
                f"SELECT count(*) FROM {relation}"
            )
            alias = _quote_literal(self.catalogue.config.alias)
            stats = self.catalogue.remote_rows(
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
        scope_relations: dict[str, tuple[str, ...]],
    ) -> None:
        if from_snapshot > to_snapshot:
            raise MaterializationError("The CDC snapshot range is reversed.")
        selected_columns = _selected_scope_columns(key_columns, scope_relations)
        columns = ", ".join(
            _quote_identifier(column) for column in selected_columns
        )
        alias = _quote_literal(self.catalogue.config.alias)
        schema = _quote_literal(self.catalogue.config.schema)
        source = self.table_identity_from_id(source_table_id)
        self.catalogue.remote_execute(
            "CREATE OR REPLACE TEMP TABLE _atlas_materialization_changes AS "
            f"SELECT change_type, {columns} FROM ducklake_table_changes("
            f"{alias}, {schema}, {_quote_literal(source.table_name)}, "
            f"{int(from_snapshot)}, {int(to_snapshot)})"
        )
        self.catalogue.remote_execute(
            "CREATE OR REPLACE TEMP TABLE _atlas_materialization_changed_keys AS "
            f"SELECT DISTINCT {columns} FROM _atlas_materialization_changes"
        )

    def _prepare_backfill_keys(
        self,
        *,
        source_table: str,
        key_columns: tuple[str, ...],
        scope_relations: dict[str, tuple[str, ...]],
        partition: int,
        partition_count: int,
    ) -> None:
        if partition_count < 1 or partition < 0 or partition >= partition_count:
            raise MaterializationError("The bootstrap partition is invalid.")
        selected_columns = _selected_scope_columns(key_columns, scope_relations)
        columns = ", ".join(
            _quote_identifier(column) for column in selected_columns
        )
        hash_arguments = ", ".join(
            _quote_identifier(column) for column in key_columns
        )
        self.catalogue.remote_execute(
            "CREATE OR REPLACE TEMP TABLE "
            "_atlas_materialization_changed_keys AS "
            f"SELECT DISTINCT {columns} "
            f"FROM {_qualified_source(self.catalogue, source_table)} "
            f"WHERE hash({hash_arguments}) % {int(partition_count)} "
            f"= {int(partition)}"
        )

    def _drop_changes(self) -> None:
        self.catalogue.remote_execute(
            "DROP TABLE IF EXISTS _atlas_materialization_changed_keys"
        )
        self.catalogue.remote_execute(
            "DROP TABLE IF EXISTS _atlas_materialization_changes"
        )

    def _has_changed_keys(self) -> bool:
        rows = self.catalogue.remote_rows(
            "SELECT EXISTS(SELECT 1 FROM _atlas_materialization_changed_keys)"
        )
        return bool(rows and rows[0][0])

    def _scope_rows(
        self,
        scope_relations: dict[str, tuple[str, ...]],
    ) -> dict[str, tuple[tuple[object, ...], ...]]:
        return {
            relation: tuple(
                tuple(row)
                for row in self.catalogue.remote_rows(
                    "SELECT DISTINCT "
                    + ", ".join(_quote_identifier(column) for column in columns)
                    + " FROM _atlas_materialization_changed_keys"
                )
            )
            for relation, columns in scope_relations.items()
        }

    def _scope_incremental_query(
        self,
        sql: str,
        *,
        source_table: str,
        key_columns: tuple[str, ...],
        scope_relations: dict[str, tuple[str, ...]],
        scope_rows: dict[str, tuple[tuple[object, ...], ...]] | None = None,
    ) -> str:
        """Restrict every physical driving-table scan before evaluating the query."""

        statement = classify_select(sql).copy()
        cte_names = {
            cte.alias_or_name.lower()
            for cte in statement.find_all(exp.CTE)
            if cte.alias_or_name
        }
        if source_table.lower() in cte_names:
            raise MaterializationError(
                f"Incremental materialization SQL may not shadow its driving table "
                f"{source_table!r} with a CTE."
            )
        if any(
            table.name.lower() == _CHANGED_KEYS_TABLE
            for table in statement.find_all(exp.Table)
        ):
            raise MaterializationError(
                f"Materialization SQL may not read the reserved relation "
                f"{_CHANGED_KEYS_TABLE}."
            )

        sources = [
            table
            for table in statement.find_all(exp.Table)
            if self._is_driving_table(table, source_table)
        ]
        if not sources:
            raise MaterializationError(
                "Incremental materialization SQL must directly read its declared "
                f"driving table {source_table!r}."
            )

        source_alias = _quote_identifier(_SCOPED_SOURCE_ALIAS)
        changed_alias = _quote_identifier(_SCOPED_CHANGED_ALIAS)
        changed_table = _quote_identifier(_CHANGED_KEYS_TABLE)
        predicate = _key_match(
            _SCOPED_SOURCE_ALIAS,
            _SCOPED_CHANGED_ALIAS,
            key_columns,
        )
        for source in sources:
            base = source.copy()
            outer_alias = base.args.pop("alias", None)
            base_sql = base.sql(dialect="duckdb")
            scoped = classify_select(
                f"SELECT {source_alias}.* FROM {base_sql} AS {source_alias} "
                f"WHERE EXISTS (SELECT 1 FROM {changed_table} AS {changed_alias} "
                f"WHERE {predicate})"
            )
            if outer_alias is None:
                outer_alias = exp.TableAlias(this=exp.to_identifier(source_table))
            source.replace(exp.Subquery(this=scoped, alias=outer_alias))
        for relation, columns in scope_relations.items():
            scoped_sources = [
                table
                for table in statement.find_all(exp.Table)
                if isinstance(table.this, exp.Identifier)
                and table.name.lower() == relation.lower()
                and table.name.lower() not in cte_names
            ]
            if not scoped_sources:
                raise MaterializationError(
                    f"Scoped relation {relation!r} is not read by the query."
                )
            relation_alias = _quote_identifier(_SCOPED_SOURCE_ALIAS)
            rows = (scope_rows or {}).get(relation)
            relation_predicate = (
                _literal_scope_predicate(
                    _SCOPED_SOURCE_ALIAS,
                    columns,
                    rows,
                )
                if rows is not None
                else _key_match(
                    _SCOPED_SOURCE_ALIAS,
                    _SCOPED_CHANGED_ALIAS,
                    columns,
                )
            )
            for source in scoped_sources:
                base = source.copy()
                outer_alias = base.args.pop("alias", None)
                base_sql = base.sql(dialect="duckdb")
                where = (
                    relation_predicate
                    if rows is not None
                    else (
                        f"EXISTS (SELECT 1 FROM {changed_table} "
                        f"AS {changed_alias} WHERE {relation_predicate})"
                    )
                )
                scoped = classify_select(
                    f"SELECT {relation_alias}.* FROM {base_sql} "
                    f"AS {relation_alias} WHERE {where}"
                )
                if outer_alias is None:
                    outer_alias = exp.TableAlias(
                        this=exp.to_identifier(relation)
                    )
                source.replace(exp.Subquery(this=scoped, alias=outer_alias))
        self._replace_scope_markers(statement, scope_relations, scope_rows or {})
        return statement.sql(dialect="duckdb")

    @staticmethod
    def _replace_scope_markers(
        statement: exp.Expression,
        scope_relations: dict[str, tuple[str, ...]],
        scope_rows: dict[str, tuple[tuple[object, ...], ...]],
    ) -> None:
        values_by_column: dict[str, list[object]] = {}
        for relation, columns in scope_relations.items():
            for row in scope_rows.get(relation, ()):
                for column, value in zip(columns, row, strict=True):
                    values = values_by_column.setdefault(column.lower(), [])
                    if value not in values:
                        values.append(value)

        for node in tuple(statement.find_all(exp.Dot)):
            function = node.expression
            if (
                not isinstance(node.this, exp.Identifier)
                or node.this.name.lower() != "macros"
                or not isinstance(function, exp.Anonymous)
                or function.name.lower() != "materialization_scope"
                or len(function.expressions) != 1
                or not isinstance(function.expressions[0], exp.Literal)
                or not function.expressions[0].is_string
            ):
                continue
            column = function.expressions[0].this.lower()
            values = values_by_column.get(column, [])
            if len(values) == 1:
                node.replace(exp.convert(values[0]))

    def _is_driving_table(self, table: exp.Table, source_table: str) -> bool:
        return (
            isinstance(table.this, exp.Identifier)
            and table.name.lower() == source_table.lower()
            and (
                not table.db
                or table.db.lower() == self.catalogue.config.schema.lower()
            )
            and (
                not table.catalog
                or table.catalog.lower() == self.catalogue.config.alias.lower()
            )
        )

    def _has_duplicate_keys(
        self, *, relation: str, key_columns: tuple[str, ...]
    ) -> bool:
        columns = ", ".join(_quote_identifier(column) for column in key_columns)
        rows = self.catalogue.remote_rows(
            f"SELECT EXISTS(SELECT 1 FROM {relation} AS keyed_rows "
            f"GROUP BY {columns} HAVING count(*) > 1)"
        )
        return bool(rows and rows[0][0])

    def _relation_columns(self, relation: str) -> set[str]:
        self._use_main()
        rows = self.catalogue.remote_rows(f"DESCRIBE {relation}")
        return {str(row[0]) for row in rows}

    def _query_columns(self, sql: str) -> set[str]:
        classify_select(sql)
        query = sql.strip().removesuffix(";")
        self._use_main()
        rows = self.catalogue.remote_rows(
            f"DESCRIBE SELECT * FROM ({query}) AS materialized_source"
        )
        return {str(row[0]) for row in rows}

    def table_identity_from_id(self, table_id: int) -> DuckLakeTableIdentity:
        alias = _quote_literal(self.catalogue.config.alias)
        rows = self.catalogue.remote_rows(
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


def _selected_scope_columns(
    key_columns: tuple[str, ...],
    scope_relations: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *key_columns,
                *(
                    column
                    for columns in scope_relations.values()
                    for column in columns
                ),
            )
        )
    )


def _literal_scope_predicate(
    alias: str,
    columns: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
) -> str:
    if not rows:
        return "false"
    return " OR ".join(
        "("
        + " AND ".join(
            f"{alias}.{_quote_identifier(column)} IS NOT DISTINCT FROM "
            f"{_sql_literal(value)}"
            for column, value in zip(columns, row, strict=True)
        )
        + ")"
        for row in rows
    )


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return _quote_literal(str(value))


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
