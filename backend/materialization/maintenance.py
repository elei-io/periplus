"""Bounded backfill and shadow rebuilds for fixed materializations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from control.urls import normalize_url
from repository.catalogue import Catalogue
from repository.objects.html import RawHtmlRepository

from materialization.contracts import (
    DOCUMENT_PROJECTIONS,
    RELATIONS,
    VISIT_PROJECTIONS,
    ProjectionName,
)
from materialization.document_projection import (
    DocumentObservation,
    DocumentProjectionSource,
)
from materialization.document_sources import document_observation_rows
from materialization.document_workload import (
    DocumentProjectionOutput,
    document_stage_plan,
)
from materialization.lanes import MaterializationLanePool
from materialization.pipeline import StageSelection, execute_bounded_stage
from materialization.sql import sql_string, sql_string_list
from materialization.visit_workload import (
    merge_page_observation_rows,
    merge_page_rows,
    page_observation_row,
    page_row,
)


@dataclass(frozen=True, slots=True)
class BatchResult:
    cursor: str | None
    done: bool
    source_items: int
    source_bytes: int
    output_rows: int


def generation_table(stage: ProjectionName, run_id: UUID) -> str:
    return f"_atlas_rebuild_{stage}_{run_id.hex[:16]}"


def prepare_rebuild(
    catalogue: Catalogue,
    run_id: UUID,
    stages: tuple[ProjectionName, ...],
) -> dict[ProjectionName, str]:
    destinations: dict[ProjectionName, str] = {}
    for stage in stages:
        table = generation_table(stage, run_id)
        catalogue.create_materialization_generation(RELATIONS[stage], table)
        destinations[stage] = table
    return destinations


def activate_rebuild(
    catalogue: Catalogue,
    run_id: UUID,
    destinations: dict[ProjectionName, str],
) -> None:
    catalogue.activate_materialization_generations(
        {
            RELATIONS[stage]: table
            for stage, table in destinations.items()
        },
        activation_id=run_id.hex,
    )


def finalize_rebuild(
    catalogue: Catalogue,
    run_id: UUID,
    destinations: dict[ProjectionName, str],
) -> None:
    catalogue.finalize_materialization_activation(
        tuple(RELATIONS[stage] for stage in destinations),
        activation_id=run_id.hex,
    )


async def materialize_document_batch(
    leases,
    lane_pool: MaterializationLanePool,
    html_repository: RawHtmlRepository,
    *,
    stages: tuple[ProjectionName, ...],
    source_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    byte_budget: int,
    destinations: dict[ProjectionName, str],
    after_snapshot: int | None = None,
    through_snapshot: int | None = None,
) -> BatchResult:
    enabled = frozenset(
        stage for stage in stages if stage in DOCUMENT_PROJECTIONS
    )
    if not enabled:
        raise ValueError("document batch requires a document projection")
    if after_snapshot is None:
        selector = lambda catalogue: _select_document_scan(
            catalogue,
            snapshot=source_snapshot,
            after_cursor=after_cursor,
            item_budget=item_budget,
            byte_budget=byte_budget,
        )
    else:
        if through_snapshot is None:
            raise ValueError("catch-up snapshot is required")
        selector = lambda catalogue: _select_document_catchup(
            catalogue,
            after_snapshot=after_snapshot,
            through_snapshot=through_snapshot,
            after_cursor=after_cursor,
            item_budget=item_budget,
            byte_budget=byte_budget,
        )
    result = await execute_bounded_stage(
        leases,
        lane_pool,
        document_stage_plan(
            html_repository,
            lane_pool.capacity,
            targets=destinations,
            select=selector,
            enabled_targets=enabled,
        ),
    )
    return BatchResult(
        cursor=result.cursor,
        done=result.done,
        source_items=result.source_items,
        source_bytes=result.source_bytes,
        output_rows=result.output_rows,
    )


def materialize_visit_batch(
    catalogue: Catalogue,
    *,
    stages: tuple[ProjectionName, ...],
    source_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    destinations: dict[ProjectionName, str],
    after_snapshot: int | None = None,
    through_snapshot: int | None = None,
) -> BatchResult:
    enabled = tuple(stage for stage in stages if stage in VISIT_PROJECTIONS)
    if not enabled:
        raise ValueError("visit batch requires a visit projection")
    if after_snapshot is None:
        rows = _visit_scan(
            catalogue,
            snapshot=source_snapshot,
            after_cursor=after_cursor,
            item_budget=item_budget,
        )
        replaced_visit_ids = frozenset()
    else:
        if through_snapshot is None:
            raise ValueError("catch-up snapshot is required")
        replaced_visit_ids, rows = _visit_catchup(
            catalogue,
            after_snapshot=after_snapshot,
            through_snapshot=through_snapshot,
            after_cursor=after_cursor,
            item_budget=item_budget,
        )
    output_rows = 0
    if "pages" in enabled:
        by_url = {
            (url := normalize_url(str(row[2]))): page_row(url)
            for row in rows
        }
        pages = list(by_url.values())
        merge_page_rows(
            catalogue,
            pages,
            table_name=destinations.get("pages", "pages"),
        )
        output_rows += len(pages)
    if "page_observations" in enabled:
        observations = [
            page_observation_row(*row[:4])
            for row in rows
        ]
        merge_page_observation_rows(
            catalogue,
            observations,
            table_name=destinations.get(
                "page_observations", "page_observations"
            ),
            replaced_visit_ids=replaced_visit_ids,
        )
        output_rows += len(observations)
    return BatchResult(
        cursor=(
            max(replaced_visit_ids)
            if replaced_visit_ids
            else (str(rows[-1][0]) if rows else None)
        ),
        done=not rows and not replaced_visit_ids,
        source_items=max(len(rows), len(replaced_visit_ids)),
        source_bytes=0,
        output_rows=output_rows,
    )


def _select_document_scan(
    catalogue: Catalogue,
    *,
    snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    byte_budget: int,
) -> StageSelection[DocumentProjectionSource, DocumentProjectionOutput]:
    document_ids = _document_ids_at_snapshot(
        catalogue,
        snapshot=snapshot,
        after_cursor=after_cursor,
        item_budget=item_budget,
    )
    rows = document_observation_rows(
        catalogue, document_ids, snapshot=snapshot
    )
    selected = _within_byte_budget(rows, byte_budget, size_index=4)
    return StageSelection(
        items=_projection_sources(catalogue, selected, snapshot=snapshot),
        cursor=str(selected[-1][0]) if selected else None,
        done=not document_ids,
    )


def _select_document_catchup(
    catalogue: Catalogue,
    *,
    after_snapshot: int,
    through_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    byte_budget: int,
) -> StageSelection[DocumentProjectionSource, DocumentProjectionOutput]:
    document_ids = _changed_document_ids(
        catalogue,
        after_snapshot=after_snapshot,
        through_snapshot=through_snapshot,
        after_cursor=after_cursor,
        item_budget=item_budget,
    )
    rows = document_observation_rows(
        catalogue,
        document_ids,
        snapshot=through_snapshot,
    )
    selected = _within_byte_budget(rows, byte_budget, size_index=4)
    selected_ids = frozenset(str(row[0]) for row in selected)
    affected_hashes = _changed_hashes_for_documents(
        catalogue,
        document_ids=selected_ids,
        after_snapshot=after_snapshot,
        through_snapshot=through_snapshot,
    )
    live_hashes = _live_document_hashes(
        catalogue,
        affected_hashes,
        snapshot=through_snapshot,
    )
    corrections = (
        DocumentProjectionOutput(
            removed_hashes=frozenset(affected_hashes - live_hashes),
            replaced_document_ids=selected_ids,
        ),
    ) if selected_ids or affected_hashes - live_hashes else ()
    return StageSelection(
        items=_projection_sources(
            catalogue,
            selected,
            snapshot=through_snapshot,
        ),
        initial_outputs=corrections,
        cursor=str(selected[-1][0]) if selected else None,
        done=not document_ids,
    )


def _document_ids_at_snapshot(
    catalogue: Catalogue,
    *,
    snapshot: int,
    after_cursor: str | None,
    item_budget: int,
) -> list[str]:
    cursor = _cursor_filter("document_id::VARCHAR", after_cursor)
    return [
        str(document_id)
        for (document_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT document_id::VARCHAR
            FROM ingest.documents AT (VERSION => {snapshot})
            WHERE lower(detected_media_type) = 'text/html'
              {cursor}
            ORDER BY document_id
            LIMIT {item_budget}
            """
        )
    ]


def _changed_document_ids(
    catalogue: Catalogue,
    *,
    after_snapshot: int,
    through_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
) -> list[str]:
    cursor = _cursor_filter("document_id::VARCHAR", after_cursor)
    return [
        str(document_id)
        for (document_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT document_id::VARCHAR
            FROM ducklake_table_changes(
              {sql_string(catalogue.config.alias)},
              'ingest', 'documents',
              {after_snapshot + 1}, {through_snapshot}
            )
            WHERE document_id IS NOT NULL
              {cursor}
            ORDER BY document_id
            LIMIT {item_budget}
            """
        )
    ]


def _changed_hashes_for_documents(
    catalogue: Catalogue,
    *,
    document_ids: frozenset[str],
    after_snapshot: int,
    through_snapshot: int,
) -> set[str]:
    if not document_ids:
        return set()
    return {
        str(content_hash)
        for (content_hash,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT content_sha256
            FROM ducklake_table_changes(
              {sql_string(catalogue.config.alias)},
              'ingest', 'documents',
              {after_snapshot + 1}, {through_snapshot}
            )
            WHERE document_id IN (
              {sql_string_list(set(document_ids))}
            )
              AND content_sha256 IS NOT NULL
            """
        )
    }


def _live_document_hashes(
    catalogue: Catalogue,
    content_hashes: set[str],
    *,
    snapshot: int,
) -> set[str]:
    if not content_hashes:
        return set()
    return {
        str(content_hash)
        for (content_hash,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT content_sha256
            FROM ingest.documents AT (VERSION => {snapshot})
            WHERE content_sha256 IN (
              {sql_string_list(content_hashes)}
            )
              AND lower(detected_media_type) = 'text/html'
            """
        )
    }


def _projection_sources(
    catalogue: Catalogue,
    rows: list[tuple],
    *,
    snapshot: int,
) -> tuple[DocumentProjectionSource, ...]:
    observations: dict[str, list[DocumentObservation]] = {}
    sizes: dict[str, int] = {}
    for document_id, content_hash, raw_url, observed_at, content_bytes in rows:
        if content_hash is None:
            continue
        value = str(content_hash)
        sizes[value] = max(sizes.get(value, 0), int(content_bytes))
        if raw_url is not None and observed_at is not None:
            observations.setdefault(value, []).append(
                DocumentObservation(
                    document_id=str(document_id),
                    source_url=normalize_url(str(raw_url)),
                    observed_at=observed_at,
                )
            )
        else:
            observations.setdefault(value, [])
    if not observations:
        return ()
    candidates = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, object_key, storage_encoding
        FROM (
          SELECT content_sha256, object_key, storage_encoding,
                 row_number() OVER (
                   PARTITION BY content_sha256
                   ORDER BY
                     CASE storage_encoding WHEN 'zstd' THEN 0 ELSE 1 END,
                     object_key
                 ) AS candidate_index
          FROM ingest.documents AT (VERSION => {snapshot})
          WHERE content_sha256 IN (
            {sql_string_list(set(observations))}
          )
            AND lower(detected_media_type) = 'text/html'
        )
        WHERE candidate_index = 1
        ORDER BY content_sha256
        """
    )
    return tuple(
        DocumentProjectionSource(
            content_sha256=str(content_hash),
            object_key=str(object_key),
            storage_encoding=str(storage_encoding),
            content_bytes=sizes.get(str(content_hash), 0),
            observations=tuple(
                sorted(
                    observations[str(content_hash)],
                    key=lambda item: item.document_id,
                )
            ),
        )
        for content_hash, object_key, storage_encoding in candidates
    )


def _visit_scan(
    catalogue: Catalogue,
    *,
    snapshot: int,
    after_cursor: str | None,
    item_budget: int,
) -> list[tuple]:
    cursor = _cursor_filter("visit_id::VARCHAR", after_cursor)
    return catalogue.trusted_remote_rows(
        f"""
        SELECT visit_id::VARCHAR, document_id,
               coalesce(effective_url, requested_url), observed_at
        FROM ingest.visits AT (VERSION => {snapshot})
        WHERE observed_at IS NOT NULL
          {cursor}
        ORDER BY visit_id
        LIMIT {item_budget}
        """
    )


def _visit_catchup(
    catalogue: Catalogue,
    *,
    after_snapshot: int,
    through_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
) -> tuple[frozenset[str], list[tuple]]:
    cursor = _cursor_filter("visit_id::VARCHAR", after_cursor)
    visit_ids = frozenset(
        str(visit_id)
        for (visit_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT visit_id::VARCHAR
            FROM ducklake_table_changes(
              {sql_string(catalogue.config.alias)},
              'ingest', 'visits',
              {after_snapshot + 1}, {through_snapshot}
            )
            WHERE visit_id IS NOT NULL
              {cursor}
            ORDER BY visit_id
            LIMIT {item_budget}
            """
        )
    )
    if not visit_ids:
        return visit_ids, []
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT visit_id::VARCHAR, document_id,
               coalesce(effective_url, requested_url), observed_at
        FROM ingest.visits AT (VERSION => {through_snapshot})
        WHERE visit_id IN ({sql_string_list(set(visit_ids))})
          AND observed_at IS NOT NULL
        ORDER BY visit_id
        """
    )
    return visit_ids, rows


def _cursor_filter(column: str, cursor: str | None) -> str:
    return "" if cursor is None else f"AND {column} > {sql_string(cursor)}"


def _within_byte_budget(
    rows: list[tuple],
    budget: int,
    *,
    size_index: int,
) -> list[tuple]:
    selected: list[tuple] = []
    consumed = 0
    for row in rows:
        size = int(row[size_index])
        if selected and consumed + size > budget:
            break
        selected.append(row)
        consumed += size
    return selected
