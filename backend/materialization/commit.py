from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select

from config import get_int
from control.catalogue_materializations.models import CatalogueMaterialization
from db.session import session_scope
from materialization.fencing import scope_job_is_current
from materialization.queue import MaterializationCommitJob, MaterializationFailureJob
from repository.catalogue.client import Catalogue
from repository.objects.config import object_store_from_env, staging_root_from_env

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
CommitOutcome = Literal["committed", "already_committed", "stale"]


def commit_scope(catalogue: Catalogue, job: MaterializationCommitJob) -> CommitOutcome:
    """Atomically replace one active definition scope and record coverage."""

    scope = job.scope
    _validate_identifiers(scope.target_table)
    store = object_store_from_env()
    with session_scope() as session:
        definition = session.scalar(
            select(CatalogueMaterialization)
            .where(CatalogueMaterialization.id == scope.materialization_id)
            .with_for_update()
        )
        if not scope_job_is_current(definition, scope):
            store.delete(job.staging_key)
            return "stale"

        connection = catalogue.connection
        coverage = _qualified(
            catalogue, catalogue.config.schema, "materialization_scope_results"
        )
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
            store.delete(job.staging_key)
            return "already_committed"

        path = _download_staging(job)
        try:
            with pa.memory_map(str(path), "r") as source:
                table = pa.ipc.open_file(source).read_all()
            if table.num_rows != job.row_count:
                raise ValueError(
                    "materialization staging row count does not match the Arrow file"
                )
            if scope.scope_column not in table.column_names:
                raise ValueError(
                    f"materialization output must contain {scope.scope_column!r}"
                )
            _commit_table(catalogue, definition, job, table, coverage)
        finally:
            path.unlink(missing_ok=True)
        store.delete(job.staging_key)
        return "committed"


def commit_scope_batch(
    catalogue: Catalogue, jobs: list[MaterializationCommitJob]
) -> dict[str, CommitOutcome]:
    """Commit compatible scopes through one Parquet append and DuckLake transaction."""

    if not jobs:
        return {}
    first = jobs[0].scope
    compatibility = (
        first.materialization_id,
        first.definition_revision_id,
        first.target_table,
        first.scope_kind,
        first.scope_column,
    )
    if any(
        (
            job.scope.materialization_id,
            job.scope.definition_revision_id,
            job.scope.target_table,
            job.scope.scope_kind,
            job.scope.scope_column,
        )
        != compatibility
        for job in jobs
    ):
        raise ValueError("materialization commit batch contains incompatible scopes")
    if len({job.scope.scope_id for job in jobs}) != len(jobs):
        raise ValueError("materialization commit batch contains duplicate scopes")
    _validate_identifiers(first.target_table)
    store = object_store_from_env()
    outcomes: dict[str, CommitOutcome] = {}
    local_paths: list[Path] = []
    committed_jobs: list[MaterializationCommitJob] = []
    tables: list[pa.Table] = []
    partition_values: list[date | None] = []
    with session_scope() as session:
        definition = session.scalar(
            select(CatalogueMaterialization)
            .where(CatalogueMaterialization.id == first.materialization_id)
            .with_for_update()
        )
        coverage = _qualified(
            catalogue, catalogue.config.schema, "materialization_scope_results"
        )
        for job in jobs:
            scope = job.scope
            if not scope_job_is_current(definition, scope):
                outcomes[scope.operation_id] = "stale"
                continue
            already_committed = catalogue.connection.execute(
                f"SELECT 1 FROM {coverage} WHERE definition_revision_id = ? "
                "AND scope_kind = ? AND scope_id = ? AND operation_id = ? "
                "AND status = 'succeeded' LIMIT 1",
                [
                    scope.definition_revision_id,
                    scope.scope_kind,
                    scope.scope_id,
                    scope.operation_id,
                ],
            ).fetchone()
            if already_committed is not None:
                outcomes[scope.operation_id] = "already_committed"
                continue
            try:
                path = _download_staging(job)
                local_paths.append(path)
                with pa.memory_map(str(path), "r") as source:
                    table = pa.ipc.open_file(source).read_all()
                if table.num_rows != job.row_count:
                    raise ValueError(
                        "materialization staging row count does not match the Arrow file"
                    )
                if scope.scope_column not in table.column_names:
                    raise ValueError(
                        f"materialization output must contain {scope.scope_column!r}"
                    )
                if tables and not table.schema.equals(tables[0].schema):
                    raise ValueError("materialization batch output schemas do not match")
                committed_jobs.append(job)
                tables.append(table)
                partition_values.append(
                    _partition_value(table, definition.partition_column)
                )
            except Exception:
                for local_path in local_paths:
                    local_path.unlink(missing_ok=True)
                raise
        try:
            if committed_jobs:
                _commit_tables(
                    catalogue,
                    definition,
                    committed_jobs,
                    tables,
                    partition_values,
                    coverage,
                )
                for job in committed_jobs:
                    outcomes[job.scope.operation_id] = "committed"
            for job in jobs:
                if job.scope.operation_id in outcomes:
                    store.delete(job.staging_key)
            return outcomes
        finally:
            for path in local_paths:
                path.unlink(missing_ok=True)


def _commit_tables(
    catalogue: Catalogue,
    definition: CatalogueMaterialization,
    jobs: list[MaterializationCommitJob],
    tables: list[pa.Table],
    partition_values: list[date | None],
    coverage: str,
) -> None:
    connection = catalogue.connection
    target = _qualified(catalogue, "_atlas_materializations", jobs[0].scope.target_table)
    combined = pa.concat_tables(tables) if len(tables) > 1 else tables[0]
    columns = ", ".join(_quote_identifier(value) for value in combined.column_names)
    scope_ids = [job.scope.scope_id for job in jobs]
    previous_rows = connection.execute(
        f"SELECT scope_id, arg_max(partition_value, completed_at) "
        f"FROM {coverage} WHERE materialization_id = ? AND scope_kind = ? "
        "AND status = 'succeeded' AND partition_value IS NOT NULL "
        "AND scope_id IN (SELECT unnest(?)) GROUP BY scope_id",
        [jobs[0].scope.materialization_id, jobs[0].scope.scope_kind, scope_ids],
    ).fetchall()
    previous_partitions = {str(scope_id): value for scope_id, value in previous_rows}
    delete_groups: dict[date | None, list[str]] = {}
    for job, partition_value in zip(jobs, partition_values, strict=True):
        delete_partition = previous_partitions.get(job.scope.scope_id, partition_value)
        delete_groups.setdefault(delete_partition, []).append(job.scope.scope_id)
    completed_at = datetime.now(UTC)
    coverage_rows = [
        {
            "materialization_id": str(job.scope.materialization_id),
            "definition_revision_id": str(job.scope.definition_revision_id),
            "scope_kind": job.scope.scope_kind,
            "scope_id": job.scope.scope_id,
            "operation_id": job.scope.operation_id,
            "row_count": job.row_count,
            "output_bytes": job.output_bytes,
            "started_at": job.started_at,
            "completed_at": completed_at,
            "partition_value": partition_value,
        }
        for job, partition_value in zip(jobs, partition_values, strict=True)
    ]
    coverage_table = pa.Table.from_pylist(coverage_rows)
    connection.register("atlas_materialization_coverage_batch", coverage_table)
    descriptor, parquet_value = tempfile.mkstemp(
        dir=staging_root_from_env(), prefix="materialization-batch.", suffix=".parquet"
    )
    os.close(descriptor)
    parquet_path = Path(parquet_value)
    pq.write_table(combined, parquet_path)
    try:
        with catalogue.lake.transaction():
            for delete_partition, grouped_scope_ids in delete_groups.items():
                delete_sql = (
                    f"DELETE FROM {target} WHERE "
                    f"CAST({_quote_identifier(jobs[0].scope.scope_column)} AS VARCHAR) "
                    "IN (SELECT unnest(?))"
                )
                parameters: list[object] = [grouped_scope_ids]
                if definition.partition_column and delete_partition is not None:
                    partition = _quote_identifier(definition.partition_column)
                    delete_sql += (
                        f" AND {partition} >= CAST(? AS DATE) "
                        f"AND {partition} < CAST(? AS DATE) + INTERVAL 1 DAY"
                    )
                    parameters.extend([delete_partition, delete_partition])
                connection.execute(delete_sql, parameters)
            if combined.num_rows:
                connection.execute(
                    f"INSERT INTO {target} ({columns}) "
                    f"SELECT {columns} FROM read_parquet(?)",
                    [str(parquet_path)],
                )
            connection.execute(
                f"DELETE FROM {coverage} WHERE definition_revision_id = ? "
                "AND scope_kind = ? AND scope_id IN (SELECT unnest(?))",
                [
                    jobs[0].scope.definition_revision_id,
                    jobs[0].scope.scope_kind,
                    scope_ids,
                ],
            )
            connection.execute(
                f"""
                INSERT INTO {coverage} (
                    materialization_id, definition_revision_id, scope_kind,
                    scope_id, operation_id, row_count, output_bytes, status,
                    error, started_at, completed_at, partition_value
                )
                SELECT CAST(materialization_id AS UUID),
                       CAST(definition_revision_id AS UUID), scope_kind, scope_id,
                       operation_id, row_count, output_bytes, 'succeeded', NULL,
                       started_at, completed_at, partition_value
                FROM atlas_materialization_coverage_batch
                """
            )
            catalogue.set_commit_message(
                author="Atlas materialization",
                message=(
                    f"Updated {jobs[0].scope.target_table} for {len(jobs)} scopes"
                ),
                extra={
                    "materialization_id": str(jobs[0].scope.materialization_id),
                    "definition_revision_id": str(
                        jobs[0].scope.definition_revision_id
                    ),
                    "operation_ids": [job.scope.operation_id for job in jobs],
                    "scope_ids": [job.scope.scope_id for job in jobs],
                    "row_count": sum(job.row_count for job in jobs),
                },
            )
    finally:
        connection.unregister("atlas_materialization_coverage_batch")
        parquet_path.unlink(missing_ok=True)


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
            catalogue, catalogue.config.schema, "materialization_scope_results"
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
    job: MaterializationCommitJob,
    table: pa.Table,
    coverage: str,
) -> None:
    scope = job.scope
    connection = catalogue.connection
    target = _qualified(catalogue, "_atlas_materializations", scope.target_table)
    columns = ", ".join(_quote_identifier(value) for value in table.column_names)
    partition_value = _partition_value(table, definition.partition_column)
    previous_partition = connection.execute(
        f"SELECT partition_value FROM {coverage} "
        "WHERE materialization_id = ? AND scope_kind = ? "
        "AND scope_id = ? AND status = 'succeeded' "
        "AND partition_value IS NOT NULL ORDER BY completed_at DESC LIMIT 1",
        [scope.materialization_id, scope.scope_kind, scope.scope_id],
    ).fetchone()
    delete_partition = previous_partition[0] if previous_partition else partition_value
    descriptor, parquet_value = tempfile.mkstemp(
        dir=staging_root_from_env(),
        prefix=f"{scope.operation_id}.",
        suffix=".parquet",
    )
    os.close(descriptor)
    parquet_path = Path(parquet_value)
    pq.write_table(table, parquet_path)
    try:
        with catalogue.lake.transaction():
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
            connection.execute(delete_sql, parameters)
            if table.num_rows:
                connection.execute(
                    f"INSERT INTO {target} ({columns}) "
                    f"SELECT {columns} FROM read_parquet(?)",
                    [str(parquet_path)],
                )
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
            catalogue.set_commit_message(
                author="Atlas materialization",
                message=f"Updated {scope.target_table} document scope",
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


def _download_staging(job: MaterializationCommitJob) -> Path:
    maximum = get_int("ATLAS_MATERIALIZATION_MAX_OUTPUT_BYTES") * 2 + 1024 * 1024
    if job.file_bytes <= 0 or job.file_bytes > maximum:
        raise ValueError("materialization Arrow file has an invalid size")
    store = object_store_from_env()
    if store.size(job.staging_key) != job.file_bytes:
        raise ValueError("materialization staging object size changed")
    root = staging_root_from_env() / "materialization-commit"
    root.mkdir(parents=True, exist_ok=True)
    descriptor, value = tempfile.mkstemp(dir=root, suffix=".arrow")
    digest = sha256()
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as output, store.open(job.staging_key) as content:
            while chunk := content.read(1024 * 1024):
                written += len(chunk)
                if written > job.file_bytes:
                    raise ValueError("materialization staging object grew while reading")
                digest.update(chunk)
                output.write(chunk)
        if written != job.file_bytes or digest.hexdigest() != job.staging_sha256:
            raise ValueError("materialization staging object failed integrity validation")
        return Path(value)
    except Exception:
        Path(value).unlink(missing_ok=True)
        raise


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
