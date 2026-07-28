"""Visit-owned page and observation materialization."""

from __future__ import annotations

from urllib.parse import urlsplit

import tldextract
from control.urls import normalize_url
from repository.catalogue import Catalogue, page_id_for

from materialization.sql import (
    row_batches,
    sql_nullable_integer,
    sql_nullable_string,
    sql_string,
    sql_string_list,
    sql_timestamp,
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
               coalesce(effective_url, requested_url), observed_at
        FROM ingest.visits AT (VERSION => {end_snapshot})
        WHERE visit_id IN ({sql_string_list(set(visit_ids))})
          AND observed_at IS NOT NULL
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
    observed_at: object,
) -> dict[str, object]:
    normalized_url = normalize_url(str(raw_url))
    return {
        "page_id": str(page_id_for(normalized_url)),
        "visit_id": str(visit_id),
        "document_id": (
            str(document_id) if document_id is not None else None
        ),
        "observed_at": observed_at,
    }


def merge_page_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "pages",
) -> None:
    if not rows:
        return
    with catalogue.remote_transaction():
        for batch in row_batches(rows):
            values = ", ".join(
                "("
                + ", ".join(
                    (
                        f"UUID {sql_string(str(row['page_id']))}",
                        sql_string(str(row["normalized_url"])),
                        sql_string(str(row["scheme"])),
                        sql_string(str(row["hostname"])),
                        sql_nullable_integer(row["port"]),
                        sql_string(str(row["path"])),
                        sql_nullable_string(row["query"]),
                        sql_nullable_string(row["registrable_domain"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.{table_name} AS target
                USING (VALUES {values}) AS delta(
                  page_id, normalized_url, scheme, hostname, port, path, query,
                  registrable_domain
                )
                  ON target.page_id = delta.page_id
                 AND target.normalized_url = delta.normalized_url
                WHEN NOT MATCHED THEN INSERT
                """
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
    with catalogue.remote_transaction():
        if replaced_visit_ids:
            catalogue.trusted_remote_execute(
                f"DELETE FROM material.{table_name} "
                "WHERE visit_id IN "
                f"({sql_string_list(set(replaced_visit_ids))})"
            )
        for batch in row_batches(rows):
            values = ", ".join(
                "("
                + ", ".join(
                    (
                        f"UUID {sql_string(str(row['page_id']))}",
                        f"UUID {sql_string(str(row['visit_id']))}",
                        (
                            "NULL"
                            if row["document_id"] is None
                            else f"UUID {sql_string(str(row['document_id']))}"
                        ),
                        sql_timestamp(row["observed_at"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.{table_name} AS target
                USING (VALUES {values}) AS delta(
                  page_id, visit_id, document_id, observed_at
                )
                  ON target.visit_id = delta.visit_id
                WHEN MATCHED THEN UPDATE SET
                  page_id = delta.page_id,
                  document_id = delta.document_id,
                  observed_at = delta.observed_at
                WHEN NOT MATCHED THEN INSERT
                """
            )
