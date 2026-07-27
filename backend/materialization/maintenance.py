"""Bounded backfill and shadow-rebuild batches for fixed Atlas projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from materialization.executor import (
    _apply_html_element_delta,
    _append_missing_link_rows,
    _jsonld_rows,
    _link_rows_for_documents,
    _link_source_rows,
    _merge_page_observation_rows,
    _merge_page_rows,
    _page_observation_row,
    _page_row,
    _project_html_documents,
    _replace_link_observation_rows,
    _replace_content_hash_slices,
    _sql_string,
    _sql_string_list,
)
from repository.catalogue import Catalogue
from repository.catalogue.schema import (
    HTML_ELEMENTS,
    JSONLD_VALUES,
    LINK_OBSERVATIONS,
    LINKS,
    PAGE_OBSERVATIONS,
    PAGES,
    RelationName,
)
from repository.objects.html import RawHtmlRepository
from control.urls import normalize_url


ProjectionName = Literal[
    "html_elements",
    "jsonld_values",
    "links",
    "link_observations",
    "pages",
    "page_observations",
]
Mode = Literal["backfill", "rebuild"]
STAGE_ORDER: tuple[ProjectionName, ...] = (
    "html_elements",
    "jsonld_values",
    "links",
    "link_observations",
    "pages",
    "page_observations",
)
RELATIONS: dict[ProjectionName, RelationName] = {
    "html_elements": HTML_ELEMENTS,
    "jsonld_values": JSONLD_VALUES,
    "links": LINKS,
    "link_observations": LINK_OBSERVATIONS,
    "pages": PAGES,
    "page_observations": PAGE_OBSERVATIONS,
}
PROJECTOR_VERSIONS: dict[ProjectionName, int] = {
    "html_elements": 1,
    "jsonld_values": 1,
    "links": 2,
    "link_observations": 1,
    "pages": 1,
    "page_observations": 1,
}


@dataclass(frozen=True, slots=True)
class BatchResult:
    cursor: str | None
    done: bool
    source_items: int
    source_bytes: int
    output_rows: int


def dependency_closure(
    requested: set[ProjectionName],
) -> tuple[ProjectionName, ...]:
    selected = set(requested)
    if "html_elements" in selected:
        selected.update(("jsonld_values", "links", "link_observations"))
    if selected & {"links", "link_observations"}:
        selected.update(("links", "link_observations"))
    return tuple(stage for stage in STAGE_ORDER if stage in selected)


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


def materialize_batch(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    *,
    stage: ProjectionName,
    source_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    byte_budget: int,
    destinations: dict[ProjectionName, str],
) -> BatchResult:
    if item_budget < 1:
        raise ValueError("item_budget must be positive")
    if byte_budget < 1:
        raise ValueError("byte_budget must be positive")
    return {
        "html_elements": _html_batch,
        "jsonld_values": _jsonld_batch,
        "links": _links_batch,
        "link_observations": _link_observations_batch,
        "pages": _pages_batch,
        "page_observations": _page_observations_batch,
    }[stage](
        catalogue,
        html_repository,
        source_snapshot=source_snapshot,
        after_cursor=after_cursor,
        item_budget=item_budget,
        byte_budget=byte_budget,
        destinations=destinations,
    )


def materialize_catchup_batch(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    *,
    stage: ProjectionName,
    after_snapshot: int,
    through_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    byte_budget: int,
    destinations: dict[ProjectionName, str],
) -> BatchResult:
    scope = {
        "after_snapshot": after_snapshot,
        "through_snapshot": through_snapshot,
        "after_cursor": after_cursor,
        "item_budget": item_budget,
        "byte_budget": byte_budget,
        "destinations": destinations,
    }
    if stage in {"html_elements", "jsonld_values"}:
        documents = _changed_document_hash_batch(catalogue, **scope)
        selected = _within_byte_budget(
            documents, byte_budget, size_index=3
        )
        hashes = {str(row[0]) for row in selected}
        if stage == "html_elements":
            output = _project_html_documents(
                html_repository,
                [
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in selected
                    if row[1] is not None and row[2] is not None
                ],
            )
            _apply_html_element_delta(
                catalogue,
                rows=output,
                removed_hashes=hashes,
                table_name=destinations["html_elements"],
            )
        else:
            elements_table = destinations.get(
                "html_elements", "html_elements"
            )
            source = (
                catalogue.trusted_remote_rows(
                    f"""
                    SELECT content_sha256, element_index, attributes, text_direct
                    FROM material.{elements_table}
                    WHERE content_sha256 IN ({_sql_string_list(hashes)})
                      AND tag = 'script'
                    ORDER BY content_sha256, element_index
                    """
                )
                if hashes
                else []
            )
            output = _jsonld_rows(source)
            _replace_content_hash_slices(
                catalogue,
                table_name=destinations["jsonld_values"],
                content_hashes=hashes,
                rows=output,
                variant_columns={"value"},
            )
        return _result(
            selected, documents, output, size_index=3
        )
    if stage in {"links", "link_observations"}:
        source = _changed_document_observation_batch(catalogue, **scope)
        selected = _within_byte_budget(source, byte_budget, size_index=4)
        live_documents = [
            (
                str(document_id),
                str(content_hash),
                normalize_url(str(url)),
                observed_at,
            )
            for document_id, content_hash, url, observed_at, _ in selected
            if content_hash is not None
            and url is not None
            and observed_at is not None
        ]
        output = _link_rows_for_documents(
            catalogue,
            live_documents,
            elements_table=destinations.get(
                "html_elements", "html_elements"
            ),
        )
        if stage == "links":
            with catalogue.remote_transaction():
                written = _append_missing_link_rows(
                    catalogue,
                    output.link_rows,
                    table_name=destinations["links"],
                )
        else:
            with catalogue.remote_transaction():
                written = _replace_link_observation_rows(
                    catalogue,
                    document_ids=frozenset(str(row[0]) for row in selected),
                    rows=output.observation_rows,
                    table_name=destinations["link_observations"],
                )
        return _result_count(
            selected,
            source,
            written,
            size_index=4,
        )
    visits = _changed_visit_batch(catalogue, **scope)
    if stage == "pages":
        by_url = {
            (url := normalize_url(str(row[2]))): _page_row(url)
            for row in visits
        }
        output = list(by_url.values())
        _merge_page_rows(
            catalogue,
            output,
            table_name=destinations["pages"],
        )
    else:
        output = [
            _page_observation_row(*row[:4])
            for row in visits
        ]
        _merge_page_observation_rows(
            catalogue,
            output,
            table_name=destinations["page_observations"],
        )
    return _result(visits, visits, output)


def _html_batch(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    **scope,
) -> BatchResult:
    documents = _document_hash_batch(catalogue, **_scan_scope(scope))
    selected = _within_byte_budget(documents, scope["byte_budget"], size_index=3)
    hashes = {str(row[0]) for row in selected}
    projected = _project_html_documents(
        html_repository,
        [(str(row[0]), str(row[1]), str(row[2])) for row in selected],
    )
    target = scope["destinations"].get("html_elements", "html_elements")
    _apply_html_element_delta(
        catalogue,
        rows=projected,
        removed_hashes=hashes,
        table_name=target,
    )
    return _result(selected, documents, projected, size_index=3)


def _jsonld_batch(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    **scope,
) -> BatchResult:
    documents = _document_hash_batch(catalogue, **_scan_scope(scope))
    selected = _within_byte_budget(documents, scope["byte_budget"], size_index=3)
    hashes = {str(row[0]) for row in selected}
    elements_table = scope["destinations"].get(
        "html_elements", "html_elements"
    )
    source = (
        catalogue.trusted_remote_rows(
            f"""
            SELECT content_sha256, element_index, attributes, text_direct
            FROM material.{elements_table}
            WHERE content_sha256 IN ({_sql_string_list(hashes)})
              AND tag = 'script'
            ORDER BY content_sha256, element_index
            """
        )
        if hashes
        else []
    )
    rows = _jsonld_rows(source)
    target = scope["destinations"].get("jsonld_values", "jsonld_values")
    _replace_content_hash_slices(
        catalogue,
        table_name=target,
        content_hashes=hashes,
        rows=rows,
        variant_columns={"value"},
    )
    return _result(selected, documents, rows, size_index=3)


def _links_batch(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    **scope,
) -> BatchResult:
    rows = _document_observation_batch(catalogue, **_scan_scope(scope))
    selected = _within_byte_budget(rows, scope["byte_budget"], size_index=4)
    documents = [
        (
            str(document_id),
            str(content_hash),
            normalize_url(str(raw_url)),
            observed_at,
        )
        for document_id, content_hash, raw_url, observed_at, _ in selected
        if content_hash is not None
        and raw_url is not None
        and observed_at is not None
    ]
    output = _link_rows_for_documents(
        catalogue,
        documents,
        elements_table=scope["destinations"].get(
            "html_elements", "html_elements"
        ),
    )
    target = scope["destinations"].get("links", "links")
    with catalogue.remote_transaction():
        written = _append_missing_link_rows(
            catalogue,
            output.link_rows,
            table_name=target,
        )
    return _result_count(
        selected,
        rows,
        written,
        size_index=4,
    )


def _link_observations_batch(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    **scope,
) -> BatchResult:
    rows = _document_observation_batch(catalogue, **_scan_scope(scope))
    selected = _within_byte_budget(rows, scope["byte_budget"], size_index=4)
    output = _link_rows_for_documents(
        catalogue,
        [
            (
                str(document_id),
                str(content_hash),
                normalize_url(str(raw_url)),
                observed_at,
            )
            for document_id, content_hash, raw_url, observed_at, _ in selected
            if content_hash is not None
            and raw_url is not None
            and observed_at is not None
        ],
        elements_table=scope["destinations"].get(
            "html_elements", "html_elements"
        ),
    )
    target = scope["destinations"].get(
        "link_observations", "link_observations"
    )
    with catalogue.remote_transaction():
        written = _replace_link_observation_rows(
            catalogue,
            document_ids=output.document_ids,
            rows=output.observation_rows,
            table_name=target,
        )
    return _result_count(
        selected,
        rows,
        written,
        size_index=4,
    )


def _pages_batch(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    **scope,
) -> BatchResult:
    rows = _visit_batch(catalogue, **_scan_scope(scope))
    output_by_url = {
        (url := normalize_url(str(row[1]))): _page_row(url)
        for row in rows
    }
    output = list(output_by_url.values())
    target = scope["destinations"].get("pages", "pages")
    _merge_page_rows(catalogue, output, table_name=target)
    return _result(rows, rows, output)


def _page_observations_batch(
    catalogue: Catalogue,
    _html_repository: RawHtmlRepository,
    **scope,
) -> BatchResult:
    cursor_filter = _cursor_filter("visit_id", scope["after_cursor"])
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT visit_id::VARCHAR, document_id,
               coalesce(effective_url, requested_url), observed_at
        FROM ingest.visits AT (VERSION => {scope["source_snapshot"]})
        WHERE observed_at IS NOT NULL
          {cursor_filter}
        ORDER BY visit_id
        LIMIT {scope["item_budget"]}
        """
    )
    output = [
        _page_observation_row(visit_id, document_id, url, observed_at)
        for visit_id, document_id, url, observed_at in rows
    ]
    target = scope["destinations"].get(
        "page_observations", "page_observations"
    )
    _merge_page_observation_rows(catalogue, output, table_name=target)
    return _result(rows, rows, output)


def _document_hash_batch(
    catalogue: Catalogue,
    *,
    source_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
) -> list[tuple]:
    cursor_filter = _cursor_filter("content_sha256", after_cursor)
    return catalogue.trusted_remote_rows(
        f"""
        WITH candidates AS (
          SELECT content_sha256, object_key, storage_encoding, content_bytes,
                 row_number() OVER (
                   PARTITION BY content_sha256
                   ORDER BY
                     CASE storage_encoding WHEN 'zstd' THEN 0 ELSE 1 END,
                     object_key
                 ) AS candidate_index
          FROM ingest.documents AT (VERSION => {source_snapshot})
          WHERE lower(detected_media_type) = 'text/html'
            {cursor_filter}
        )
        SELECT content_sha256, object_key, storage_encoding, content_bytes
        FROM candidates
        WHERE candidate_index = 1
        ORDER BY content_sha256
        LIMIT {item_budget}
        """
    )


def _document_observation_batch(
    catalogue: Catalogue,
    *,
    source_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
) -> list[tuple]:
    cursor_filter = _cursor_filter("document_id::VARCHAR", after_cursor)
    document_ids = [
        str(document_id)
        for (document_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT document_id::VARCHAR
            FROM ingest.documents AT (VERSION => {source_snapshot})
            WHERE lower(detected_media_type) = 'text/html'
              {cursor_filter}
            ORDER BY document_id
            LIMIT {item_budget}
            """
        )
    ]
    return _link_source_rows(
        catalogue,
        document_ids,
        snapshot=source_snapshot,
    )


def _changed_document_hash_batch(
    catalogue: Catalogue,
    *,
    after_snapshot: int,
    through_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    **_scope,
) -> list[tuple]:
    alias = _sql_string(catalogue.config.alias)
    cursor_filter = _cursor_filter("content_sha256", after_cursor)
    changed_hashes = [
        str(content_hash)
        for (content_hash,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT content_sha256
            FROM ducklake_table_changes(
              {alias}, 'ingest', 'documents',
              {after_snapshot + 1}, {through_snapshot}
            )
            WHERE lower(detected_media_type) = 'text/html'
              AND content_sha256 IS NOT NULL
              {cursor_filter}
            ORDER BY content_sha256
            LIMIT {item_budget}
            """
        )
    ]
    if not changed_hashes:
        return []
    candidates = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, object_key, storage_encoding, content_bytes
        FROM (
          SELECT content_sha256, object_key, storage_encoding, content_bytes,
                 row_number() OVER (
                   PARTITION BY content_sha256
                   ORDER BY
                     CASE storage_encoding WHEN 'zstd' THEN 0 ELSE 1 END,
                     object_key
                 ) AS candidate_index
          FROM ingest.documents AT (VERSION => {through_snapshot})
          WHERE content_sha256 IN (
            {_sql_string_list(set(changed_hashes))}
          )
            AND lower(detected_media_type) = 'text/html'
        )
        WHERE candidate_index = 1
        """
    )
    by_hash = {
        str(row[0]): (str(row[1]), str(row[2]), int(row[3]))
        for row in candidates
    }
    return [
        (
            content_hash,
            *by_hash[content_hash],
        )
        if content_hash in by_hash
        else (content_hash, None, None, 0)
        for content_hash in changed_hashes
    ]


def _changed_document_observation_batch(
    catalogue: Catalogue,
    *,
    after_snapshot: int,
    through_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    **_scope,
) -> list[tuple]:
    alias = _sql_string(catalogue.config.alias)
    cursor_filter = _cursor_filter("document_id::VARCHAR", after_cursor)
    document_ids = [
        str(document_id)
        for (document_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT document_id::VARCHAR
            FROM ducklake_table_changes(
              {alias}, 'ingest', 'documents',
              {after_snapshot + 1}, {through_snapshot}
            )
            WHERE document_id IS NOT NULL
              {cursor_filter}
            ORDER BY document_id
            LIMIT {item_budget}
            """
        )
    ]
    return _link_source_rows(
        catalogue,
        document_ids,
        snapshot=through_snapshot,
    )


def _changed_visit_batch(
    catalogue: Catalogue,
    *,
    after_snapshot: int,
    through_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
    **_scope,
) -> list[tuple]:
    alias = _sql_string(catalogue.config.alias)
    cursor_filter = _cursor_filter("visits.visit_id::VARCHAR", after_cursor)
    return catalogue.trusted_remote_rows(
        f"""
        WITH changed AS (
          SELECT DISTINCT visit_id
          FROM ducklake_table_changes(
            {alias}, 'ingest', 'visits',
            {after_snapshot + 1}, {through_snapshot}
          )
          WHERE observed_at IS NOT NULL
            AND change_type IN ('insert', 'update_postimage')
        )
        SELECT visits.visit_id::VARCHAR, visits.document_id,
               coalesce(visits.effective_url, visits.requested_url),
               visits.observed_at
        FROM changed
        JOIN ingest.visits AT (VERSION => {through_snapshot})
          AS visits USING (visit_id)
        WHERE true {cursor_filter}
        ORDER BY visits.visit_id
        LIMIT {item_budget}
        """
    )


def _visit_batch(
    catalogue: Catalogue,
    *,
    source_snapshot: int,
    after_cursor: str | None,
    item_budget: int,
) -> list[tuple]:
    cursor_filter = _cursor_filter("visit_id::VARCHAR", after_cursor)
    return catalogue.trusted_remote_rows(
        f"""
        SELECT visit_id::VARCHAR,
               coalesce(effective_url, requested_url)
        FROM ingest.visits AT (VERSION => {source_snapshot})
        WHERE observed_at IS NOT NULL
          {cursor_filter}
        ORDER BY visit_id
        LIMIT {item_budget}
        """
    )


def _cursor_filter(column: str, cursor: str | None) -> str:
    return "" if cursor is None else f"AND {column} > {_sql_string(cursor)}"


def _scan_scope(scope: dict) -> dict:
    return {
        "source_snapshot": scope["source_snapshot"],
        "after_cursor": scope["after_cursor"],
        "item_budget": scope["item_budget"],
    }


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


def _result(
    selected: list[tuple],
    scanned: list[tuple],
    output: list[dict[str, object]],
    *,
    size_index: int | None = None,
) -> BatchResult:
    source_bytes = (
        sum(int(row[size_index]) for row in selected)
        if size_index is not None
        else 0
    )
    return BatchResult(
        cursor=str(selected[-1][0]) if selected else None,
        done=len(scanned) == 0,
        source_items=len(selected),
        source_bytes=source_bytes,
        output_rows=len(output),
    )


def _result_count(
    selected: list[tuple],
    scanned: list[tuple],
    output_rows: int,
    *,
    size_index: int | None = None,
) -> BatchResult:
    source_bytes = (
        sum(int(row[size_index]) for row in selected)
        if size_index is not None
        else 0
    )
    return BatchResult(
        cursor=str(selected[-1][0]) if selected else None,
        done=len(scanned) == 0,
        source_items=len(selected),
        source_bytes=source_bytes,
        output_rows=output_rows,
    )
