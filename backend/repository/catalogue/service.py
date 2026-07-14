"""Typed durable operations for the DuckLake repository."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

from repository.catalogue.client import Catalogue
from repository.catalogue.exceptions import CatalogueConflictError, CatalogueValidationError
from repository.catalogue.records import (
    ArtifactRecord,
    CatalogueWriteResult,
    CrawlRecord,
    DocumentRecord,
    ElementRecord,
)
from dom import ElementRow, GroupedLinkPayload, links_from_elements


@dataclass(frozen=True, slots=True)
class CatalogueBatchEntry:
    document: DocumentRecord | None
    crawl: CrawlRecord
    artifact: ArtifactRecord | None = None
    elements_path: Path | None = None
    replace_projection: bool = False


@dataclass(frozen=True, slots=True)
class CompactionResult:
    schema_name: str
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

        operation_key = hashlib.sha256(
            "\0".join(sorted(str(entry.crawl.crawl_id) for entry in entries)).encode()
        ).hexdigest()
        content_ids = sorted(
            {
                identity
                for entry in entries
                for identity in (entry.crawl.document_id, entry.crawl.artifact_id)
                if identity is not None
            }
        )
        with ExitStack() as fences:
            for identity in content_ids:
                identity_key = hashlib.sha256(identity.encode()).hexdigest()
                fences.enter_context(self._write_fence(f"content-{identity_key}"))
            fences.enter_context(self._write_fence(f"ingest-{operation_key}"))
            return self._record_crawl_batch_unfenced(entries)

    def compact_small_files(
        self,
        *,
        minimum_files: int,
        maximum_input_file_bytes: int,
        target_file_bytes: int,
        maximum_compacted_files: int,
        maximum_operation_bytes: int = 256 * 1024 * 1024,
        cleanup_older_than_seconds: int = 7 * 24 * 60 * 60,
    ) -> list[CompactionResult]:
        """Merge small files in bounded table-level operations when thresholds are met."""

        for name, value in (
            ("minimum_files", minimum_files),
            ("maximum_input_file_bytes", maximum_input_file_bytes),
            ("target_file_bytes", target_file_bytes),
            ("maximum_compacted_files", maximum_compacted_files),
            ("maximum_operation_bytes", maximum_operation_bytes),
            ("cleanup_older_than_seconds", cleanup_older_than_seconds),
        ):
            if value <= 0:
                raise CatalogueValidationError(f"{name} must be greater than zero")
        if target_file_bytes <= maximum_input_file_bytes:
            raise CatalogueValidationError(
                "target_file_bytes must be greater than maximum_input_file_bytes"
            )
        if target_file_bytes > maximum_operation_bytes:
            raise CatalogueValidationError(
                "target_file_bytes must not exceed maximum_operation_bytes"
            )

        with self._write_fence("maintenance-global"):
            # DuckLake inlines tiny writes into its metadata catalogue. Flush those
            # accumulated rows before file compaction so Postgres does not become
            # an unbounded data store at low ingestion rates.
            self.catalogue.connection.execute(
                "CALL ducklake_flush_inlined_data(?)",
                [self.catalogue.config.alias],
            ).fetchall()
            candidates = self._small_file_candidates(
                maximum_input_file_bytes=maximum_input_file_bytes,
            )
            results: list[CompactionResult] = []
            bounded_compactions = min(
                maximum_compacted_files,
                max(1, maximum_operation_bytes // target_file_bytes),
            )
            for schema_name, table_name, eligible_files, eligible_bytes in candidates:
                if eligible_files < minimum_files:
                    continue
                self.catalogue.connection.execute(
                    "CALL ducklake_set_option("
                    "?, 'target_file_size', ?, schema => ?, table_name => ?)",
                    [
                        self.catalogue.config.alias,
                        f"{target_file_bytes}B",
                        schema_name,
                        table_name,
                    ],
                )
                rows = self.catalogue.connection.execute(
                    "CALL ducklake_merge_adjacent_files("
                    "?, ?, schema => ?, max_compacted_files => ?, max_file_size => ?)",
                    [
                        self.catalogue.config.alias,
                        table_name,
                        schema_name,
                        bounded_compactions,
                        maximum_input_file_bytes,
                    ],
                ).fetchall()
                results.append(
                    CompactionResult(
                        schema_name=schema_name,
                        table_name=table_name,
                        eligible_files=eligible_files,
                        eligible_bytes=eligible_bytes,
                        files_processed=sum(int(row[2]) for row in rows),
                        files_created=sum(int(row[3]) for row in rows),
                    )
                )
            # Compaction schedules superseded files for deletion. Keep a generous
            # grace window for long reads, then reclaim only scheduled files;
            # snapshot expiry and orphan deletion remain explicit retention actions.
            self.catalogue.connection.execute(
                "CALL ducklake_cleanup_old_files(?, older_than => "
                "now() - CAST(? AS BIGINT) * INTERVAL '1 second')",
                [self.catalogue.config.alias, cleanup_older_than_seconds],
            ).fetchall()
            return results

    def _small_file_candidates(
        self,
        *,
        maximum_input_file_bytes: int,
    ) -> list[tuple[str, str, int, int]]:
        metadata = _quote_identifier(
            f"__ducklake_metadata_{self.catalogue.config.alias}"
        )
        rows = self.catalogue.connection.execute(
            f"""
            SELECT
                schema_name,
                table_name,
                max(partition_files) AS eligible_files,
                sum(partition_bytes) AS eligible_bytes
            FROM (
                SELECT
                    schema_info.schema_name,
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
                  AND schema_info.schema_name IN (?, '_atlas_materializations')
                  AND data_file.file_size_bytes < ?
                GROUP BY schema_info.schema_name, table_info.table_name, data_file.partition_id
            ) AS partitions
            GROUP BY schema_name, table_name
            ORDER BY schema_name, table_name
            """,
            [self.catalogue.config.schema, maximum_input_file_bytes],
        ).fetchall()
        return [
            (
                str(schema_name),
                str(table_name),
                int(eligible_files),
                int(eligible_bytes),
            )
            for schema_name, table_name, eligible_files, eligible_bytes in rows
        ]

    def _record_crawl_batch_unfenced(
        self,
        entries: Sequence[CatalogueBatchEntry],
    ) -> list[CatalogueWriteResult]:
        if not entries:
            return []
        new_documents: dict[str, DocumentRecord] = {}
        new_artifacts: dict[str, ArtifactRecord] = {}
        replacement_documents: dict[str, DocumentRecord] = {}
        element_paths: dict[str, Path] = {}
        new_crawls: dict[UUID, CrawlRecord] = {}
        document_created: list[bool] = []
        artifact_created: list[bool] = []
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
            batch_artifacts = [
                entry.artifact for entry in entries if entry.artifact is not None
            ]
            for artifact in batch_artifacts:
                _validate_artifact_identity(artifact)
            artifacts_by_id = self.get_artifacts(
                [artifact.artifact_id for artifact in batch_artifacts]
            )
            crawls_by_id = self._lookup_batch_crawls(
                [entry.crawl for entry in entries]
            )

            for entry in entries:
                artifact = entry.artifact
                document = entry.document
                crawl = entry.crawl
                if artifact is not None and document is not None:
                    raise CatalogueValidationError(
                        "a crawl cannot include both an artifact and a document"
                    )
                if artifact is None:
                    if crawl.artifact_id is not None:
                        raise CatalogueValidationError(
                            "a crawl with artifact_id must include its artifact"
                        )
                    artifact_created.append(False)
                else:
                    if crawl.artifact_id != artifact.artifact_id:
                        raise CatalogueValidationError(
                            "crawl.artifact_id must match artifact.artifact_id"
                        )
                    existing_artifact = artifacts_by_id.get(artifact.artifact_id)
                    pending_artifact = new_artifacts.get(artifact.artifact_id)
                    if existing_artifact is None and pending_artifact is None:
                        new_artifacts[artifact.artifact_id] = artifact
                        artifact_created.append(True)
                    elif existing_artifact is not None:
                        _validate_canonical_artifact(existing_artifact, artifact)
                        artifact_created.append(False)
                    else:
                        assert pending_artifact is not None
                        _validate_canonical_artifact(pending_artifact, artifact)
                        artifact_created.append(False)

                if document is None:
                    if crawl.document_id is not None:
                        raise CatalogueValidationError(
                            "a crawl with document_id must include its document"
                        )
                    if (
                        artifact is None
                        and (crawl.outcome != "failed" or crawl.failure_code is None)
                    ):
                        raise CatalogueValidationError(
                            "a contentless crawl must record an acquisition error"
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

            if new_artifacts:
                self._append(
                    "artifacts",
                    [_artifact_values(value) for value in new_artifacts.values()],
                )
            if new_documents:
                self._append(
                    "documents",
                    [_document_values(value) for value in new_documents.values()],
                )
            if element_paths:
                # Keep the whole commit inside the attached DuckLake database.
                # DuckDBPyRelation.query() creates a helper view in ``memory``;
                # after the document append that becomes an illegal second
                # database write in the same transaction.
                self.catalogue.connection.execute(
                    f"INSERT INTO {self._table('elements')} BY NAME "
                    "SELECT * FROM read_parquet(?, union_by_name = true)",
                    [[str(path) for path in element_paths.values()]],
                )
            if replacement_documents:
                self._update_projection_recipes(list(replacement_documents.values()))
            if new_crawls:
                self._append(
                    "crawls",
                    [_crawl_values(value) for value in new_crawls.values()],
                )

            made_changes = bool(
                new_documents
                or new_artifacts
                or element_paths
                or replacement_documents
                or new_crawls
            )
            if made_changes:
                self.catalogue.set_commit_message(
                    author="Atlas repository",
                    message=f"Ingested {len(entries)} crawl operation(s)",
                    extra={
                        "crawl_ids": [str(entry.crawl.crawl_id) for entry in entries],
                        "new_documents": len(new_documents),
                        "new_artifacts": len(new_artifacts),
                        "new_crawls": len(new_crawls),
                        "element_rows": sum(
                            entry.document.element_count
                            for entry in entries
                            if entry.document is not None
                            and entry.document.document_id in element_paths
                        ),
                    },
                )

        snapshot = (
            self.catalogue.last_committed_snapshot()
            if made_changes
            else self.catalogue.latest_snapshot()
        )
        if snapshot is None:
            raise CatalogueValidationError("DuckLake did not publish a repository snapshot")
        return [
            CatalogueWriteResult(
                artifact_id=(
                    entry.artifact.artifact_id if entry.artifact is not None else None
                ),
                document_id=(
                    entry.document.document_id if entry.document is not None else None
                ),
                crawl_id=entry.crawl.crawl_id,
                document_created=created_document,
                artifact_created=created_artifact,
                crawl_created=created_crawl,
                repository_snapshot=snapshot,
            )
            for entry, created_document, created_artifact, created_crawl in zip(
                entries,
                document_created,
                artifact_created,
                crawl_created,
                strict=True,
            )
        ]

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        rows = self.catalogue.lake.sql_dicts(
            f"SELECT * FROM {self._table('artifacts')} WHERE artifact_id = $artifact_id",
            artifact_id=artifact_id,
        )
        row = _one_or_none(rows, identity=f"artifact_id {artifact_id!r}")
        return ArtifactRecord.model_validate(row) if row is not None else None

    def get_artifacts(
        self,
        artifact_ids: Sequence[str],
    ) -> dict[str, ArtifactRecord]:
        rows = self._lookup_rows_by_identity(
            "artifacts",
            "artifact_id",
            artifact_ids,
        )
        result: dict[str, ArtifactRecord] = {}
        for row in rows:
            artifact = ArtifactRecord.model_validate(row)
            if artifact.artifact_id in result:
                raise CatalogueConflictError(
                    f"catalogue contains duplicate rows for artifact_id "
                    f"{artifact.artifact_id!r}"
                )
            result[artifact.artifact_id] = artifact
        return result

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
        config_hash: str,
        captured_after: datetime | None = None,
        captured_before: datetime | None = None,
        limit: int = 20,
    ) -> list[CrawlRecord]:
        """Return recent successful acquisition candidates in reuse order."""

        if limit <= 0:
            raise CatalogueValidationError("limit must be greater than zero")
        conditions = [
            "normalized_url = $normalized_url",
            "config_hash = $config_hash",
            "purpose = 'use'",
            "outcome = 'success'",
            "document_id IS NOT NULL",
            "(status_code IS NULL OR status_code BETWEEN 200 AND 399)",
        ]
        params: dict[str, object] = {
            "normalized_url": normalized_url,
            "config_hash": config_hash,
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

    def get_projected_links(
        self,
        document_id: str,
        *,
        page_url: str,
    ) -> GroupedLinkPayload:
        """Return the canonical link payload shared by fresh and cached pages."""

        return links_from_elements(self.iter_elements(document_id), page_url=page_url)

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
        placeholders = ", ".join(
            "(" + ", ".join("?" for _ in range(18)) + ")" for _ in documents
        )
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
                document.quality_schema_version,
                document.html_character_count,
                document.visible_text_chars,
                document.script_count,
                document.app_marker_count,
                document.lazy_marker_count,
                document.interaction_marker_count,
                document.button_count,
                document.form_count,
                document.input_count,
                document.anchor_count,
                json.dumps(document.quality_flags_json, separators=(",", ":")),
            )
        ]
        self.catalogue.connection.execute(
            f"UPDATE {self._table('documents')} AS target SET "
            "dom_schema_version = staged.dom_schema_version, "
            "parser_name = staged.parser_name, "
            "parser_version = staged.parser_version, "
            "parser_options_hash = staged.parser_options_hash, "
            "element_count = staged.element_count, "
            "quality_schema_version = staged.quality_schema_version, "
            "html_character_count = staged.html_character_count, "
            "visible_text_chars = staged.visible_text_chars, "
            "script_count = staged.script_count, "
            "app_marker_count = staged.app_marker_count, "
            "lazy_marker_count = staged.lazy_marker_count, "
            "interaction_marker_count = staged.interaction_marker_count, "
            "button_count = staged.button_count, "
            "form_count = staged.form_count, "
            "input_count = staged.input_count, "
            "anchor_count = staged.anchor_count, "
            "quality_flags_json = staged.quality_flags_json "
            f"FROM (VALUES {placeholders}) AS staged("
            "document_id, dom_schema_version, parser_name, parser_version, "
            "parser_options_hash, element_count, quality_schema_version, "
            "html_character_count, visible_text_chars, script_count, "
            "app_marker_count, lazy_marker_count, interaction_marker_count, "
            "button_count, form_count, input_count, anchor_count, quality_flags_json) "
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
    def _write_fence(self, operation: str) -> Iterator[None]:
        """Fence redelivery of one deterministic operation without serializing the catalogue."""

        with self.catalogue.lake.fence(
            operation,
            namespace="atlas",
            timeout=120,
        ):
            yield

def _document_from_row(row: dict[str, Any]) -> DocumentRecord:
    values = dict(row)
    if isinstance(values.get("quality_flags_json"), str):
        values["quality_flags_json"] = json.loads(values["quality_flags_json"])
    return DocumentRecord.model_validate(values)


def _document_values(document: DocumentRecord) -> dict[str, object]:
    values = document.model_dump(mode="python")
    values["quality_flags_json"] = json.dumps(
        values["quality_flags_json"], separators=(",", ":")
    )
    return values


def _artifact_values(artifact: ArtifactRecord) -> dict[str, object]:
    return artifact.model_dump(mode="python")


def _crawl_values(crawl: CrawlRecord) -> dict[str, object]:
    values = crawl.model_dump(mode="python")
    values["config_json"] = json.dumps(
        values["config_json"], separators=(",", ":"), sort_keys=True
    )
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


def _validate_artifact_identity(artifact: ArtifactRecord) -> None:
    expected = f"sha256:{artifact.sha256}"
    if artifact.artifact_id != expected:
        raise CatalogueValidationError(
            "artifact_id must be the sha256-prefixed canonical byte hash"
        )


def _validate_canonical_artifact(
    existing: ArtifactRecord,
    proposed: ArtifactRecord,
) -> None:
    immutable = ("artifact_id", "sha256", "object_key", "size_bytes")
    if any(getattr(existing, name) != getattr(proposed, name) for name in immutable):
        raise CatalogueConflictError(
            f"artifact_id {existing.artifact_id!r} already has different canonical metadata"
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
        "quality_schema_version",
        "html_character_count",
        "visible_text_chars",
        "script_count",
        "app_marker_count",
        "lazy_marker_count",
        "interaction_marker_count",
        "button_count",
        "form_count",
        "input_count",
        "anchor_count",
        "quality_flags_json",
    )
    if any(getattr(existing, name) != getattr(proposed, name) for name in recipe):
        raise CatalogueConflictError(
            f"microbatch contains conflicting projections for document_id "
            f"{existing.document_id!r}"
        )


def _crawl_from_row(row: dict[str, Any]) -> CrawlRecord:
    values = dict(row)
    if isinstance(values.get("config_json"), str):
        values["config_json"] = json.loads(values["config_json"])
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
