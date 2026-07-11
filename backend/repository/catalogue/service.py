"""Typed durable operations for the DuckLake repository."""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from uuid import UUID

from repository.catalogue.client import Catalogue
from repository.catalogue.exceptions import CatalogueConflictError, CatalogueValidationError
from repository.catalogue.records import (
    CatalogueWriteResult,
    CrawlRecord,
    DocumentRecord,
    ElementRecord,
    LinkRecord,
)
from dom import ElementRow, GroupedLinkPayload, anchors_from_elements, links_from_elements


@dataclass(frozen=True, slots=True)
class CatalogueBatchEntry:
    document: DocumentRecord | None
    crawl: CrawlRecord
    elements_path: Path | None = None
    replace_projection: bool = False


@dataclass(frozen=True, slots=True)
class CompactionResult:
    table_name: str
    eligible_files: int
    eligible_bytes: int
    files_processed: int
    files_created: int


class CatalogueService:
    """The only application boundary for Atlas catalogue reads and writes."""

    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def record_crawl_batch(
        self,
        entries: Sequence[CatalogueBatchEntry],
    ) -> list[CatalogueWriteResult]:
        """Commit a microbatch with one append per logical DuckLake table."""

        with self._write_fence():
            return self._record_crawl_batch_unfenced(entries)

    def compact_small_files(
        self,
        *,
        minimum_files: int,
        maximum_input_file_bytes: int,
        target_file_bytes: int,
        maximum_compacted_files: int,
    ) -> list[CompactionResult]:
        """Merge small files in bounded table-level operations when thresholds are met."""

        for name, value in (
            ("minimum_files", minimum_files),
            ("maximum_input_file_bytes", maximum_input_file_bytes),
            ("target_file_bytes", target_file_bytes),
            ("maximum_compacted_files", maximum_compacted_files),
        ):
            if value <= 0:
                raise CatalogueValidationError(f"{name} must be greater than zero")
        if target_file_bytes <= maximum_input_file_bytes:
            raise CatalogueValidationError(
                "target_file_bytes must be greater than maximum_input_file_bytes"
            )

        with self._write_fence():
            candidates = self._small_file_candidates(
                maximum_input_file_bytes=maximum_input_file_bytes,
            )
            results: list[CompactionResult] = []
            for table_name, eligible_files, eligible_bytes in candidates:
                if eligible_files < minimum_files:
                    continue
                self.catalogue.connection.execute(
                    "CALL ducklake_set_option("
                    "?, 'target_file_size', ?, schema => ?, table_name => ?)",
                    [
                        self.catalogue.config.alias,
                        f"{target_file_bytes}B",
                        self.catalogue.config.schema,
                        table_name,
                    ],
                )
                rows = self.catalogue.connection.execute(
                    "CALL ducklake_merge_adjacent_files("
                    "?, ?, schema => ?, max_compacted_files => ?, max_file_size => ?)",
                    [
                        self.catalogue.config.alias,
                        table_name,
                        self.catalogue.config.schema,
                        maximum_compacted_files,
                        maximum_input_file_bytes,
                    ],
                ).fetchall()
                results.append(
                    CompactionResult(
                        table_name=table_name,
                        eligible_files=eligible_files,
                        eligible_bytes=eligible_bytes,
                        files_processed=sum(int(row[2]) for row in rows),
                        files_created=sum(int(row[3]) for row in rows),
                    )
                )
            return results

    def _small_file_candidates(
        self,
        *,
        maximum_input_file_bytes: int,
    ) -> list[tuple[str, int, int]]:
        metadata = _quote_identifier(
            f"__ducklake_metadata_{self.catalogue.config.alias}"
        )
        rows = self.catalogue.connection.execute(
            f"""
            SELECT
                table_name,
                max(partition_files) AS eligible_files,
                sum(partition_bytes) AS eligible_bytes
            FROM (
                SELECT
                    table_info.table_name,
                    data_file.partition_id,
                    count(*) AS partition_files,
                    sum(data_file.file_size_bytes) AS partition_bytes
                FROM {metadata}.ducklake_data_file AS data_file
                JOIN {metadata}.ducklake_table AS table_info
                  ON table_info.table_id = data_file.table_id
                JOIN {metadata}.ducklake_schema AS schema_info
                  ON schema_info.schema_id = table_info.schema_id
                WHERE data_file.end_snapshot IS NULL
                  AND table_info.end_snapshot IS NULL
                  AND schema_info.end_snapshot IS NULL
                  AND schema_info.schema_name = ?
                  AND data_file.file_size_bytes < ?
                GROUP BY table_info.table_name, data_file.partition_id
            ) AS partitions
            GROUP BY table_name
            ORDER BY table_name
            """,
            [self.catalogue.config.schema, maximum_input_file_bytes],
        ).fetchall()
        return [
            (str(table_name), int(eligible_files), int(eligible_bytes))
            for table_name, eligible_files, eligible_bytes in rows
        ]

    def _record_crawl_batch_unfenced(
        self,
        entries: Sequence[CatalogueBatchEntry],
    ) -> list[CatalogueWriteResult]:
        if not entries:
            return []
        new_documents: dict[str, DocumentRecord] = {}
        replacement_documents: dict[str, DocumentRecord] = {}
        element_paths: dict[str, Path] = {}
        new_crawls: dict[UUID, CrawlRecord] = {}
        document_created: list[bool] = []
        crawl_created: list[bool] = []

        with self.catalogue.lake.transaction():
            batch_documents = [
                entry.document for entry in entries if entry.document is not None
            ]
            for document in batch_documents:
                _validate_document_identity(document)
            documents_by_id = self.get_documents(
                [document.document_id for document in batch_documents]
            )
            crawls_by_id = self._lookup_batch_crawls(
                [entry.crawl for entry in entries]
            )

            for entry in entries:
                document = entry.document
                crawl = entry.crawl
                if document is None:
                    if crawl.document_id is not None:
                        raise CatalogueValidationError(
                            "a crawl with document_id must include its document"
                        )
                    if not crawl.errors_json:
                        raise CatalogueValidationError(
                            "a documentless crawl must record an acquisition error"
                        )
                    if entry.elements_path is not None or entry.replace_projection:
                        raise CatalogueValidationError(
                            "a documentless crawl cannot contain a DOM projection"
                        )
                    document_created.append(False)
                else:
                    if crawl.document_id != document.document_id:
                        raise CatalogueValidationError(
                            "crawl.document_id must match document.document_id"
                        )
                    existing_document = documents_by_id.get(document.document_id)
                    pending_document = new_documents.get(document.document_id)
                    if existing_document is None and pending_document is None:
                        if document.element_count > 0 and entry.elements_path is None:
                            raise CatalogueValidationError(
                                "a new document with elements requires a staged DOM projection"
                            )
                        new_documents[document.document_id] = document
                        document_created.append(True)
                    elif existing_document is not None:
                        _validate_canonical_document(existing_document, document)
                        document_created.append(False)
                    elif pending_document is not None:
                        _validate_canonical_document(pending_document, document)
                        document_created.append(False)

                    should_write_projection = (
                        entry.elements_path is not None
                        and (existing_document is None or entry.replace_projection)
                    )
                    if should_write_projection:
                        previous_path = element_paths.get(document.document_id)
                        if previous_path is None:
                            element_paths[document.document_id] = entry.elements_path
                    if entry.replace_projection:
                        if existing_document is None:
                            raise CatalogueValidationError(
                                "projection replacement requires an existing document"
                            )
                        if document.element_count > 0 and entry.elements_path is None:
                            raise CatalogueValidationError(
                                "projection replacement requires a staged DOM projection"
                            )
                        pending_replacement = replacement_documents.get(
                            document.document_id
                        )
                        if pending_replacement is not None:
                            _validate_projection_recipe(pending_replacement, document)
                        else:
                            replacement_documents[document.document_id] = document

                existing_crawl = crawls_by_id.get(crawl.crawl_id)
                if existing_crawl is not None:
                    if existing_crawl != crawl:
                        raise CatalogueConflictError(
                            f"crawl_id {str(crawl.crawl_id)!r} already has different provenance"
                        )
                    crawl_created.append(False)
                elif (pending := new_crawls.get(crawl.crawl_id)) is not None:
                    if pending != crawl:
                        raise CatalogueConflictError(
                            f"microbatch contains conflicting crawl {str(crawl.crawl_id)!r}"
                        )
                    crawl_created.append(False)
                else:
                    new_crawls[crawl.crawl_id] = crawl
                    crawl_created.append(True)

            if replacement_documents:
                identifiers = list(replacement_documents)
                placeholders = ", ".join("?" for _ in identifiers)
                self.catalogue.connection.execute(
                    f"DELETE FROM {self._table('elements')} "
                    f"WHERE document_id IN ({placeholders})",
                    identifiers,
                )

            if new_documents:
                self._append(
                    "documents",
                    [value.model_dump(mode="python") for value in new_documents.values()],
                )
            if element_paths:
                relation = self.catalogue.connection.read_parquet(
                    [str(path) for path in element_paths.values()],
                    union_by_name=True,
                )
                self.catalogue.lake.table.append(
                    "elements",
                    relation,
                    schema_name=self.catalogue.config.schema,
                )
            if replacement_documents:
                self._update_projection_recipes(list(replacement_documents.values()))
            if new_crawls:
                self._append(
                    "crawls",
                    [_crawl_values(value) for value in new_crawls.values()],
                )

        snapshot = self.catalogue.latest_snapshot()
        if snapshot is None:
            raise CatalogueValidationError("DuckLake did not publish a repository snapshot")
        return [
            CatalogueWriteResult(
                document_id=(
                    entry.document.document_id if entry.document is not None else None
                ),
                crawl_id=entry.crawl.crawl_id,
                document_created=created_document,
                crawl_created=created_crawl,
                repository_snapshot=snapshot,
            )
            for entry, created_document, created_crawl in zip(
                entries,
                document_created,
                crawl_created,
                strict=True,
            )
        ]

    def get_document(self, document_id: str) -> DocumentRecord | None:
        rows = self.catalogue.lake.sql_dicts(
            f"SELECT * FROM {self._table('documents')} WHERE document_id = $document_id",
            document_id=document_id,
        )
        row = _one_or_none(rows, identity=f"document_id {document_id!r}")
        return _document_from_row(row) if row is not None else None

    def get_documents(
        self,
        document_ids: Sequence[str],
    ) -> dict[str, DocumentRecord]:
        """Resolve a bounded set of document identities in one analytical lookup."""

        rows = self._lookup_rows_by_identity(
            "documents",
            "document_id",
            document_ids,
        )
        result: dict[str, DocumentRecord] = {}
        for row in rows:
            document = _document_from_row(row)
            if document.document_id in result:
                raise CatalogueConflictError(
                    f"catalogue contains duplicate rows for document_id "
                    f"{document.document_id!r}"
                )
            result[document.document_id] = document
        return result

    def get_crawl(self, crawl_id: UUID) -> CrawlRecord | None:
        rows = self.catalogue.lake.sql_dicts(
            f"SELECT * FROM {self._table('crawls')} WHERE crawl_id = $crawl_id",
            crawl_id=crawl_id,
        )
        row = _one_or_none(rows, identity=f"crawl_id {str(crawl_id)!r}")
        return _crawl_from_row(row) if row is not None else None

    def find_cached_crawls(
        self,
        *,
        normalized_url: str,
        input_hash: str,
        captured_after: datetime | None = None,
        captured_before: datetime | None = None,
        limit: int = 20,
    ) -> list[CrawlRecord]:
        """Return recent successful acquisition candidates in reuse order."""

        if limit <= 0:
            raise CatalogueValidationError("limit must be greater than zero")
        conditions = [
            "normalized_url = $normalized_url",
            "input_hash = $input_hash",
            "document_id IS NOT NULL",
            "(status_code IS NULL OR status_code BETWEEN 200 AND 399)",
            "json_array_length(errors_json) = 0",
        ]
        params: dict[str, object] = {
            "normalized_url": normalized_url,
            "input_hash": input_hash,
            "limit": limit,
        }
        if captured_after is not None:
            conditions.append("captured_at >= $captured_after")
            params["captured_after"] = captured_after
        if captured_before is not None:
            conditions.append("captured_at < $captured_before")
            params["captured_before"] = captured_before
        rows = self.catalogue.lake.sql_dicts(
            f"SELECT * FROM {self._table('crawls')} WHERE "
            + " AND ".join(conditions)
            + " ORDER BY captured_at DESC LIMIT $limit",
            **params,
        )
        return [_crawl_from_row(row) for row in rows]

    def get_elements(
        self,
        document_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ElementRecord]:
        _validate_page(limit=limit, offset=offset)
        sql = (
            "SELECT element_index, parent_index, subtree_end_index, depth, tag, "
            "namespace_uri, attributes, text_direct, text_tail "
            f"FROM {self._table('elements')} WHERE document_id = $document_id "
            "ORDER BY element_index"
        )
        params: dict[str, object] = {"document_id": document_id}
        if limit is not None:
            sql += " LIMIT $limit OFFSET $offset"
            params.update(limit=limit, offset=offset)
        elif offset:
            sql += " LIMIT ALL OFFSET $offset"
            params["offset"] = offset
        rows = self.catalogue.lake.sql_dicts(sql, **params)
        return [ElementRecord.model_validate(row) for row in rows]

    def iter_elements(
        self,
        document_id: str,
        *,
        batch_size: int = 8_192,
    ) -> Iterator[ElementRow]:
        """Stream page-local rows without materializing a document-sized Pydantic list."""

        if batch_size <= 0:
            raise CatalogueValidationError("batch_size must be greater than zero")
        reader = self.catalogue.connection.execute(
            "SELECT element_index, parent_index, subtree_end_index, depth, tag, "
            "namespace_uri, attributes, text_direct, text_tail "
            f"FROM {self._table('elements')} WHERE document_id = ? "
            "ORDER BY element_index",
            [document_id],
        ).to_arrow_reader(batch_size=batch_size)
        for batch in reader:
            for row in batch.to_pylist():
                attributes = row["attributes"]
                yield ElementRow(
                    element_index=int(row["element_index"]),
                    parent_index=(
                        int(row["parent_index"])
                        if row["parent_index"] is not None
                        else None
                    ),
                    subtree_end_index=int(row["subtree_end_index"]),
                    depth=int(row["depth"]),
                    tag=str(row["tag"]),
                    namespace_uri=row["namespace_uri"],
                    attributes=(
                        dict(attributes)
                        if attributes is not None
                        else {}
                    ),
                    text_direct=str(row["text_direct"]),
                    text_tail=str(row["text_tail"]),
                )

    def get_links(self, document_id: str) -> list[LinkRecord]:
        """Return raw anchors with complete descendant text in document order."""

        return [
            LinkRecord(
                document_id=document_id,
                element_index=anchor.element_index,
                href=anchor.href,
                text=anchor.text,
                title=anchor.title,
            )
            for anchor in anchors_from_elements(self.iter_elements(document_id))
        ]

    def get_projected_links(
        self,
        document_id: str,
        *,
        page_url: str,
    ) -> GroupedLinkPayload:
        """Return the canonical link payload shared by fresh and cached pages."""

        return links_from_elements(self.iter_elements(document_id), page_url=page_url)

    def count_elements(self, document_id: str) -> int:
        value = self.catalogue.lake.sql_scalar(
            f"SELECT count(*) FROM {self._table('elements')} "
            "WHERE document_id = $document_id",
            document_id=document_id,
        )
        return int(value or 0)

    def _lookup_batch_crawls(
        self,
        crawls: Sequence[CrawlRecord],
    ) -> dict[UUID, CrawlRecord]:
        rows = self._lookup_rows_by_identity(
            "crawls",
            "crawl_id",
            [crawl.crawl_id for crawl in crawls],
        )
        result: dict[UUID, CrawlRecord] = {}
        for row in rows:
            crawl = _crawl_from_row(row)
            if crawl.crawl_id in result:
                raise CatalogueConflictError(
                    f"catalogue contains duplicate rows for crawl_id "
                    f"{str(crawl.crawl_id)!r}"
                )
            result[crawl.crawl_id] = crawl
        return result

    def _lookup_rows_by_identity(
        self,
        table_name: str,
        column_name: str,
        values: Sequence[object],
    ) -> list[dict[str, Any]]:
        unique_values = list(dict.fromkeys(values))
        if not unique_values:
            return []
        placeholders = ", ".join("(?)" for _ in unique_values)
        return self._fetch_rows(
            f"WITH requested(value) AS (VALUES {placeholders}) "
            f"SELECT stored.* FROM {self._table(table_name)} AS stored "
            f"JOIN requested ON requested.value = stored.{column_name}",
            unique_values,
        )

    def _update_projection_recipes(
        self,
        documents: Sequence[DocumentRecord],
    ) -> None:
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?)" for _ in documents)
        parameters = [
            value
            for document in documents
            for value in (
                document.document_id,
                document.dom_schema_version,
                document.parser_name,
                document.parser_version,
                document.parser_options_hash,
                document.element_count,
            )
        ]
        self.catalogue.connection.execute(
            f"UPDATE {self._table('documents')} AS target SET "
            "dom_schema_version = staged.dom_schema_version, "
            "parser_name = staged.parser_name, "
            "parser_version = staged.parser_version, "
            "parser_options_hash = staged.parser_options_hash, "
            "element_count = staged.element_count "
            f"FROM (VALUES {placeholders}) AS staged("
            "document_id, dom_schema_version, parser_name, parser_version, "
            "parser_options_hash, element_count) "
            "WHERE target.document_id = staged.document_id",
            parameters,
        )

    def _fetch_rows(
        self,
        sql: str,
        parameters: Sequence[object],
    ) -> list[dict[str, Any]]:
        cursor = self.catalogue.connection.execute(sql, list(parameters))
        names = [description[0] for description in cursor.description]
        return [
            dict(zip(names, row, strict=True))
            for row in cursor.fetchall()
        ]

    def _insert_document(self, document: DocumentRecord) -> None:
        self._append("documents", [document.model_dump(mode="python")])

    def _insert_crawl(self, crawl: CrawlRecord) -> None:
        self._append("crawls", [_crawl_values(crawl)])

    def _insert_elements(
        self, document_id: str, elements: Sequence[ElementRecord]
    ) -> None:
        if not elements:
            return
        rows = [
            {"document_id": document_id, **element.model_dump(mode="python")}
            for element in elements
        ]
        self._append("elements", rows)

    def _append(self, table_name: str, rows: list[dict[str, object]]) -> None:
        self.catalogue.lake.table.append(
            table_name,
            rows,
            schema_name=self.catalogue.config.schema,
        )

    def _table(self, table_name: str) -> str:
        return ".".join(
            _quote_identifier(part)
            for part in (self.catalogue.config.alias, self.catalogue.config.schema, table_name)
        )

    @contextmanager
    def _write_fence(self) -> Iterator[None]:
        """Serialize repository commits through ducklake-client's catalogue fence."""

        with self.catalogue.lake.fence(
            "repository-commit",
            namespace="atlas",
            timeout=120,
        ):
            yield

    @staticmethod
    def _validate_batch(
        document: DocumentRecord,
        crawl: CrawlRecord,
        elements: Sequence[ElementRecord],
    ) -> None:
        _validate_document_identity(document)
        if crawl.document_id != document.document_id:
            raise CatalogueValidationError("crawl.document_id must match document.document_id")
        object_key = PurePosixPath(document.html_object_key)
        if not object_key.parts or object_key.is_absolute() or ".." in object_key.parts:
            raise CatalogueValidationError("document.html_object_key must be a relative safe key")
        CatalogueService._validate_elements(document, elements)

    @staticmethod
    def _validate_elements(
        document: DocumentRecord,
        elements: Sequence[ElementRecord],
    ) -> None:
        if document.element_count != len(elements):
            raise CatalogueValidationError("document.element_count must match the element batch")
        for expected_index, element in enumerate(elements):
            if element.element_index != expected_index:
                raise CatalogueValidationError(
                    "elements must have contiguous zero-based document-order indexes"
                )
            if element.parent_index is not None and element.parent_index >= element.element_index:
                raise CatalogueValidationError(
                    "an element parent must precede the element in document order"
                )
            if element.subtree_end_index < element.element_index:
                raise CatalogueValidationError(
                    "an element subtree must end at or after the element"
                )

def _document_from_row(row: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord.model_validate(row)


def _crawl_values(crawl: CrawlRecord) -> dict[str, object]:
    values = crawl.model_dump(mode="python")
    for name in ("input_json", "warnings_json", "errors_json"):
        values[name] = json.dumps(values[name], separators=(",", ":"), sort_keys=True)
    return values


def _validate_canonical_document(
    existing: DocumentRecord,
    proposed: DocumentRecord,
) -> None:
    immutable = (
        "document_id",
        "html_sha256",
        "html_object_key",
        "html_content_type",
        "html_encoding",
        "html_size_bytes",
        "compression",
    )
    if any(getattr(existing, name) != getattr(proposed, name) for name in immutable):
        raise CatalogueConflictError(
            f"document_id {existing.document_id!r} already has different canonical metadata"
        )


def _validate_document_identity(document: DocumentRecord) -> None:
    expected = f"sha256:{document.html_sha256}"
    if document.document_id != expected:
        raise CatalogueValidationError(
            "document_id must be the sha256-prefixed canonical HTML hash"
        )


def _validate_projection_recipe(
    existing: DocumentRecord,
    proposed: DocumentRecord,
) -> None:
    recipe = (
        "dom_schema_version",
        "parser_name",
        "parser_version",
        "parser_options_hash",
        "element_count",
    )
    if any(getattr(existing, name) != getattr(proposed, name) for name in recipe):
        raise CatalogueConflictError(
            f"microbatch contains conflicting projections for document_id "
            f"{existing.document_id!r}"
        )


def _crawl_from_row(row: dict[str, Any]) -> CrawlRecord:
    values = dict(row)
    for name in ("input_json", "warnings_json", "errors_json"):
        if isinstance(values.get(name), str):
            values[name] = json.loads(values[name])
    return CrawlRecord.model_validate(values)


def _validate_page(*, limit: int | None, offset: int) -> None:
    if limit is not None and limit <= 0:
        raise CatalogueValidationError("limit must be greater than zero")
    if offset < 0:
        raise CatalogueValidationError("offset must be zero or greater")


def _one_or_none(
    rows: list[dict[str, Any]], *, identity: str
) -> dict[str, Any] | None:
    if len(rows) > 1:
        raise CatalogueConflictError(f"catalogue contains duplicate rows for {identity}")
    return rows[0] if rows else None


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
