"""Project and atomically commit one visit-scoped rebuild batch."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pyarrow as pa

from atlas.ingestion.objects.html import RawHtmlRepository
from atlas.materialization.document_projection import (
    DocumentObservation,
    DocumentProjectionSource,
    project_documents,
)
from atlas.materialization.sql import sql_string, sql_string_list
from atlas.materialization.store import MaterializationBatch, MaterializationRun
from atlas.materialization.visit_workload import (
    merge_page_head_rows,
    merge_page_rows,
    page_head_row,
    page_observation_row,
    page_row,
)
from atlas.platform.catalogue import Catalogue
from atlas.platform.catalogue.schema import PARTITION_BUCKETS, expected_columns
from atlas.urls import normalize_url

_LARGE_OUTPUTS = (
    "content_stats",
    "html_elements",
    "jsonld_values",
    "link_occurrences",
    "page_observations",
)
_PARTITION_KEYS = {
    "content_stats": "content_sha256",
    "html_elements": "content_sha256",
    "jsonld_values": "content_sha256",
    "link_occurrences": "link_id",
    "page_observations": "page_id",
}
_RELATIONS = {
    relation.table: relation
    for relation in expected_columns()
    if relation.schema == "material"
}


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
class PreparedBatch:
    """Reusable local projection and files for one remote commit."""

    source_items: int
    source_bytes: int
    output_rows: int
    output_bytes: int
    project_seconds: float
    parquet_seconds: float
    pages: tuple[dict[str, object], ...]
    heads: tuple[dict[str, object], ...]
    files: dict[str, tuple[Path, ...]]
    link_identities: Path | None


def discard_batch_staging(
    data_path: str,
    *,
    run_id: UUID,
    batch_id: UUID,
) -> None:
    """Remove only transient sidecars owned by one batch."""

    directory = (
        Path(data_path)
        / "material"
        / "staging"
        / run_id.hex
        / batch_id.hex
    )
    if directory.is_dir():
        shutil.rmtree(directory)


def discard_run_link_staging(data_path: str, *, run_id: UUID) -> None:
    """Remove link identity sidecars after activation or failed-run cleanup."""

    root = Path(data_path) / "material" / "staging" / run_id.hex
    if not root.is_dir():
        return
    for directory in root.glob("*/link_identities"):
        if directory.is_dir():
            shutil.rmtree(directory)


def discard_batch_link_staging(
    data_path: str,
    *,
    run_id: UUID,
    batch_id: UUID,
) -> None:
    directory = (
        Path(data_path)
        / "material"
        / "staging"
        / run_id.hex
        / batch_id.hex
        / "link_identities"
    )
    if directory.is_dir():
        shutil.rmtree(directory)


def link_identity_paths(data_path: str, *, run_id: UUID) -> tuple[Path, ...]:
    root = Path(data_path) / "material" / "staging" / run_id.hex
    return tuple(sorted(root.glob("*/link_identities/data.parquet")))


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
    visit_rows = _visit_rows(catalogue, batch)
    pages: dict[str, dict[str, object]] = {}
    observations: list[dict[str, object]] = []
    heads: list[dict[str, object]] = []
    normalized_visits: dict[str, tuple[str, object]] = {}
    for visit_id, document_id, raw_url, visit_at, *_rest in visit_rows:
        normalized_url = normalize_url(str(raw_url))
        page = pages.get(normalized_url)
        if page is None:
            page = page_row(normalized_url)
            pages[normalized_url] = page
        page_id = str(page["page_id"])
        observations.append(
            page_observation_row(
                visit_id,
                document_id,
                page_id,
                visit_at,
            )
        )
        heads.append(page_head_row(visit_id, page_id, visit_at))
        normalized_visits[str(visit_id)] = (normalized_url, visit_at)
    sources, owned_hashes = _document_sources(
        catalogue,
        run,
        batch,
        visit_rows,
        normalized_visits,
    )
    projection = project_documents(
        html_repository,
        sources,
        content_output_hashes=owned_hashes,
    )
    source_bytes = sum(source.content_bytes for source in sources)
    project_seconds = time.perf_counter() - project_started

    tables = {
        "content_stats": projection.content_stats,
        "html_elements": projection.html_elements,
        "jsonld_values": projection.jsonld_values,
        "link_occurrences": projection.link_occurrences,
        "page_observations": _page_observation_table(observations),
    }

    parquet_started = time.perf_counter()
    files: dict[str, tuple[Path, ...]] = {}
    output_bytes = 0
    for table_name in _LARGE_OUTPUTS:
        paths = _write_partitioned_parquet(
            catalogue,
            tables[table_name],
            run_id=run.id,
            batch_id=batch.id,
            table_name=table_name,
        )
        files[table_name] = paths
        output_bytes += sum(path.stat().st_size for path in paths)
    link_identities = _write_link_identity_parquet(
        catalogue,
        projection.links,
        run_id=run.id,
        batch_id=batch.id,
    )
    parquet_seconds = time.perf_counter() - parquet_started
    output_rows = (
        sum(table.num_rows for table in tables.values())
        + len(pages)
        + len(heads)
        + projection.links.num_rows
    )

    return PreparedBatch(
        source_items=len(visit_rows),
        source_bytes=source_bytes,
        output_rows=output_rows,
        output_bytes=output_bytes,
        project_seconds=project_seconds,
        parquet_seconds=parquet_seconds,
        pages=tuple(pages.values()),
        heads=tuple(heads),
        files=files,
        link_identities=link_identities,
    )


def commit_prepared_batch(
    catalogue: Catalogue,
    run: MaterializationRun,
    batch: MaterializationBatch,
    prepared: PreparedBatch,
    *,
    active_generation: bool = False,
) -> BatchResult:
    commit_started = time.perf_counter()
    with catalogue.remote_transaction():
        if active_generation and not _is_active_generation(catalogue, run.id):
            return BatchResult(
                source_items=prepared.source_items,
                source_bytes=prepared.source_bytes,
                output_rows=prepared.output_rows,
                output_bytes=prepared.output_bytes,
                project_seconds=prepared.project_seconds,
                parquet_seconds=prepared.parquet_seconds,
                commit_seconds=time.perf_counter() - commit_started,
                superseded=True,
            )
        if _is_applied(catalogue, batch.id):
            return BatchResult(
                source_items=prepared.source_items,
                source_bytes=prepared.source_bytes,
                output_rows=prepared.output_rows,
                output_bytes=prepared.output_bytes,
                project_seconds=prepared.project_seconds,
                parquet_seconds=prepared.parquet_seconds,
                commit_seconds=time.perf_counter() - commit_started,
                already_applied=True,
            )
        for table_name, paths in prepared.files.items():
            for path in paths:
                catalogue.trusted_remote_execute(
                    "CALL ducklake_add_data_files("
                    f"{sql_string(catalogue.config.alias)}, "
                    f"{sql_string(run.generation_tables[table_name])}, "
                    f"{sql_string(str(path))}, schema => 'material')"
                )
        merge_page_rows(
            catalogue,
            list(prepared.pages),
            table_name=run.generation_tables["pages"],
            transaction=False,
        )
        merge_page_head_rows(
            catalogue,
            list(prepared.heads),
            table_name=run.generation_tables["page_heads"],
            transaction=False,
        )
        if active_generation and prepared.link_identities is not None:
            merge_live_links(
                catalogue,
                links_table=run.generation_tables["links"],
                occurrences_table=run.generation_tables["link_occurrences"],
                identities=prepared.link_identities,
            )
        catalogue.trusted_remote_execute(
            "INSERT INTO material._atlas_applied_batches VALUES ("
            f"UUID {sql_string(str(run.id))}, "
            f"UUID {sql_string(str(batch.id))}, "
            f"{batch.snapshot}, {prepared.source_items}, "
            f"{prepared.source_bytes}, {prepared.output_rows}, "
            f"{prepared.output_bytes}, now())"
        )
    commit_seconds = time.perf_counter() - commit_started
    logging.info(
        "materialization batch committed run=%s batch=%s visits=%s "
        "source_bytes=%s output_rows=%s output_bytes=%s "
        "project_seconds=%.3f parquet_seconds=%.3f commit_seconds=%.3f",
        run.id,
        batch.id,
        prepared.source_items,
        prepared.source_bytes,
        prepared.output_rows,
        prepared.output_bytes,
        prepared.project_seconds,
        prepared.parquet_seconds,
        commit_seconds,
    )
    return BatchResult(
        source_items=prepared.source_items,
        source_bytes=prepared.source_bytes,
        output_rows=prepared.output_rows,
        output_bytes=prepared.output_bytes,
        project_seconds=prepared.project_seconds,
        parquet_seconds=prepared.parquet_seconds,
        commit_seconds=commit_seconds,
    )


def process_batch(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    run: MaterializationRun,
    batch: MaterializationBatch,
) -> BatchResult:
    """Prepare and commit a batch once; runtime retries only the commit."""

    prepared = prepare_batch(catalogue, html_repository, run, batch)
    if isinstance(prepared, BatchResult):
        return prepared
    return commit_prepared_batch(catalogue, run, batch, prepared)


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
) -> tuple[tuple[DocumentProjectionSource, ...], frozenset[str]]:
    document_ids = {
        str(row[1]) for row in visit_rows if row[1] is not None
    }
    if not document_ids:
        return (), frozenset()
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
        key = str(content_hash)
        item = by_hash.setdefault(
            key,
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
    owner_rows = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, min(document_id::VARCHAR)
        FROM ingest.documents AT (VERSION => {batch.snapshot})
        WHERE content_sha256 IN ({sql_string_list(hashes)})
          AND lower(detected_media_type) = 'text/html'
        GROUP BY content_sha256
        """
    ) if hashes else []
    candidates = {
        str(content_hash)
        for content_hash, owner_id in owner_rows
        if str(owner_id) in document_ids
    }
    existing = (
        {
            str(row[0])
            for row in catalogue.trusted_remote_rows(
                "SELECT content_sha256 FROM material."
                f"{run.generation_tables['content_stats']} "
                f"WHERE content_sha256 IN ({sql_string_list(candidates)})"
            )
        }
        if candidates
        else set()
    )
    owned_hashes = frozenset(candidates - existing)
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
    )


def _page_observation_table(rows: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                pa.field("page_id", pa.string(), nullable=False),
                pa.field("visit_id", pa.string(), nullable=False),
                pa.field("document_id", pa.string()),
                pa.field("visit_at", pa.timestamp("us", tz="UTC"), nullable=False),
            ]
        ),
    )


def _write_partitioned_parquet(
    catalogue: Catalogue,
    table: pa.Table,
    *,
    run_id: UUID,
    batch_id: UUID,
    table_name: str,
) -> tuple[Path, ...]:
    if table.num_rows == 0:
        return ()
    root = (
        Path(catalogue.config.data_path)
        / "material"
        / "data"
        / run_id.hex
        / table_name
        / batch_id.hex
    )
    root.mkdir(parents=True, exist_ok=True)
    registration = f"_atlas_batch_{batch_id.hex}"
    connection = catalogue.trusted_connection
    connection.register(registration, table)
    try:
        columns = expected_columns()[_RELATIONS[table_name]]
        projections = ", ".join(
            _cast_projection(name, _column_type(column.data_type))
            for name, column in columns.items()
        )
        key = _PARTITION_KEYS[table_name]
        typed = f"(SELECT {projections} FROM {registration}) AS projected"
        buckets = [
            int(row[0])
            for row in connection.execute(
                "SELECT DISTINCT "
                f"(murmur3_32({key}) & 2147483647) % {PARTITION_BUCKETS} "
                f"FROM {typed} ORDER BY 1"
            ).fetchall()
        ]
        paths: list[Path] = []
        for bucket in buckets:
            directory = root / f"bucket={bucket}"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "data.parquet"
            connection.execute(
                f"COPY (SELECT * FROM {typed} "
                f"WHERE (murmur3_32({key}) & 2147483647) "
                f"% {PARTITION_BUCKETS} = {bucket}) "
                f"TO {sql_string(str(path))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            paths.append(path)
        return tuple(paths)
    finally:
        connection.unregister(registration)


def _write_link_identity_parquet(
    catalogue: Catalogue,
    table: pa.Table,
    *,
    run_id: UUID,
    batch_id: UUID,
) -> Path | None:
    """Stage one narrow link-identity sidecar for generation finalization."""

    if table.num_rows == 0:
        return None
    directory = (
        Path(catalogue.config.data_path)
        / "material"
        / "staging"
        / run_id.hex
        / batch_id.hex
        / "link_identities"
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "data.parquet"
    registration = f"_atlas_link_identities_{batch_id.hex}"
    connection = catalogue.trusted_connection
    connection.register(registration, table)
    try:
        columns = expected_columns()[_RELATIONS["links"]]
        projections = ", ".join(
            _cast_projection(name, _column_type(column.data_type))
            for name, column in columns.items()
        )
        connection.execute(
            f"COPY (SELECT {projections} FROM {registration}) "
            f"TO {sql_string(str(path))} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        connection.unregister(registration)
    return path


def populate_final_links(
    catalogue: Catalogue,
    *,
    links_table: str,
    occurrences_table: str,
    identities: tuple[Path, ...],
) -> None:
    """Build the link dimension once from completed occurrence evidence."""

    if not identities:
        return
    paths = ", ".join(sql_string(str(path)) for path in identities)
    catalogue.trusted_remote_execute(
        f"""
        INSERT INTO material.{links_table}
        WITH identities AS (
          SELECT DISTINCT
                 link_id::UUID AS link_id,
                 source_page_id::UUID AS source_page_id,
                 target_page_id::UUID AS target_page_id,
                 source_url, target_url, relation_scope
          FROM read_parquet([{paths}])
        ),
        rollup AS (
          SELECT link_id, min(observed_at) AS first_seen_at,
                 max(observed_at) AS last_seen_at,
                 count(DISTINCT visit_id) AS visit_count,
                 count(DISTINCT content_sha256) AS distinct_content_count,
                 count(*) AS occurrence_count
          FROM material.{occurrences_table}
          GROUP BY link_id
        )
        SELECT identity.link_id, identity.source_page_id,
               identity.target_page_id, identity.source_url,
               identity.target_url, identity.relation_scope,
               rollup.first_seen_at, rollup.last_seen_at,
               rollup.visit_count, rollup.distinct_content_count,
               rollup.occurrence_count
        FROM identities AS identity
        JOIN rollup USING (link_id)
        """
    )


def merge_live_links(
    catalogue: Catalogue,
    *,
    links_table: str,
    occurrences_table: str,
    identities: Path,
) -> None:
    """Upsert exact rollups for links touched by one committed live batch."""

    path = sql_string(str(identities))
    catalogue.trusted_remote_execute(
        f"""
        MERGE INTO material.{links_table} AS target
        USING (
          WITH identities AS (
            SELECT DISTINCT
                   link_id::UUID AS link_id,
                   source_page_id::UUID AS source_page_id,
                   target_page_id::UUID AS target_page_id,
                   source_url, target_url, relation_scope
            FROM read_parquet({path})
          ),
          rollup AS (
            SELECT occurrence.link_id,
                   min(occurrence.observed_at) AS first_seen_at,
                   max(occurrence.observed_at) AS last_seen_at,
                   count(DISTINCT occurrence.visit_id) AS visit_count,
                   count(DISTINCT occurrence.content_sha256)
                     AS distinct_content_count,
                   count(*) AS occurrence_count
            FROM material.{occurrences_table} AS occurrence
            JOIN identities USING (link_id)
            GROUP BY occurrence.link_id
          )
          SELECT identity.link_id, identity.source_page_id,
                 identity.target_page_id, identity.source_url,
                 identity.target_url, identity.relation_scope,
                 rollup.first_seen_at, rollup.last_seen_at,
                 rollup.visit_count, rollup.distinct_content_count,
                 rollup.occurrence_count
          FROM identities AS identity
          JOIN rollup USING (link_id)
        ) AS delta
          ON target.link_id = delta.link_id
        WHEN MATCHED THEN UPDATE SET
          source_page_id = delta.source_page_id,
          target_page_id = delta.target_page_id,
          source_url = delta.source_url,
          target_url = delta.target_url,
          relation_scope = delta.relation_scope,
          first_seen_at = delta.first_seen_at,
          last_seen_at = delta.last_seen_at,
          visit_count = delta.visit_count,
          distinct_content_count = delta.distinct_content_count,
          occurrence_count = delta.occurrence_count
        WHEN NOT MATCHED THEN INSERT
        """
    )


def _cast_projection(name: str, target_type: str) -> str:
    quoted = f'"{name}"'
    return f"{quoted}::{target_type} AS {quoted}"


def _column_type(value) -> str:
    renderer = getattr(value, "sql", None)
    return str(renderer() if callable(renderer) else value)


def _is_applied(catalogue: Catalogue, batch_id: UUID) -> bool:
    return bool(
        catalogue.trusted_remote_rows(
            "SELECT 1 FROM material._atlas_applied_batches "
            f"WHERE batch_id = UUID {sql_string(str(batch_id))} LIMIT 1"
        )
    )


def _is_active_generation(catalogue: Catalogue, generation_id: UUID) -> bool:
    return bool(
        catalogue.trusted_remote_rows(
            "SELECT 1 FROM material._atlas_materialization_state "
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
        "FROM material._atlas_applied_batches "
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
