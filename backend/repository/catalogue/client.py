"""DuckLake bootstrap and connection lifecycle for the repository."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from ducklake_client import ColumnDef, DuckLake, DuckLakeError, PostgresCatalog, SQLType

from repository.catalogue.config import CatalogueConfig
from repository.catalogue.exceptions import CatalogueSchemaError
from repository.catalogue.schema import (
    ARTIFACT_COLUMNS,
    CATALOGUE_SCHEMA_VERSION,
    CRAWL_ATTEMPT_COLUMNS,
    CRAWL_ATTEMPTS_TABLE,
    CRAWL_COLUMNS,
    CRAWL_STEP_COLUMNS,
    CRAWL_STEPS_TABLE,
    DOCUMENT_COLUMNS,
    INTERNAL_SCHEMA,
    URL_COLUMNS,
    expected_columns,
    expected_internal_columns,
)
from dom.schema import ELEMENT_COLUMNS

if TYPE_CHECKING:
    import duckdb


ELEMENT_PARTITION_BUCKETS = 256


class Catalogue:
    """Thin Atlas boundary over the published ``ducklake-client`` package."""

    def __init__(
        self,
        config: CatalogueConfig,
        *,
        temporary_directory: TemporaryDirectory[str] | None = None,
    ) -> None:
        self.config = config
        self._temporary_directory = temporary_directory
        self.lake = DuckLake(
            catalog=config.catalog,
            storage=config.storage,
            alias=config.alias,
            duckdb=config.duckdb,
            attach=config.attach,
        )
        self._use_catalogue_schema_if_available()

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        return self.lake.connection

    @property
    def metadata_schema(self) -> str:
        """Schema containing the attached DuckLake metadata tables."""

        return "public" if isinstance(self.config.catalog, PostgresCatalog) else "main"

    def bootstrap(self) -> None:
        """Create or migrate the catalogue and reject incompatible tables."""

        with self.lake.transaction():
            self.lake.schema.create(self.config.schema)
            self.lake.schema.create("views")
            self.lake.schema.create("macros")
            self.lake.schema.create(INTERNAL_SCHEMA)
            self.lake.schema.create("_atlas_materializations")
            # Superseded scoped-materialization state is deliberately not
            # migrated into the event-driven contract.
            self.connection.execute(
                f"DROP TABLE IF EXISTS "
                f"{self._qualified_table(INTERNAL_SCHEMA, 'materialization_coverage')}"
            )
            self.lake.table.create(
                "urls",
                schema_name=self.config.schema,
                **URL_COLUMNS,
            )
            self.lake.table.create(
                "artifacts",
                schema_name=self.config.schema,
                **ARTIFACT_COLUMNS,
            )
            self.lake.table.create(
                "documents",
                schema_name=self.config.schema,
                **DOCUMENT_COLUMNS,
            )
            self.lake.table.create(
                "crawls",
                schema_name=self.config.schema,
                **CRAWL_COLUMNS,
            )
            self.lake.table.create(
                CRAWL_ATTEMPTS_TABLE,
                schema_name=self.config.schema,
                **CRAWL_ATTEMPT_COLUMNS,
            )
            self.lake.table.create(
                "elements",
                schema_name=self.config.schema,
                **ELEMENT_COLUMNS,
            )
            self.lake.table.create(
                CRAWL_STEPS_TABLE,
                schema_name=self.config.schema,
                **CRAWL_STEP_COLUMNS,
            )
        self._use_catalogue_schema_if_available()
        self._configure_layout()
        self._configure_inlining()
        self._validate_physical_schema()

    def _use_catalogue_schema_if_available(self) -> None:
        """Make unqualified names resolve within the attached Atlas catalogue."""

        exists = self.connection.execute(
            """
            SELECT count(*)
            FROM information_schema.schemata
            WHERE catalog_name = ? AND schema_name = ?
            """,
            [self.config.alias, self.config.schema],
        ).fetchone()
        if exists is not None and int(exists[0]) > 0:
            namespace = ".".join(
                _quote_identifier(value)
                for value in (self.config.alias, self.config.schema)
            )
            self.connection.execute(f"USE {namespace}")

    def _configure_layout(self) -> None:
        """Apply the physical partition and sort contract for new data."""

        for table_name, column in (
            ("crawls", "captured_at"),
            (CRAWL_ATTEMPTS_TABLE, "started_at"),
            (CRAWL_STEPS_TABLE, "started_at"),
        ):
            expected = (
                (0, column, "year"),
                (1, column, "month"),
                (2, column, "day"),
            )
            if self._partitioning(table_name) == expected:
                continue
            table = self._qualified_table(self.config.schema, table_name)
            self.connection.execute(
                f"ALTER TABLE {table} SET PARTITIONED BY ("
                f"year({column}), month({column}), day({column}))"
            )
        elements = self._qualified_table(self.config.schema, "elements")
        if self._partitioning("elements") != (
            (0, "document_id", f"bucket({ELEMENT_PARTITION_BUCKETS})"),
        ):
            self.connection.execute(
                f"ALTER TABLE {elements} SET PARTITIONED BY "
                f"(bucket({ELEMENT_PARTITION_BUCKETS}, document_id))"
            )
        sort_orders = {
            "urls": ("url_id",),
            "artifacts": ("artifact_id",),
            "documents": ("document_id",),
            "crawls": ("requested_url_id", "captured_at"),
            CRAWL_ATTEMPTS_TABLE: ("crawl_id", "attempt_number"),
            CRAWL_STEPS_TABLE: ("crawl_id", "attempt_number", "step_ordinal"),
            "elements": ("document_id", "element_index"),
        }
        for table_name, columns in sort_orders.items():
            if self._sort_order(table_name) == columns:
                continue
            table = self._qualified_table(self.config.schema, table_name)
            expressions = ", ".join(_quote_identifier(value) for value in columns)
            self.connection.execute(
                f"ALTER TABLE {table} SET SORTED BY ({expressions})"
            )

    def _partitioning(
        self, table_name: str, schema_name: str | None = None
    ) -> tuple[tuple[int, str, str], ...]:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.config.alias}")
        rows = self.connection.execute(
            f"""
            SELECT pc.partition_key_index, c.column_name, pc.transform
            FROM {metadata}.ducklake_partition_info AS pi
            JOIN {metadata}.ducklake_partition_column AS pc
              ON pc.partition_id = pi.partition_id
             AND pc.table_id = pi.table_id
            JOIN {metadata}.ducklake_table AS t ON t.table_id = pi.table_id
            JOIN {metadata}.ducklake_schema AS s ON s.schema_id = t.schema_id
            JOIN {metadata}.ducklake_column AS c
              ON c.table_id = t.table_id
             AND c.column_id = pc.column_id
             AND c.end_snapshot IS NULL
            WHERE s.schema_name = ?
              AND t.table_name = ?
              AND pi.end_snapshot IS NULL
              AND t.end_snapshot IS NULL
              AND s.end_snapshot IS NULL
            ORDER BY pc.partition_key_index
            """,
            [schema_name or self.config.schema, table_name],
        ).fetchall()
        return tuple((int(row[0]), str(row[1]), str(row[2])) for row in rows)

    def _sort_order(
        self, table_name: str, schema_name: str | None = None
    ) -> tuple[str, ...]:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.config.alias}")
        rows = self.connection.execute(
            f"""
            SELECT expression
            FROM {metadata}.ducklake_sort_info AS si
            JOIN {metadata}.ducklake_sort_expression AS se
              ON se.sort_id = si.sort_id AND se.table_id = si.table_id
            JOIN {metadata}.ducklake_table AS t ON t.table_id = si.table_id
            JOIN {metadata}.ducklake_schema AS s ON s.schema_id = t.schema_id
            WHERE s.schema_name = ? AND t.table_name = ?
              AND si.end_snapshot IS NULL
              AND t.end_snapshot IS NULL
              AND s.end_snapshot IS NULL
            ORDER BY se.sort_key_index
            """,
            [schema_name or self.config.schema, table_name],
        ).fetchall()
        return tuple(str(row[0]).strip('"') for row in rows)

    def _qualified_table(self, schema_name: str, table_name: str) -> str:
        return ".".join(
            _quote_identifier(value)
            for value in (self.config.alias, schema_name, table_name)
        )

    def _migrate_layout(self) -> None:
        """Rewrite pre-partition files once so the current layout can prune them."""

        self._rewrite_legacy_bucket_files(
            "elements",
            column="document_id",
            columns=ELEMENT_COLUMNS,
            bucket_count=ELEMENT_PARTITION_BUCKETS,
        )

    def _rewrite_legacy_bucket_files(
        self,
        table_name: str,
        *,
        column: str,
        columns: dict[str, ColumnDef],
        bucket_count: int,
        stage_in_memory: bool = False,
    ) -> None:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.config.alias}")
        legacy_files = self.connection.execute(
            f"""
            SELECT count(*)
            FROM {metadata}.ducklake_data_file AS df
            JOIN {metadata}.ducklake_table AS t ON t.table_id = df.table_id
            JOIN {metadata}.ducklake_schema AS s ON s.schema_id = t.schema_id
            JOIN {metadata}.ducklake_partition_info AS pi
              ON pi.table_id = t.table_id
            WHERE s.schema_name = ? AND t.table_name = ?
              AND s.end_snapshot IS NULL AND t.end_snapshot IS NULL
              AND pi.end_snapshot IS NULL
              AND df.end_snapshot IS NULL
              AND df.begin_snapshot < pi.begin_snapshot
            """,
            [self.config.schema, table_name],
        ).fetchone()
        if legacy_files is None or int(legacy_files[0]) == 0:
            return

        replacement_name = f"__atlas_bucket{bucket_count}_{table_name}"
        table = ".".join(
            _quote_identifier(value)
            for value in (self.config.alias, self.config.schema, table_name)
        )
        replacement = ".".join(
            _quote_identifier(value)
            for value in (self.config.alias, self.config.schema, replacement_name)
        )
        definitions = ", ".join(
            f"{_quote_identifier(name)} {_type_sql(definition.data_type)}"
            + ("" if definition.nullable else " NOT NULL")
            for name, definition in columns.items()
        )
        source = table
        registration = f"__atlas_repartition_source_{table_name}"
        if stage_in_memory:
            # Fan-out tables are small state tables but may contain hundreds of
            # update fragments. Detach the replacement write from those files;
            # DuckLake 1.5 can otherwise fault while dropping the source table
            # in the same transaction that scanned its update history.
            rows = self.connection.execute(f"SELECT * FROM {table}").to_arrow_table()
            self.connection.register(registration, rows)
            source = _quote_identifier(registration)
        source_batches = self._s3_source_batches(
            metadata=metadata,
            table_name=table_name,
        )
        migration_settings = {
            str(name): str(value)
            for name, value in self.connection.execute(
                """
                SELECT name, value
                FROM duckdb_settings()
                WHERE name IN (
                    'http_keep_alive',
                    'memory_limit',
                    's3_uploader_thread_limit',
                    'threads',
                    'preserve_insertion_order',
                    'partitioned_write_max_open_files',
                    'partitioned_write_flush_threshold'
                )
                """
            ).fetchall()
        }
        try:
            # A wide bucket rewrite is an exclusive deployment operation. Give
            # that one-time rewrite enough parallelism to finish inside the S3
            # request-signing window while still bounding partition writers.
            self.connection.execute("SET memory_limit = '2048MB'")
            self.connection.execute("SET threads = 4")
            if "http_keep_alive" in migration_settings:
                self.connection.execute("SET http_keep_alive = false")
            if "s3_uploader_thread_limit" in migration_settings:
                self.connection.execute("SET s3_uploader_thread_limit = 4")
            self.connection.execute("SET preserve_insertion_order = false")
            self.connection.execute("SET partitioned_write_max_open_files = 16")
            self.connection.execute("SET partitioned_write_flush_threshold = 100000")
            if source_batches:
                self._rewrite_s3_batches(
                    table=table,
                    table_name=table_name,
                    replacement=replacement,
                    replacement_name=replacement_name,
                    definitions=definitions,
                    columns=columns,
                    column=column,
                    bucket_count=bucket_count,
                    batches=source_batches,
                )
            else:
                with self.lake.transaction():
                    self.connection.execute(f"DROP TABLE IF EXISTS {replacement}")
                    self.connection.execute(
                        f"CREATE TABLE {replacement} ({definitions})"
                    )
                    self.connection.execute(
                        f"ALTER TABLE {replacement} SET PARTITIONED BY "
                        f"(bucket({bucket_count}, {_quote_identifier(column)}))"
                    )
                    self.connection.execute(
                        f"INSERT INTO {replacement} BY NAME SELECT * FROM {source}"
                    )
                    self.connection.execute(f"DROP TABLE {table}")
                    self.connection.execute(
                        f"ALTER TABLE {replacement} "
                        f"RENAME TO {_quote_identifier(table_name)}"
                    )
                    self.set_commit_message(
                        author="Atlas setup",
                        message=f"Repartitioned {self.config.schema}.{table_name}",
                        extra={"partition": f"bucket({bucket_count}, {column})"},
                    )
        finally:
            self.connection.execute(
                "SET memory_limit = "
                f"'{migration_settings['memory_limit']}'"
            )
            if "http_keep_alive" in migration_settings:
                self.connection.execute(
                    "SET http_keep_alive = "
                    f"{migration_settings['http_keep_alive'].lower()}"
                )
            if "s3_uploader_thread_limit" in migration_settings:
                self.connection.execute(
                    "SET s3_uploader_thread_limit = "
                    f"{int(migration_settings['s3_uploader_thread_limit'])}"
                )
            self.connection.execute(
                f"SET threads = {int(migration_settings['threads'])}"
            )
            self.connection.execute(
                "SET preserve_insertion_order = "
                f"{migration_settings['preserve_insertion_order'].lower()}"
            )
            self.connection.execute(
                "SET partitioned_write_max_open_files = "
                f"{int(migration_settings['partitioned_write_max_open_files'])}"
            )
            self.connection.execute(
                "SET partitioned_write_flush_threshold = "
                f"{int(migration_settings['partitioned_write_flush_threshold'])}"
            )
            if stage_in_memory:
                self.connection.unregister(registration)

    def _s3_source_batches(
        self,
        *,
        metadata: str,
        table_name: str,
    ) -> list[tuple[int, list[str]]]:
        data_path = getattr(self.config.storage, "data_path", None)
        if not callable(data_path):
            return []
        root = str(data_path()).rstrip("/")
        if not root.startswith("s3://"):
            return []
        delete_count = self.connection.execute(
            f"""
            SELECT count(*)
            FROM {metadata}.ducklake_delete_file AS d
            JOIN {metadata}.ducklake_table AS t ON t.table_id = d.table_id
            JOIN {metadata}.ducklake_schema AS s ON s.schema_id = t.schema_id
            WHERE s.schema_name = ? AND t.table_name = ?
              AND s.end_snapshot IS NULL AND t.end_snapshot IS NULL
            """,
            [self.config.schema, table_name],
        ).fetchone()
        if delete_count is not None and int(delete_count[0]) > 0:
            raise CatalogueSchemaError(
                f"cannot batch-repartition {table_name!r} with delete files"
            )
        files = self.connection.execute(
            f"""
            SELECT s.path, t.path, df.path, df.record_count,
                   coalesce(fpv.partition_value, '')
            FROM {metadata}.ducklake_data_file AS df
            JOIN {metadata}.ducklake_table AS t ON t.table_id = df.table_id
            JOIN {metadata}.ducklake_schema AS s ON s.schema_id = t.schema_id
            LEFT JOIN {metadata}.ducklake_file_partition_value AS fpv
              ON fpv.data_file_id = df.data_file_id
             AND fpv.table_id = df.table_id
             AND fpv.partition_key_index = 0
            WHERE s.schema_name = ? AND t.table_name = ?
              AND s.end_snapshot IS NULL AND t.end_snapshot IS NULL
              AND df.end_snapshot IS NULL
            ORDER BY coalesce(fpv.partition_value, ''), df.file_order
            """,
            [self.config.schema, table_name],
        ).fetchall()
        grouped: dict[str, tuple[int, list[str]]] = {}
        for schema_path, table_path, file_path, record_count, partition_value in files:
            key = str(partition_value)
            count, paths = grouped.setdefault(key, (0, []))
            full_path = "/".join(
                part.strip("/")
                for part in (root, str(schema_path), str(table_path), str(file_path))
                if part
            )
            grouped[key] = (count + int(record_count), [*paths, full_path])
        return [grouped[key] for key in sorted(grouped)]

    def _rewrite_s3_batches(
        self,
        *,
        table: str,
        table_name: str,
        replacement: str,
        replacement_name: str,
        definitions: str,
        columns: dict[str, ColumnDef],
        column: str,
        bucket_count: int,
        batches: list[tuple[int, list[str]]],
    ) -> None:
        with self.lake.transaction():
            self.connection.execute(f"DROP TABLE IF EXISTS {replacement}")
            self.connection.execute(f"CREATE TABLE {replacement} ({definitions})")
            self.connection.execute(
                f"ALTER TABLE {replacement} SET PARTITIONED BY "
                f"(bucket({bucket_count}, {_quote_identifier(column)}))"
            )
        selected_columns = ", ".join(_quote_identifier(name) for name in columns)
        for expected_rows, paths in batches:
            with TemporaryDirectory(prefix="atlas-repartition-") as directory:
                local_paths = self._download_s3_files(paths, Path(directory))
                path_list = ", ".join(
                    _quote_literal(str(path)) for path in local_paths
                )
                inserted = None
                for attempt in range(8):
                    try:
                        with self.lake.transaction():
                            inserted = self.connection.execute(
                                f"INSERT INTO {replacement} BY NAME "
                                f"SELECT {selected_columns} "
                                f"FROM read_parquet([{path_list}], "
                                "hive_partitioning = false)"
                            ).fetchone()
                        break
                    except Exception as exc:
                        if (
                            "RequestTimeTooSkewed" not in str(exc)
                            or attempt == 7
                        ):
                            raise
            actual_rows = 0 if inserted is None else int(inserted[0])
            if actual_rows != expected_rows:
                raise CatalogueSchemaError(
                    f"repartition batch for {table_name!r} expected "
                    f"{expected_rows} rows, inserted {actual_rows}"
                )
        with self.lake.transaction():
            self.connection.execute(f"DROP TABLE {table}")
            self.connection.execute(
                f"ALTER TABLE {replacement} "
                f"RENAME TO {_quote_identifier(table_name)}"
            )
            self.set_commit_message(
                author="Atlas setup",
                message=f"Repartitioned {self.config.schema}.{table_name}",
                extra={"partition": f"bucket({bucket_count}, {column})"},
            )

    def _download_s3_files(
        self,
        paths: list[str],
        directory: Path,
    ) -> list[Path]:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError

        storage = self.config.storage
        url_style = str(getattr(storage, "url_style", "path") or "path")
        client_config = Config(
            max_pool_connections=16,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={
                "addressing_style": (
                    "virtual" if url_style == "vhost" else url_style
                )
            },
        )

        def new_client():
            return boto3.client(
                "s3",
                endpoint_url=getattr(storage, "endpoint", None),
                region_name=getattr(storage, "region", None),
                aws_access_key_id=getattr(storage, "key_id", None),
                aws_secret_access_key=getattr(
                    storage, "secret_access_key", None
                ),
                aws_session_token=getattr(storage, "session_token", None),
                config=client_config,
            )

        client = new_client()
        downloaded: list[Path] = []
        try:
            for index, path in enumerate(paths):
                parsed = urlsplit(path)
                destination = directory / f"{index}.parquet"
                for attempt in range(8):
                    try:
                        client.download_file(
                            parsed.netloc,
                            parsed.path.lstrip("/"),
                            str(destination),
                        )
                        break
                    except ClientError as exc:
                        if (
                            exc.response["Error"].get("Code")
                            != "RequestTimeTooSkewed"
                            or attempt == 7
                        ):
                            raise
                        client.close()
                        client = new_client()
                downloaded.append(destination)
        finally:
            client.close()
        return downloaded

    def _configure_inlining(self) -> None:
        """Disable metadata inlining for every Atlas-owned physical table."""

        for schema_name in (
            self.config.schema,
            INTERNAL_SCHEMA,
            "_atlas_materializations",
        ):
            for table in self.lake.table.list(schema_name=schema_name):
                self.connection.execute(
                    f"CALL {_quote_identifier(self.config.alias)}.set_option("
                    "'data_inlining_row_limit', 0, schema => ?, table_name => ?)",
                    [schema_name, table.table_name],
                )

    def _migrate_schema(self) -> None:
        """Apply small, idempotent DuckLake schema upgrades owned by Atlas."""

        for obsolete in ("run_crawl_usages", "run_manifests"):
            table = ".".join(
                _quote_identifier(value)
                for value in (self.config.alias, self.config.schema, obsolete)
            )
            with self.lake.transaction():
                self.connection.execute(f"DROP TABLE IF EXISTS {table}")

        info = self.lake.table.info(
            "crawls",
            schema_name=self.config.schema,
            include_summary=False,
            include_row_count=False,
            include_snapshots=False,
        )
        document_id = next(
            (column for column in info.columns if column.name == "document_id"),
            None,
        )
        if document_id is not None and not document_id.nullable:
            table = ".".join(
                _quote_identifier(value)
                for value in (self.config.alias, self.config.schema, "crawls")
            )
            with self.lake.transaction():
                self.connection.execute(
                    f"ALTER TABLE {table} ALTER COLUMN document_id DROP NOT NULL"
                )


    def validate_schema(self) -> None:
        """Validate columns without scanning catalogue data."""

        self._validate_physical_schema()
        self._validate_macros()

    def _validate_physical_schema(self) -> None:
        self._validate_schema_tables(self.config.schema, expected_columns())
        self._validate_schema_tables(INTERNAL_SCHEMA, expected_internal_columns())
        self._validate_layout()

    def _validate_schema_tables(
        self,
        schema_name: str,
        expected_tables: dict[str, dict[str, ColumnDef]],
    ) -> None:
        for table_name, expected in expected_tables.items():
            try:
                info = self.lake.table.info(
                    table_name,
                    schema_name=schema_name,
                    include_summary=False,
                    include_row_count=False,
                    include_snapshots=False,
                )
            except DuckLakeError as exc:
                raise CatalogueSchemaError(
                    f"catalogue table is missing or unreadable: "
                    f"{schema_name}.{table_name}"
                ) from exc
            actual_columns = [
                (column.name, _normalize_type(column.data_type), column.nullable)
                for column in info.columns
            ]
            normalized_expected = [
                (name, _normalize_type(_type_sql(column.data_type)), column.nullable)
                for name, column in expected.items()
            ]
            if actual_columns != normalized_expected:
                raise CatalogueSchemaError(
                    f"catalogue table {schema_name}.{table_name} does not match "
                    f"schema {CATALOGUE_SCHEMA_VERSION}: "
                    f"expected {normalized_expected!r}, got {actual_columns!r}"
                )

    def _validate_layout(self) -> None:
        metadata = _quote_identifier(f"__ducklake_metadata_{self.config.alias}")
        legacy_files = self.connection.execute(
            f"""
            SELECT s.schema_name, t.table_name, count(*)
            FROM {metadata}.ducklake_data_file AS df
            JOIN {metadata}.ducklake_table AS t ON t.table_id = df.table_id
            JOIN {metadata}.ducklake_schema AS s ON s.schema_id = t.schema_id
            JOIN {metadata}.ducklake_partition_info AS pi
              ON pi.table_id = t.table_id
            WHERE s.schema_name = ?
              AND t.table_name IN ('crawls', 'crawl_attempts', 'crawl_steps', 'elements')
              AND s.end_snapshot IS NULL
              AND t.end_snapshot IS NULL
              AND pi.end_snapshot IS NULL
              AND df.end_snapshot IS NULL
              AND df.begin_snapshot < pi.begin_snapshot
            GROUP BY s.schema_name, t.table_name
            """,
            [self.config.schema],
        ).fetchall()
        if legacy_files:
            raise CatalogueSchemaError(
                "greenfield catalogue reset required; active files predate the "
                f"declared partition contract: {legacy_files!r}"
            )

        crawls = self._partitioning("crawls")
        expected_crawls = (
            (0, "captured_at", "year"),
            (1, "captured_at", "month"),
            (2, "captured_at", "day"),
        )
        if crawls != expected_crawls:
            raise CatalogueSchemaError(
                f"catalogue table 'crawls' must be partitioned by "
                f"year/month/day(captured_at), got {crawls!r}"
            )
        for table_name in (CRAWL_ATTEMPTS_TABLE, CRAWL_STEPS_TABLE):
            actual = self._partitioning(table_name)
            expected = (
                (0, "started_at", "year"),
                (1, "started_at", "month"),
                (2, "started_at", "day"),
            )
            if actual != expected:
                raise CatalogueSchemaError(
                    f"catalogue table {table_name!r} must be partitioned by "
                    f"year/month/day(started_at), got {actual!r}"
                )
        elements = self._partitioning("elements")
        expected_elements = (
            (0, "document_id", f"bucket({ELEMENT_PARTITION_BUCKETS})")
        ,)
        if elements != expected_elements:
            raise CatalogueSchemaError(
                "catalogue table 'elements' must be partitioned by "
                f"bucket({ELEMENT_PARTITION_BUCKETS}, document_id), "
                f"got {elements!r}"
            )
        expected_sorts = {
            "urls": ("url_id",),
            "artifacts": ("artifact_id",),
            "documents": ("document_id",),
            "crawls": ("requested_url_id", "captured_at"),
            CRAWL_ATTEMPTS_TABLE: ("crawl_id", "attempt_number"),
            CRAWL_STEPS_TABLE: ("crawl_id", "attempt_number", "step_ordinal"),
            "elements": ("document_id", "element_index"),
        }
        for table_name, expected in expected_sorts.items():
            actual = self._sort_order(table_name)
            if actual != expected:
                raise CatalogueSchemaError(
                    f"catalogue table {table_name!r} must be sorted by "
                    f"{expected!r}, got {actual!r}"
                )
    def _validate_macros(self) -> None:
        rows = self.connection.execute(
            """
            SELECT function_name
            FROM duckdb_functions()
            WHERE database_name = ?
              AND schema_name = 'macros'
              AND function_type = 'macro'
              AND function_name IN (
                  'get_attribute', 'has_attribute', 'has_text', 'text_content',
                  'inner_html', 'normalize_url', 'readable_text', 'resolve_url',
                  'url_parts'
              )
            ORDER BY function_name
            """,
            [self.config.alias],
        ).fetchall()
        actual = [str(row[0]) for row in rows]
        expected = [
            "get_attribute",
            "has_attribute",
            "has_text",
            "inner_html",
            "normalize_url",
            "readable_text",
            "resolve_url",
            "text_content",
            "url_parts",
        ]
        if actual != expected:
            raise CatalogueSchemaError(
                f"catalogue scalar macros do not match the managed contract: "
                f"expected {expected!r}, got {actual!r}"
            )

    def latest_snapshot(self) -> int | None:
        return self.lake.snapshots.latest()

    def last_committed_snapshot(self) -> int | None:
        """Return the snapshot committed most recently by this connection."""

        alias = _quote_identifier(self.config.alias)
        row = self.connection.execute(
            f"SELECT id FROM {alias}.last_committed_snapshot()"
        ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    def set_commit_message(
        self,
        *,
        author: str,
        message: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Annotate the transaction's DuckLake snapshot for operations and audit."""

        alias = _quote_identifier(self.config.alias)
        self.connection.execute(
            f"CALL {alias}.set_commit_message(?, ?, extra_info => ?)",
            [author, message, json.dumps(extra or {}, separators=(",", ":"))],
        )

    def close(self) -> None:
        try:
            self.lake.close()
        finally:
            if self._temporary_directory is not None:
                self._temporary_directory.cleanup()
                self._temporary_directory = None

    def __enter__(self) -> Catalogue:
        _ = self.connection
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _normalize_type(value: str) -> str:
    normalized = " ".join(value.upper().split())
    normalized = normalized.replace("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ")
    normalized = normalized.replace("MAP(VARCHAR, VARCHAR)", "MAP(VARCHAR,VARCHAR)")
    return normalized


def _type_sql(value: str | SQLType) -> str:
    return value.sql() if isinstance(value, SQLType) else value


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
