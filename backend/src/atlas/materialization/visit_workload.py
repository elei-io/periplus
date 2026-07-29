"""Visit-owned page and observation materialization."""

from __future__ import annotations

from urllib.parse import urlsplit

import pyarrow as pa
import tldextract
from atlas.urls import normalize_url
from atlas.platform.catalogue import Catalogue, page_id_for
from atlas.platform.catalogue.schema import (
    PAGES,
    PAGE_HEADS,
    PAGE_OBSERVATIONS,
    RelationName,
)

from atlas.materialization.bulk import (
    MaterialMutation,
    commit_material_mutations,
)
from atlas.materialization.sql import (
    sql_string,
    sql_string_list,
)

_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def changed_visit_rows(
    catalogue: Catalogue,
    *,
    start_snapshot: int,
    end_snapshot: int,
) -> tuple[frozenset[str], list[tuple]]:
    alias = sql_string(catalogue.config.alias)
    visit_ids = frozenset(
        str(visit_id)
        for (visit_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT visit_id::VARCHAR
            FROM ducklake_table_changes(
              {alias}, 'ingest', 'visits',
              {start_snapshot}, {end_snapshot}
            )
            WHERE visit_id IS NOT NULL
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
        FROM ingest.visits AT (VERSION => {end_snapshot})
        WHERE visit_id IN ({sql_string_list(set(visit_ids))})
        ORDER BY visit_id
        """
    )
    return visit_ids, rows


def page_row(normalized_url: str) -> dict[str, object]:
    parsed = urlsplit(normalized_url)
    result = _TLD_EXTRACT(parsed.hostname or "")
    return {
        "page_id": str(page_id_for(normalized_url)),
        "normalized_url": normalized_url,
        "scheme": parsed.scheme,
        "hostname": parsed.hostname,
        "port": parsed.port,
        "path": parsed.path,
        "query": parsed.query or None,
        "registrable_domain": (
            result.top_domain_under_public_suffix or None
        ),
    }


def page_observation_row(
    visit_id: object,
    document_id: object,
    raw_url: object,
    visit_at: object,
) -> dict[str, object]:
    normalized_url = normalize_url(str(raw_url))
    return {
        "page_id": str(page_id_for(normalized_url)),
        "visit_id": str(visit_id),
        "document_id": (
            str(document_id) if document_id is not None else None
        ),
        "visit_at": visit_at,
    }


def page_head_row(
    visit_id: object,
    _document_id: object,
    raw_url: object,
    visit_at: object,
) -> dict[str, object]:
    normalized_url = normalize_url(str(raw_url))
    return {
        "page_id": str(page_id_for(normalized_url)),
        "visit_id": str(visit_id),
        "visit_at": visit_at,
    }


def merge_page_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "pages",
) -> None:
    if not rows:
        return
    _replace_rows(
        catalogue,
        relation=PAGES,
        table_name=table_name,
        rows=rows,
        match_columns=("page_id",),
    )


def merge_page_observation_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "page_observations",
    replaced_visit_ids: frozenset[str] = frozenset(),
) -> None:
    if not rows and not replaced_visit_ids:
        return
    row_visit_ids = frozenset(str(row["visit_id"]) for row in rows)
    mutations = []
    deleted_only = replaced_visit_ids - row_visit_ids
    if deleted_only:
        mutations.append(
            _delete_keys_mutation(
                file_id="00-delete-page-observations",
                relation=PAGE_OBSERVATIONS,
                table_name=table_name,
                column_name="visit_id",
                values=deleted_only,
            )
        )
    if rows:
        mutations.append(
            _replace_mutation(
                file_id="10-replace-page-observations",
                relation=PAGE_OBSERVATIONS,
                table_name=table_name,
                rows=rows,
                match_columns=("visit_id",),
            )
        )
    commit_material_mutations(catalogue, tuple(mutations))


def merge_page_head_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "page_heads",
) -> None:
    if not rows:
        return
    newest: dict[str, dict[str, object]] = {}
    for row in rows:
        page_id = str(row["page_id"])
        current = newest.get(page_id)
        candidate = (row["visit_at"], str(row["visit_id"]))
        if current is None or candidate > (
            current["visit_at"],
            str(current["visit_id"]),
        ):
            newest[page_id] = row
    page_ids = set(newest)
    existing = {
        str(page_id): (visit_at, str(visit_id))
        for page_id, visit_id, visit_at
        in catalogue.trusted_remote_rows(
            "SELECT page_id::VARCHAR, visit_id::VARCHAR, visit_at "
            f"FROM material.{table_name} "
            f"WHERE page_id IN ({sql_string_list(page_ids)})"
        )
    }
    replacements = [
        row
        for page_id, row in newest.items()
        if page_id not in existing
        or (row["visit_at"], str(row["visit_id"])) > existing[page_id]
    ]
    _replace_rows(
        catalogue,
        relation=PAGE_HEADS,
        table_name=table_name,
        rows=replacements,
        match_columns=("page_id",),
    )


def rebuild_page_heads(
    catalogue: Catalogue,
    page_ids: set[str],
    *,
    observations_table: str = "page_observations",
    heads_table: str = "page_heads",
) -> None:
    if not page_ids:
        return
    identifiers = sql_string_list(page_ids)
    rows = [
        {
            "page_id": str(page_id),
            "visit_id": str(visit_id),
            "visit_at": visit_at,
        }
        for page_id, visit_id, visit_at
        in catalogue.trusted_remote_rows(
            "SELECT page_id, visit_id, visit_at "
            f"FROM material.{observations_table} "
            f"WHERE page_id IN ({identifiers}) "
            "QUALIFY row_number() OVER ("
            "PARTITION BY page_id "
            "ORDER BY visit_at DESC, visit_id DESC"
            ") = 1"
        )
    ]
    present = frozenset(str(row["page_id"]) for row in rows)
    mutations = []
    missing = frozenset(page_ids) - present
    if missing:
        mutations.append(
            _delete_keys_mutation(
                file_id="00-delete-page-heads",
                relation=PAGE_HEADS,
                table_name=heads_table,
                column_name="page_id",
                values=missing,
            )
        )
    if rows:
        mutations.append(
            _replace_mutation(
                file_id="10-replace-page-heads",
                relation=PAGE_HEADS,
                table_name=heads_table,
                rows=rows,
                match_columns=("page_id",),
            )
        )
    commit_material_mutations(catalogue, tuple(mutations))


def _replace_rows(
    catalogue: Catalogue,
    *,
    relation: RelationName,
    table_name: str,
    rows: list[dict[str, object]],
    match_columns: tuple[str, ...],
) -> None:
    if not rows:
        return
    commit_material_mutations(
        catalogue,
        (
            _replace_mutation(
                file_id=f"replace-{relation.table}",
                relation=relation,
                table_name=table_name,
                rows=rows,
                match_columns=match_columns,
            ),
        ),
    )


def _replace_mutation(
    *,
    file_id: str,
    relation: RelationName,
    table_name: str,
    rows: list[dict[str, object]],
    match_columns: tuple[str, ...],
) -> MaterialMutation:
    return MaterialMutation(
        file_id=file_id,
        relation=relation,
        table_name=table_name,
        table=pa.Table.from_pylist(rows),
        mutation_mode="replace",
        match_columns=match_columns,
    )


def _delete_keys_mutation(
    *,
    file_id: str,
    relation: RelationName,
    table_name: str,
    column_name: str,
    values: frozenset[str],
) -> MaterialMutation:
    return MaterialMutation(
        file_id=file_id,
        relation=relation,
        table_name=table_name,
        table=pa.table(
            {
                column_name: pa.array(
                    sorted(values),
                    type=pa.string(),
                )
            }
        ),
        mutation_mode="delete",
        match_columns=(column_name,),
    )
