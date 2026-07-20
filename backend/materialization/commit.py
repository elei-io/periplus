from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from hashlib import sha256
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Iterator, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select

from config import get_int
from control.catalogue_materializations.models import CatalogueMaterialization
from db.session import session_scope
from materialization.compute import ComputedMaterializationScope
from materialization.fencing import scope_job_is_current
from materialization.queue import MaterializationFailureJob, MaterializationScopeJob
from observability import materialization_metrics
from repository.catalogue.client import Catalogue
from repository.catalogue.schema import (
    INTERNAL_SCHEMA,
    MATERIALIZATION_COVERAGE_TABLE,
)
from repository.objects.config import staging_root_from_env

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
CommitOutcome = Literal["committed", "already_committed", "stale"]


def commit_scope(
    catalogue: Catalogue, job: ComputedMaterializationScope
) -> CommitOutcome:
    """Atomically replace one active definition scope and record coverage."""

    scope = job.scope
    _validate_identifiers(scope.target_table)
    with _definition_fence(scope) as definition:
        if not scope_job_is_current(definition, scope):
            return "stale"

        connection = catalogue.connection
        coverage = _qualified(
            catalogue, INTERNAL_SCHEMA, MATERIALIZATION_COVERAGE_TABLE
        )
        with materialization_metrics.operation_timer("commit.coverage_lookup"):
            already_committed = connection.execute(
                f"""
                SELECT 1
                FROM {coverage}
                WHERE definition_revision_id = ?
                  AND scope_kind = ?
                  AND scope_id = ?
                  AND operation_id = ?
                  AND status = 'succeeded'
                LIMIT 1
                """,
                [
                    scope.definition_revision_id,
                    scope.scope_kind,
                    scope.scope_id,
                    scope.operation_id,
                ],
            ).fetchone()
        if already_committed is not None:
            return "already_committed"

        with materialization_metrics.operation_timer("commit.arrow_verify"):
            _verify_local_result(job)
            with pa.memory_map(str(job.arrow_path), "r") as source:
                table = pa.ipc.open_file(source).read_all()
            if table.num_rows != job.row_count:
                raise ValueError(
                    "materialization result row count does not match the Arrow file"
                )
            if scope.scope_column not in table.column_names:
                raise ValueError(
                    f"materialization output must contain {scope.scope_column!r}"
                )
        _commit_table(catalogue, definition, job, table, coverage)
        return "committed"


@contextmanager
def _definition_fence(
    scope: MaterializationScopeJob,
) -> Iterator[CatalogueMaterialization | None]:
    with session_scope() as session:
        with materialization_metrics.operation_timer("commit.postgres_fence"):
            definition = session.scalar(
                select(CatalogueMaterialization)
                .where(CatalogueMaterialization.id == scope.materialization_id)
                .with_for_update()
            )
        yield definition


def record_scope_failure(catalogue: Catalogue, job: MaterializationFailureJob) -> bool:
    """Record one terminal failure only while its definition remains active."""

    scope = job.scope
    with session_scope() as session:
        definition = session.scalar(
            select(CatalogueMaterialization)
            .where(CatalogueMaterialization.id == scope.materialization_id)
            .with_for_update()
        )
        if not scope_job_is_current(definition, scope) and job.reason != "stale":
            return False
        coverage = _qualified(
            catalogue, INTERNAL_SCHEMA, MATERIALIZATION_COVERAGE_TABLE
        )
        succeeded = catalogue.connection.execute(
            f"SELECT 1 FROM {coverage} WHERE definition_revision_id = ? "
            "AND scope_kind = ? AND scope_id = ? "
            "AND status = 'succeeded' LIMIT 1",
            [scope.definition_revision_id, scope.scope_kind, scope.scope_id],
        ).fetchone()
        if succeeded is not None:
            return False
        with catalogue.lake.transaction():
            catalogue.connection.execute(
                f"DELETE FROM {coverage} "
                "WHERE definition_revision_id = ? AND scope_kind = ? "
                "AND scope_id = ?",
                [scope.definition_revision_id, scope.scope_kind, scope.scope_id],
            )
            catalogue.connection.execute(
                f"""
                INSERT INTO {coverage} (
                    materialization_id, definition_revision_id, scope_kind,
                    scope_id, operation_id, row_count, output_bytes, status,
                    error, started_at, completed_at, partition_value
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 'failed', ?, ?, ?, NULL)
                """,
                [
                    scope.materialization_id,
                    scope.definition_revision_id,
                    scope.scope_kind,
                    scope.scope_id,
                    scope.operation_id,
                    job.error[:4000],
                    job.started_at,
                    job.completed_at,
                ],
            )
        return True


def _commit_table(
    catalogue: Catalogue,
    definition: CatalogueMaterialization,
    job: ComputedMaterializationScope,
    table: pa.Table,
    coverage: str,
) -> None:
    scope = job.scope
    connection = catalogue.connection
    target = _qualified(catalogue, "_atlas_materializations", scope.target_table)
    columns = ", ".join(_quote_identifier(value) for value in table.column_names)
    partition_value = _partition_value(table, definition.partition_column)
    with materialization_metrics.operation_timer("commit.previous_partition_lookup"):
        previous_partition = connection.execute(
            f"SELECT partition_value FROM {coverage} "
            "WHERE materialization_id = ? AND scope_kind = ? "
            "AND scope_id = ? AND status = 'succeeded' "
            "AND partition_value IS NOT NULL ORDER BY completed_at DESC LIMIT 1",
            [scope.materialization_id, scope.scope_kind, scope.scope_id],
        ).fetchone()
    delete_partition = previous_partition[0] if previous_partition else partition_value
    staging_root = staging_root_from_env()
    staging_root.mkdir(parents=True, exist_ok=True)
    descriptor, parquet_value = tempfile.mkstemp(
        dir=staging_root,
        prefix=f"{scope.operation_id}.",
        suffix=".parquet",
    )
    os.close(descriptor)
    parquet_path = Path(parquet_value)
    with materialization_metrics.operation_timer("commit.parquet_encode"):
        pq.write_table(table, parquet_path)
    try:
        with _materialization_transaction(catalogue):
            delete_sql = (
                f"DELETE FROM {target} WHERE "
                f"{_quote_identifier(scope.scope_column)} = ?"
            )
            parameters: list[object] = [scope.scope_id]
            if definition.partition_column and delete_partition is not None:
                partition = _quote_identifier(definition.partition_column)
                delete_sql += (
                    f" AND {partition} >= CAST(? AS DATE) "
                    f"AND {partition} < CAST(? AS DATE) + INTERVAL 1 DAY"
                )
                parameters.extend([delete_partition, delete_partition])
            with materialization_metrics.operation_timer("commit.scope_delete"):
                connection.execute(delete_sql, parameters)
            if table.num_rows:
                with materialization_metrics.operation_timer("commit.scope_insert"):
                    connection.execute(
                        f"INSERT INTO {target} ({columns}) "
                        f"SELECT {columns} FROM read_parquet(?)",
                        [str(parquet_path)],
                    )
            with materialization_metrics.operation_timer("commit.coverage_write"):
                connection.execute(
                    f"DELETE FROM {coverage} "
                    "WHERE definition_revision_id = ? AND scope_kind = ? "
                    "AND scope_id = ?",
                    [scope.definition_revision_id, scope.scope_kind, scope.scope_id],
                )
                connection.execute(
                    f"""
                    INSERT INTO {coverage} (
                        materialization_id, definition_revision_id, scope_kind,
                        scope_id, operation_id, row_count, output_bytes, status,
                        error, started_at, completed_at, partition_value
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', NULL, ?, ?, ?)
                    """,
                    [
                        scope.materialization_id,
                        scope.definition_revision_id,
                        scope.scope_kind,
                        scope.scope_id,
                        scope.operation_id,
                        job.row_count,
                        job.output_bytes,
                        job.started_at,
                        datetime.now(UTC),
                        partition_value,
                    ],
                )
            with materialization_metrics.operation_timer("commit.commit_message"):
                catalogue.set_commit_message(
                    author="Atlas materialization",
                    message=(
                        f"Updated {scope.target_table} {scope.scope_kind} scope"
                    ),
                    extra={
                        "materialization_id": str(scope.materialization_id),
                        "definition_revision_id": str(scope.definition_revision_id),
                        "operation_id": scope.operation_id,
                        "scope_id": scope.scope_id,
                        "row_count": job.row_count,
                    },
                )
    finally:
        parquet_path.unlink(missing_ok=True)


@contextmanager
def _materialization_transaction(catalogue: Catalogue) -> Iterator[None]:
    with materialization_metrics.operation_timer("commit.transaction_begin"):
        transaction = catalogue.lake.transaction()
        transaction.__enter__()
    try:
        yield
    except BaseException:
        exception = sys.exc_info()
        with materialization_metrics.operation_timer("commit.transaction_rollback"):
            suppressed = transaction.__exit__(*exception)
        if not suppressed:
            raise
    else:
        with materialization_metrics.operation_timer("commit.transaction_finalize"):
            transaction.__exit__(None, None, None)


def _verify_local_result(job: ComputedMaterializationScope) -> None:
    maximum = get_int("ATLAS_MATERIALIZATION_MAX_OUTPUT_BYTES") * 2 + 1024 * 1024
    if job.file_bytes <= 0 or job.file_bytes > maximum:
        raise ValueError("materialization result Arrow file has an invalid size")
    if job.arrow_path.stat().st_size != job.file_bytes:
        raise ValueError("materialization result Arrow file size changed")
    digest = sha256()
    with job.arrow_path.open("rb") as content:
        while chunk := content.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != job.arrow_sha256:
        raise ValueError("materialization result Arrow file failed integrity validation")


def _partition_value(table: pa.Table, column: str | None) -> date | None:
    if column is None or table.num_rows == 0:
        return None
    if column not in table.column_names:
        raise ValueError(f"partition column {column!r} is missing from materialization output")
    values = {value for value in table[column].to_pylist() if value is not None}
    dates = {value.date() if isinstance(value, datetime) else value for value in values}
    if len(dates) > 1:
        raise ValueError("one materialization scope may not span multiple daily partitions")
    value = next(iter(dates), None)
    if value is not None and not isinstance(value, date):
        raise ValueError("materialization partition value must be a date or timestamp")
    return value


def _validate_identifiers(table: str) -> None:
    if not _SAFE_IDENTIFIER.fullmatch(table):
        raise ValueError(f"unsafe materialization identifier: {table!r}")


def _qualified(catalogue: Catalogue, schema: str, table: str) -> str:
    return ".".join(
        _quote_identifier(value) for value in (catalogue.config.alias, schema, table)
    )


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'
