from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

import pyarrow as pa
from sqlalchemy import select

from config import get_int
from control.materialized_views.models import MaterializedView
from db.session import session_scope
from materialization.queue import MaterializationCommitJob, MaterializationFailureJob
from repository.catalogue.client import Catalogue
from repository.objects.config import object_store_from_env, staging_root_from_env

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
CommitOutcome = Literal["committed", "already_committed", "stale"]


def commit_scope(catalogue: Catalogue, job: MaterializationCommitJob) -> CommitOutcome:
    """Atomically replace one active definition scope and record coverage."""

    scope = job.scope
    _validate_identifiers(scope.target_table, scope.scope_column)
    store = object_store_from_env()
    with session_scope() as session:
        definition = session.scalar(
            select(MaterializedView)
            .where(MaterializedView.id == scope.materialized_view_id)
            .with_for_update()
        )
        if not _is_active_revision(definition, scope.definition_revision_id):
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
              AND scope_kind = 'document'
              AND scope_id = ?
              AND operation_id = ?
              AND status = 'succeeded'
            LIMIT 1
            """,
            [scope.definition_revision_id, scope.scope_id, scope.operation_id],
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


def record_scope_failure(catalogue: Catalogue, job: MaterializationFailureJob) -> bool:
    """Record one terminal failure only while its definition remains active."""

    scope = job.scope
    with session_scope() as session:
        definition = session.scalar(
            select(MaterializedView)
            .where(MaterializedView.id == scope.materialized_view_id)
            .with_for_update()
        )
        if not _is_active_revision(definition, scope.definition_revision_id):
            return False
        coverage = _qualified(
            catalogue, catalogue.config.schema, "materialization_scope_results"
        )
        succeeded = catalogue.connection.execute(
            f"SELECT 1 FROM {coverage} WHERE definition_revision_id = ? "
            "AND scope_kind = 'document' AND scope_id = ? "
            "AND status = 'succeeded' LIMIT 1",
            [scope.definition_revision_id, scope.scope_id],
        ).fetchone()
        if succeeded is not None:
            return False
        with catalogue.lake.transaction():
            catalogue.connection.execute(
                f"DELETE FROM {coverage} "
                "WHERE definition_revision_id = ? AND scope_kind = 'document' "
                "AND scope_id = ?",
                [scope.definition_revision_id, scope.scope_id],
            )
            catalogue.connection.execute(
                f"""
                INSERT INTO {coverage} (
                    materialized_view_id, definition_revision_id, scope_kind,
                    scope_id, operation_id, row_count, output_bytes, status,
                    error, started_at, completed_at, partition_value
                ) VALUES (?, ?, 'document', ?, ?, 0, 0, 'failed', ?, ?, ?, NULL)
                """,
                [
                    scope.materialized_view_id,
                    scope.definition_revision_id,
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
    definition: MaterializedView,
    job: MaterializationCommitJob,
    table: pa.Table,
    coverage: str,
) -> None:
    scope = job.scope
    connection = catalogue.connection
    relation_name = f"atlas_materialization_{scope.operation_id[:16]}"
    connection.register(relation_name, table)
    target = _qualified(catalogue, "materialized", scope.target_table)
    columns = ", ".join(_quote_identifier(value) for value in table.column_names)
    partition_value = _partition_value(table, definition.partition_column)
    previous_partition = connection.execute(
        f"SELECT partition_value FROM {coverage} "
        "WHERE materialized_view_id = ? AND scope_kind = 'document' "
        "AND scope_id = ? AND status = 'succeeded' "
        "AND partition_value IS NOT NULL ORDER BY completed_at DESC LIMIT 1",
        [scope.materialized_view_id, scope.scope_id],
    ).fetchone()
    delete_partition = previous_partition[0] if previous_partition else partition_value
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
                    f"SELECT {columns} FROM {_quote_identifier(relation_name)}"
                )
            connection.execute(
                f"DELETE FROM {coverage} "
                "WHERE definition_revision_id = ? AND scope_kind = 'document' "
                "AND scope_id = ?",
                [scope.definition_revision_id, scope.scope_id],
            )
            connection.execute(
                f"""
                INSERT INTO {coverage} (
                    materialized_view_id, definition_revision_id, scope_kind,
                    scope_id, operation_id, row_count, output_bytes, status,
                    error, started_at, completed_at, partition_value
                ) VALUES (?, ?, 'document', ?, ?, ?, ?, 'succeeded', NULL, ?, ?, ?)
                """,
                [
                    scope.materialized_view_id,
                    scope.definition_revision_id,
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
                    "materialized_view_id": str(scope.materialized_view_id),
                    "definition_revision_id": str(scope.definition_revision_id),
                    "operation_id": scope.operation_id,
                    "scope_id": scope.scope_id,
                    "row_count": job.row_count,
                },
            )
    finally:
        connection.unregister(relation_name)


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


def _is_active_revision(
    definition: MaterializedView | None, definition_revision_id
) -> bool:
    return bool(
        definition is not None
        and definition.archived_at is None
        and definition.deletion_requested_at is None
        and definition.refresh_mode == "scope_incremental"
        and definition.definition_revision_id == definition_revision_id
    )


def _validate_identifiers(table: str, column: str) -> None:
    for value in (table, column):
        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError(f"unsafe materialization identifier: {value!r}")


def _qualified(catalogue: Catalogue, schema: str, table: str) -> str:
    return ".".join(
        _quote_identifier(value) for value in (catalogue.config.alias, schema, table)
    )


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'
