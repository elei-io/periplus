"""Visit-owned page and observation materialization."""

from __future__ import annotations

from contextlib import nullcontext
from functools import lru_cache
from urllib.parse import urlsplit

import tldextract
from atlas.platform.catalogue import Catalogue, page_id_for

from atlas.materialization.sql import (
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
    return {
        "page_id": str(page_id_for(normalized_url)),
        "normalized_url": normalized_url,
        "scheme": parsed.scheme,
        "hostname": parsed.hostname,
        "port": parsed.port,
        "path": parsed.path,
        "query": parsed.query or None,
        "registrable_domain": _registrable_domain(parsed.hostname or ""),
    }


@lru_cache(maxsize=4_096)
def _registrable_domain(hostname: str) -> str | None:
    return _TLD_EXTRACT(hostname).top_domain_under_public_suffix or None


def page_observation_row(
    visit_id: object,
    document_id: object,
    page_id: object,
    visit_at: object,
) -> dict[str, object]:
    return {
        "page_id": str(page_id),
        "visit_id": str(visit_id),
        "document_id": (
            str(document_id) if document_id is not None else None
        ),
        "visit_at": visit_at,
    }


def page_head_row(
    visit_id: object,
    page_id: object,
    visit_at: object,
) -> dict[str, object]:
    return {
        "page_id": str(page_id),
        "visit_id": str(visit_id),
        "visit_at": visit_at,
    }


def merge_page_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "pages",
    transaction: bool = True,
) -> None:
    if not rows:
        return
    with (
        catalogue.remote_transaction()
        if transaction
        else nullcontext()
    ):
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
    transaction: bool = True,
) -> None:
    if not rows and not replaced_visit_ids:
        return
    with (
        catalogue.remote_transaction()
        if transaction
        else nullcontext()
    ):
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
                        sql_timestamp(row["visit_at"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.{table_name} AS target
                USING (VALUES {values}) AS delta(
                  page_id, visit_id, document_id, visit_at
                )
                  ON target.visit_id = delta.visit_id
                WHEN MATCHED THEN UPDATE SET
                  page_id = delta.page_id,
                  document_id = delta.document_id,
                  visit_at = delta.visit_at
                WHEN NOT MATCHED THEN INSERT
                """
            )


def merge_page_head_rows(
    catalogue: Catalogue,
    rows: list[dict[str, object]],
    *,
    table_name: str = "page_heads",
    transaction: bool = True,
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
    with (
        catalogue.remote_transaction()
        if transaction
        else nullcontext()
    ):
        for batch in row_batches(list(newest.values())):
            values = ", ".join(
                "("
                + ", ".join(
                    (
                        f"UUID {sql_string(str(row['page_id']))}",
                        f"UUID {sql_string(str(row['visit_id']))}",
                        sql_timestamp(row["visit_at"]),
                    )
                )
                + ")"
                for row in batch
            )
            catalogue.trusted_remote_execute(
                f"""
                MERGE INTO material.{table_name} AS target
                USING (VALUES {values}) AS delta(
                  page_id, visit_id, visit_at
                )
                  ON target.page_id = delta.page_id
                WHEN MATCHED AND (
                  delta.visit_at > target.visit_at
                  OR (
                    delta.visit_at = target.visit_at
                    AND delta.visit_id > target.visit_id
                  )
                ) THEN UPDATE SET
                  visit_id = delta.visit_id,
                  visit_at = delta.visit_at
                WHEN NOT MATCHED THEN INSERT
                """
            )


def rebuild_page_heads(
    catalogue: Catalogue,
    page_ids: set[str],
    *,
    observations_table: str = "page_observations",
    heads_table: str = "page_heads",
    transaction: bool = True,
) -> None:
    if not page_ids:
        return
    identifiers = sql_string_list(page_ids)
    with (
        catalogue.remote_transaction()
        if transaction
        else nullcontext()
    ):
        catalogue.trusted_remote_execute(
            f"DELETE FROM material.{heads_table} "
            f"WHERE page_id IN ({identifiers})"
        )
        catalogue.trusted_remote_execute(
            f"""
            INSERT INTO material.{heads_table}
        SELECT page_id, visit_id, visit_at
            FROM material.{observations_table}
            WHERE page_id IN ({identifiers})
            QUALIFY row_number() OVER (
              PARTITION BY page_id
              ORDER BY visit_at DESC, visit_id DESC
            ) = 1
            """
        )
