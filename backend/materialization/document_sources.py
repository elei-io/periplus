"""Pinned source selection shared by document materialization modes."""

from __future__ import annotations

from datetime import datetime

from control.urls import normalize_url
from repository.catalogue import Catalogue

from materialization.sql import (
    sql_string_list,
    value_batches,
)


def html_documents(
    catalogue: Catalogue,
    *,
    snapshot: int,
    content_hashes: set[str] | None = None,
) -> list[tuple[str, str, str]]:
    hash_filter = ""
    if content_hashes is not None:
        if not content_hashes:
            return []
        hash_filter = (
            "\n          AND content_sha256 IN "
            f"({sql_string_list(content_hashes)})"
        )
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT content_sha256, object_key, storage_encoding
        FROM ingest.documents AT (VERSION => {snapshot})
        WHERE lower(detected_media_type) = 'text/html'
        {hash_filter}
        ORDER BY
          content_sha256,
          CASE storage_encoding WHEN 'zstd' THEN 0 ELSE 1 END,
          object_key
        """
    )
    documents: dict[str, tuple[str, str, str]] = {}
    for content_hash, object_key, storage_encoding in rows:
        value = str(content_hash)
        documents.setdefault(
            value,
            (value, str(object_key), str(storage_encoding)),
        )
    return list(documents.values())


def changed_document_ids(
    catalogue: Catalogue,
    *,
    start_snapshot: int,
    end_snapshot: int,
) -> list[str]:
    alias = "'" + catalogue.config.alias.replace("'", "''") + "'"
    return [
        str(document_id)
        for (document_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT DISTINCT document_id
            FROM ducklake_table_changes(
              {alias}, 'ingest', 'documents',
              {start_snapshot}, {end_snapshot}
            )
            WHERE document_id IS NOT NULL
            ORDER BY document_id
            """
        )
    ]


def document_observation_rows(
    catalogue: Catalogue,
    document_ids: list[str],
    *,
    snapshot: int,
) -> list[tuple[str, str | None, str | None, datetime | None, int]]:
    if not document_ids:
        return []
    documents: dict[str, tuple[str, str, int]] = {}
    for batch in value_batches(document_ids):
        for document_id, visit_id, content_hash, content_bytes in (
            catalogue.trusted_remote_rows(
                f"""
                SELECT document_id::VARCHAR, visit_id::VARCHAR,
                       content_sha256, content_bytes
                FROM ingest.documents AT (VERSION => {snapshot})
                WHERE document_id IN ({sql_string_list(set(batch))})
                  AND lower(detected_media_type) = 'text/html'
                """
            )
        ):
            documents[str(document_id)] = (
                str(visit_id),
                str(content_hash),
                int(content_bytes),
            )
    visit_ids = sorted({row[0] for row in documents.values()})
    visits: dict[str, tuple[str, datetime]] = {}
    for batch in value_batches(visit_ids):
        for visit_id, raw_url, observed_at in catalogue.trusted_remote_rows(
            f"""
            SELECT visit_id::VARCHAR,
                   coalesce(effective_url, requested_url),
                   observed_at
            FROM ingest.visits AT (VERSION => {snapshot})
            WHERE visit_id IN ({sql_string_list(set(batch))})
              AND observed_at IS NOT NULL
            """
        ):
            visits[str(visit_id)] = (
                normalize_url(str(raw_url)),
                observed_at,
            )
    rows: list[
        tuple[str, str | None, str | None, datetime | None, int]
    ] = []
    for document_id in document_ids:
        document = documents.get(document_id)
        if document is None:
            rows.append((document_id, None, None, None, 0))
            continue
        visit = visits.get(document[0])
        rows.append(
            (
                document_id,
                document[1],
                visit[0] if visit is not None else None,
                visit[1] if visit is not None else None,
                document[2],
            )
        )
    return rows
