"""Bounded backfill and shadow rebuilds for fixed materializations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from atlas.urls import normalize_url
from atlas.platform.catalogue import Catalogue
from atlas.ingestion.objects.html import RawHtmlRepository

from atlas.materialization.contracts import (
    DOCUMENT_PROJECTIONS,
    RELATIONS,
    VISIT_PROJECTIONS,
    ProjectionName,
)
from atlas.materialization.document_projection import (
    DocumentObservation,
    DocumentProjectionSource,
)
from atlas.materialization.document_sources import document_observation_rows
from atlas.materialization.document_workload import (
    DocumentProjectionOutput,
    document_stage_plan,
)
from atlas.materialization.lanes import MaterializationLanePool
from atlas.materialization.pipeline import StageSelection, execute_bounded_stage
from atlas.materialization.sql import sql_string, sql_string_list
from atlas.materialization.visit_workload import (
    commit_visit_projection_rows,
    merge_page_head_rows,
    merge_page_observation_rows,
    merge_page_rows,
    page_head_row,
    page_observation_row,
    page_row,
    rebuild_page_heads,
)

_LINK_ROLLUP_CURSOR_PREFIX = "link-rollups:"
_LINK_ROLLUP_SOURCE_BUDGET = 1_000
_LINK_ROLLUP_ROW_BUDGET = 100_000


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
    destinations = {
        stage: generation_table(stage, run_id)
        for stage in stages
    }
    catalogue.create_materialization_generations(
        {
            RELATIONS[stage]: table
            for stage, table in destinations.items()
        },
        generation_id=run_id.hex,
    )
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
    links_table = (
        destinations["links"]
        if {"links", "link_occurrences"}.issubset(enabled)
        else None
    )
    if after_snapshot is None:
        selector = lambda catalogue: _select_document_scan(
            catalogue,
            snapshot=source_snapshot,
            after_cursor=after_cursor,
            item_budget=item_budget,
            byte_budget=byte_budget,
            links_table=links_table,
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
            links_table=links_table,
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
    old_page_ids: set[str] = set()
    if (
        "page_observations" in enabled
        and replaced_visit_ids
    ):
        old_page_ids = {
            str(page_id)
            for (page_id,) in catalogue.trusted_remote_rows(
                "SELECT DISTINCT page_id::VARCHAR FROM material."
                f"{destinations.get('page_observations', 'page_observations')} "
                "WHERE visit_id IN "
                f"({sql_string_list(set(replaced_visit_ids))})"
            )
        }
    pages: list[dict[str, object]] = []
    if "pages" in enabled:
        by_url = {
            (url := normalize_url(str(row[2]))): page_row(url)
            for row in rows
        }
        pages = list(by_url.values())
        output_rows += len(pages)
    observations: list[dict[str, object]] = []
    if "page_observations" in enabled:
        observations = [
            page_observation_row(*row[:4])
            for row in rows
        ]
        output_rows += len(observations)
    heads: list[dict[str, object]] = []
    if "page_heads" in enabled:
        heads = [page_head_row(*row[:4]) for row in rows]
        output_rows += len(heads)
    if after_snapshot is None:
        commit_visit_projection_rows(
            catalogue,
            pages=pages,
            observations=observations,
            heads=heads,
            destinations=destinations,
        )
    else:
        if pages:
            merge_page_rows(
                catalogue,
                pages,
                table_name=destinations.get("pages", "pages"),
            )
        if "page_observations" in enabled:
            merge_page_observation_rows(
                catalogue,
                observations,
                table_name=destinations.get(
                    "page_observations", "page_observations"
                ),
                replaced_visit_ids=replaced_visit_ids,
            )
        if "page_heads" in enabled:
            heads_table = destinations.get("page_heads", "page_heads")
            merge_page_head_rows(
                catalogue,
                heads,
                table_name=heads_table,
            )
    if "page_heads" in enabled:
        heads_table = destinations.get("page_heads", "page_heads")
        affected_page_ids = old_page_ids | {
            str(row["page_id"]) for row in heads
        }
        if old_page_ids and "page_observations" in enabled:
            rebuild_page_heads(
                catalogue,
                affected_page_ids,
                observations_table=destinations.get(
                    "page_observations", "page_observations"
                ),
                heads_table=heads_table,
            )
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
    links_table: str | None = None,
) -> StageSelection[DocumentProjectionSource, DocumentProjectionOutput]:
    if _is_link_rollup_cursor(after_cursor):
        return _select_link_rollup_batch(
            catalogue,
            links_table=links_table,
            after_cursor=after_cursor,
        )
    document_ids = _document_ids_at_snapshot(
        catalogue,
        snapshot=snapshot,
        after_cursor=after_cursor,
        item_budget=item_budget,
    )
    rows = document_observation_rows(
        catalogue, document_ids, snapshot=snapshot
    )
    selected = _within_byte_budget(rows, byte_budget, size_index=5)
    if not document_ids:
        return _select_link_rollup_batch(
            catalogue,
            links_table=links_table,
            after_cursor=None,
        )
    return StageSelection(
        items=_projection_sources(catalogue, selected, snapshot=snapshot),
        initial_outputs=(),
        cursor=str(selected[-1][0]) if selected else None,
        done=False,
    )


def _select_document_catchup(
    catalogue: Catalogue,
    *,
    after_snapshot: int,
    through_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    byte_budget: int,
    links_table: str | None = None,
) -> StageSelection[DocumentProjectionSource, DocumentProjectionOutput]:
    if _is_link_rollup_cursor(after_cursor):
        return _select_link_rollup_batch(
            catalogue,
            links_table=links_table,
            after_cursor=after_cursor,
        )
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
    selected = _within_byte_budget(rows, byte_budget, size_index=5)
    if not document_ids:
        return _select_link_rollup_batch(
            catalogue,
            links_table=links_table,
            after_cursor=None,
        )
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
        (
            DocumentProjectionOutput(
                removed_hashes=frozenset(affected_hashes - live_hashes),
                replaced_document_ids=selected_ids,
            ),
        )
        if selected_ids or affected_hashes - live_hashes
        else ()
    )
    return StageSelection(
        items=_projection_sources(
            catalogue,
            selected,
            snapshot=through_snapshot,
        ),
        initial_outputs=corrections,
        cursor=str(selected[-1][0]) if selected else None,
        done=False,
    )


def _is_link_rollup_cursor(cursor: str | None) -> bool:
    return bool(cursor and cursor.startswith(_LINK_ROLLUP_CURSOR_PREFIX))


def _select_link_rollup_batch(
    catalogue: Catalogue,
    *,
    links_table: str | None,
    after_cursor: str | None,
) -> StageSelection[DocumentProjectionSource, DocumentProjectionOutput]:
    if links_table is None:
        return StageSelection(items=(), initial_outputs=(), done=True)
    after_source_page_id = (
        after_cursor.removeprefix(_LINK_ROLLUP_CURSOR_PREFIX)
        if _is_link_rollup_cursor(after_cursor)
        else None
    )
    predicate = (
        f"WHERE source_page_id::VARCHAR > "
        f"{sql_string(after_source_page_id)}"
        if after_source_page_id
        else ""
    )
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT source_page_id::VARCHAR, count(*)
        FROM material.{links_table}
        {predicate}
        GROUP BY source_page_id::VARCHAR
        ORDER BY source_page_id::VARCHAR
        LIMIT {_LINK_ROLLUP_SOURCE_BUDGET + 1}
        """
    )
    source_page_ids: list[str] = []
    selected_link_rows = 0
    for source_page_id, link_rows in rows[:_LINK_ROLLUP_SOURCE_BUDGET]:
        link_rows = int(link_rows)
        if (
            source_page_ids
            and selected_link_rows + link_rows > _LINK_ROLLUP_ROW_BUDGET
        ):
            break
        source_page_ids.append(str(source_page_id))
        selected_link_rows += link_rows
    done = (
        len(rows) <= _LINK_ROLLUP_SOURCE_BUDGET
        and len(source_page_ids) == len(rows)
    )
    cursor = (
        f"{_LINK_ROLLUP_CURSOR_PREFIX}{source_page_ids[-1]}"
        if source_page_ids
        else after_cursor
    )
    outputs = (
        (
            DocumentProjectionOutput(
                finalize_link_source_page_ids=frozenset(source_page_ids),
            ),
        )
        if source_page_ids
        else ()
    )
    return StageSelection(
        items=(),
        initial_outputs=outputs,
        cursor=cursor,
        done=done,
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
    for (
        document_id,
        visit_id,
        content_hash,
        raw_url,
        observed_at,
        content_bytes,
    ) in rows:
        if content_hash is None:
            continue
        value = str(content_hash)
        sizes[value] = max(sizes.get(value, 0), int(content_bytes))
        if raw_url is not None and observed_at is not None:
            observations.setdefault(value, []).append(
                DocumentObservation(
                    visit_id=str(visit_id),
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
               coalesce(effective_url, requested_url),
               coalesce(finished_at, observed_at, started_at, admitted_at)
        FROM ingest.visits AT (VERSION => {snapshot})
        WHERE coalesce(effective_url, requested_url) IS NOT NULL
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
               coalesce(effective_url, requested_url),
               coalesce(finished_at, observed_at, started_at, admitted_at)
        FROM ingest.visits AT (VERSION => {through_snapshot})
        WHERE visit_id IN ({sql_string_list(set(visit_ids))})
          AND coalesce(effective_url, requested_url) IS NOT NULL
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
