"""Project and atomically append one visit-scoped materialization batch."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pyarrow as pa

from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.materialization.document_projection import (
    DocumentObservation,
    DocumentProjectionSource,
    build_visit_batch_context,
)
from periplus.materialization.registry import (
    BY_NAME,
    CONTENT_PRESENCE_PROJECTION,
    PROJECTIONS,
)
from periplus.materialization.sql import sql_string, sql_string_list
from periplus.materialization.store import MaterializationBatch, MaterializationRun
from periplus.platform.catalogue import Catalogue
from periplus.platform.catalogue.schema import expected_columns
from periplus.platform.catalogue.storage import (
    portable_registration_path,
    storage_protocol,
)
from periplus.urls import normalize_url


@dataclass(frozen=True, slots=True)
class BatchResult:
    source_items: int
    source_bytes: int
    output_rows: int
    output_bytes: int
    project_seconds: float
    parquet_seconds: float
    commit_seconds: float
    already_applied: bool = False
    superseded: bool = False


@dataclass(frozen=True, slots=True)
class PreparedFile:
    """One immutable Parquet object ready for DuckLake registration."""

    path: str
    size: int


@dataclass(frozen=True, slots=True)
class PreparedBatch:
    """Reusable immutable files for one remote append transaction."""

    source_items: int
    source_bytes: int
    output_rows: int
    output_bytes: int
    project_seconds: float
    parquet_seconds: float
    files: dict[str, tuple[PreparedFile, ...]]


def prepare_batch(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    run: MaterializationRun,
    batch: MaterializationBatch,
) -> PreparedBatch | BatchResult:
    applied = _applied_result(catalogue, batch.id)
    if applied is not None:
        return applied

    project_started = time.perf_counter()
    visits = _visit_rows(catalogue, batch)
    normalized_visits = {
        str(visit_id): (normalize_url(str(raw_url)), observed_at)
        for visit_id, _document_id, raw_url, observed_at in visits
    }
    sources, owned_hashes, documents = _document_sources(
        catalogue,
        run,
        batch,
        visits,
        normalized_visits,
    )
    context = build_visit_batch_context(
        html_repository,
        sources,
        visits=tuple(visits),
        documents=documents,
        content_output_hashes=owned_hashes,
    )
    source_bytes = sum(source.content_bytes for source in sources)
    project_seconds = time.perf_counter() - project_started

    outputs = {spec.name: spec.rows(context) for spec in PROJECTIONS}
    parquet_started = time.perf_counter()
    files: dict[str, tuple[PreparedFile, ...]] = {}
    file_set_id = uuid4().hex
    for spec in PROJECTIONS:
        files[spec.name] = _write_partitioned_parquet(
            catalogue,
            outputs[spec.name],
            run_id=run.id,
            batch_id=batch.id,
            file_set_id=file_set_id,
            table_name=spec.name,
        )
    parquet_seconds = time.perf_counter() - parquet_started
    return PreparedBatch(
        source_items=len(visits),
        source_bytes=source_bytes,
        output_rows=sum(table.num_rows for table in outputs.values()),
        output_bytes=sum(
            file.size
            for relation_files in files.values()
            for file in relation_files
        ),
        project_seconds=project_seconds,
        parquet_seconds=parquet_seconds,
        files=files,
    )


def commit_prepared_batch(
    catalogue: Catalogue,
    run: MaterializationRun,
    batch: MaterializationBatch,
    prepared: PreparedBatch,
    *,
    active_generation: bool = False,
) -> BatchResult:
    """Register final files and the replay marker in one DuckLake transaction."""

    commit_started = time.perf_counter()
    storage = _catalogue_storage(catalogue)
    with catalogue.remote_transaction():
        if active_generation and not _is_active_generation(catalogue, run.id):
            return _result(prepared, commit_started, superseded=True)
        if _is_applied(catalogue, batch.id):
            return _result(prepared, commit_started, already_applied=True)
        for spec in PROJECTIONS:
            for file in prepared.files[spec.name]:
                catalogue.trusted_remote_execute(
                    "CALL ducklake_add_data_files("
                    f"{sql_string(catalogue.config.alias)}, "
                    f"{sql_string(run.generation_tables[spec.name])}, "
                    f"{sql_string(storage.registration_path(file.path))}, "
                    "schema => 'material')"
                )
        catalogue.trusted_remote_execute(
            "INSERT INTO material._periplus_applied_batches VALUES ("
            f"UUID {sql_string(str(run.id))}, "
            f"UUID {sql_string(str(batch.id))}, "
            f"{batch.snapshot}, {prepared.source_items}, "
            f"{prepared.source_bytes}, {prepared.output_rows}, "
            f"{prepared.output_bytes}, now())"
        )
    result = _result(prepared, commit_started)
    logging.info(
        "materialization batch appended run=%s batch=%s visits=%s "
        "source_bytes=%s output_rows=%s output_bytes=%s "
        "project_seconds=%.3f parquet_seconds=%.3f commit_seconds=%.3f",
        run.id,
        batch.id,
        result.source_items,
        result.source_bytes,
        result.output_rows,
        result.output_bytes,
        result.project_seconds,
        result.parquet_seconds,
        result.commit_seconds,
    )
    return result


def process_batch(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    run: MaterializationRun,
    batch: MaterializationBatch,
) -> BatchResult:
    prepared = prepare_batch(catalogue, html_repository, run, batch)
    if isinstance(prepared, BatchResult):
        return prepared
    return commit_prepared_batch(catalogue, run, batch, prepared)


def _result(
    prepared: PreparedBatch,
    commit_started: float,
    *,
    already_applied: bool = False,
    superseded: bool = False,
) -> BatchResult:
    return BatchResult(
        source_items=prepared.source_items,
        source_bytes=prepared.source_bytes,
        output_rows=prepared.output_rows,
        output_bytes=prepared.output_bytes,
        project_seconds=prepared.project_seconds,
        parquet_seconds=prepared.parquet_seconds,
        commit_seconds=time.perf_counter() - commit_started,
        already_applied=already_applied,
        superseded=superseded,
    )


def _visit_rows(
    catalogue: Catalogue,
    batch: MaterializationBatch,
) -> list[tuple]:
    if not batch.visit_ids:
        return []
    return catalogue.trusted_remote_rows(
        f"""
        SELECT visit_id::VARCHAR, document_id,
               coalesce(effective_url, requested_url),
               coalesce(finished_at, observed_at, started_at, admitted_at)
        FROM ingest.visits AT (VERSION => {batch.snapshot})
        WHERE visit_id IN ({sql_string_list(set(batch.visit_ids))})
          AND coalesce(effective_url, requested_url) IS NOT NULL
        ORDER BY visit_id
        """
    )


def _document_sources(
    catalogue: Catalogue,
    run: MaterializationRun,
    batch: MaterializationBatch,
    visit_rows: list[tuple],
    normalized_visits: dict[str, tuple[str, object]],
) -> tuple[
    tuple[DocumentProjectionSource, ...],
    frozenset[str],
    tuple[tuple[object, ...], ...],
]:
    """Resolve sources and deterministic content owners before any commit."""

    document_ids = {
        str(row[1]) for row in visit_rows if row[1] is not None
    }
    if not document_ids:
        return (), frozenset(), ()
    documents = catalogue.trusted_remote_rows(
        f"""
        SELECT document_id::VARCHAR, visit_id::VARCHAR, content_sha256,
               object_key, storage_encoding, content_bytes
        FROM ingest.documents AT (VERSION => {batch.snapshot})
        WHERE document_id IN ({sql_string_list(document_ids)})
          AND lower(detected_media_type) = 'text/html'
        ORDER BY document_id
        """
    )
    by_hash: dict[str, dict[str, object]] = {}
    for document_id, visit_id, content_hash, object_key, encoding, size in documents:
        item = by_hash.setdefault(
            str(content_hash),
            {
                "object_key": str(object_key),
                "encoding": str(encoding),
                "size": int(size),
                "observations": [],
            },
        )
        visit = normalized_visits.get(str(visit_id))
        if visit is not None:
            item["observations"].append(
                DocumentObservation(
                    visit_id=str(visit_id),
                    document_id=str(document_id),
                    source_url=visit[0],
                    observed_at=visit[1],
                )
            )
    hashes = set(by_hash)
    existing_hashes: set[str] = set()
    presence = CONTENT_PRESENCE_PROJECTION
    if hashes and presence is not None:
        destination = run.generation_tables.get(presence.name)
        if destination is None:
            raise RuntimeError(
                "material generation has no content-presence relation"
            )
        existing_hashes = {
            str(row[0])
            for row in catalogue.trusted_remote_rows(
                "SELECT DISTINCT content_sha256 "
                f"FROM material.{_quote_identifier(destination)} "
                f"WHERE {presence.content_presence_predicate} "
                f"AND content_sha256 IN ({sql_string_list(hashes)})"
            )
        }
    new_hashes = hashes - existing_hashes
    owners = (
        catalogue.trusted_remote_rows(
            f"""
            SELECT content_sha256, min(document_id::VARCHAR)
            FROM ingest.documents AT (VERSION => {batch.snapshot})
            WHERE content_sha256 IN ({sql_string_list(new_hashes)})
              AND lower(detected_media_type) = 'text/html'
            GROUP BY content_sha256
            """
        )
        if new_hashes
        else []
    )
    owned_hashes = frozenset(
        str(content_hash)
        for content_hash, owner_id in owners
        if str(owner_id) in document_ids
    )
    return (
        tuple(
            DocumentProjectionSource(
                content_sha256=content_hash,
                object_key=str(item["object_key"]),
                storage_encoding=str(item["encoding"]),
                content_bytes=int(item["size"]),
                observations=tuple(item["observations"]),
            )
            for content_hash, item in sorted(by_hash.items())
        ),
        owned_hashes,
        tuple(tuple(item) for item in documents),
    )


def _write_partitioned_parquet(
    catalogue: Catalogue,
    table: pa.Table,
    *,
    run_id: UUID,
    batch_id: UUID,
    file_set_id: str | None = None,
    table_name: str,
) -> tuple[PreparedFile, ...]:
    """Validate and write one immutable file per registry partition."""

    if table.num_rows == 0:
        return ()
    spec = BY_NAME[table_name]
    if table.schema != spec.arrow_schema:
        raise ValueError(
            f"{table_name} output schema {table.schema} "
            f"does not match registry {spec.arrow_schema}"
        )
    storage = _catalogue_storage(catalogue)
    resolved_file_set_id = file_set_id or uuid4().hex
    registration = f"_periplus_batch_{batch_id.hex}_{table_name}"
    connection = catalogue.trusted_connection
    connection.register(registration, table)
    try:
        columns = expected_columns()[spec.relation]
        projections = ", ".join(
            _cast_projection(name, _column_type(column.data_type))
            for name, column in columns.items()
        )
        typed = f"(SELECT {projections} FROM {registration}) AS projected"
        expressions = tuple(
            transform.grouping_sql for transform in spec.partitioning
        )
        if expressions:
            selected = ", ".join(expressions)
            order = ", ".join(
                str(index + 1) for index in range(len(expressions))
            )
            partitions = connection.execute(
                f"SELECT DISTINCT {selected} FROM {typed} ORDER BY {order}"
            ).fetchall()
        else:
            partitions = [()]
        files: list[PreparedFile] = []
        for values in partitions:
            components = tuple(
                f"{transform.kind}={value}"
                for transform, value in zip(
                    spec.partitioning,
                    values,
                    strict=True,
                )
            )
            path = storage.join(
                "material",
                "data",
                run_id.hex,
                table_name,
                batch_id.hex,
                resolved_file_set_id,
                *(components or ("unpartitioned",)),
                "data.parquet",
            )
            storage.prepare_parent(path)
            order_by = ", ".join(spec.sort_order)
            predicates = " AND ".join(
                f"{expression} = {_partition_literal(value)}"
                for expression, value in zip(
                    expressions,
                    values,
                    strict=True,
                )
            ) or "true"
            connection.execute(
                f"COPY (SELECT * FROM {typed} "
                f"WHERE {predicates} "
                f"ORDER BY {order_by}) "
                f"TO {sql_string(path)} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            size = storage.file_size(connection, path)
            files.append(PreparedFile(path=path, size=size))
        return tuple(files)
    finally:
        connection.unregister(registration)


def _cast_projection(name: str, target_type: str) -> str:
    quoted = f'"{name}"'
    return f"{quoted}::{target_type} AS {quoted}"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _partition_literal(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return sql_string(str(value))


def _portable_registration_path(path: str | Path) -> str:
    """Expose the shared protocol rule for focused path tests."""

    return portable_registration_path(str(path))


def _catalogue_storage(catalogue):
    try:
        return catalogue.storage
    except AttributeError:
        return storage_protocol(catalogue.config)


def _column_type(value) -> str:
    renderer = getattr(value, "sql", None)
    return str(renderer() if callable(renderer) else value)


def _is_applied(catalogue: Catalogue, batch_id: UUID) -> bool:
    return bool(
        catalogue.trusted_remote_rows(
            "SELECT 1 FROM material._periplus_applied_batches "
            f"WHERE batch_id = UUID {sql_string(str(batch_id))} LIMIT 1"
        )
    )


def _is_active_generation(catalogue: Catalogue, generation_id: UUID) -> bool:
    return bool(
        catalogue.trusted_remote_rows(
            "SELECT 1 FROM material._periplus_materialization_state "
            f"WHERE generation_id = UUID {sql_string(str(generation_id))} "
            "LIMIT 1"
        )
    )


def _applied_result(
    catalogue: Catalogue,
    batch_id: UUID,
) -> BatchResult | None:
    rows = catalogue.trusted_remote_rows(
        "SELECT source_items, source_bytes, output_rows, output_bytes "
        "FROM material._periplus_applied_batches "
        f"WHERE batch_id = UUID {sql_string(str(batch_id))} LIMIT 1"
    )
    if not rows:
        return None
    return BatchResult(
        source_items=int(rows[0][0]),
        source_bytes=int(rows[0][1]),
        output_rows=int(rows[0][2]),
        output_bytes=int(rows[0][3]),
        project_seconds=0,
        parquet_seconds=0,
        commit_seconds=0,
        already_applied=True,
    )
