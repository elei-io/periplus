"""Typed durable operations for the DuckLake repository."""

from __future__ import annotations

import json
from collections.abc import Sequence
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
    CrawlAttemptRecord,
    CrawlRecord,
    CrawlStepRecord,
    DocumentRecord,
    ElementRecord,
    UrlRecord,
)
from repository.catalogue.schema import CRAWL_ATTEMPTS_TABLE, CRAWL_STEPS_TABLE, INTERNAL_SCHEMA
from dom import ElementRow, GroupedLinkPayload, links_from_elements


@dataclass(frozen=True, slots=True)
class CatalogueBatchEntry:
    document: DocumentRecord | None
    crawl: CrawlRecord
    urls: tuple[UrlRecord, ...] = ()
    crawl_attempts: tuple[CrawlAttemptRecord, ...] = ()
    crawl_steps: tuple[CrawlStepRecord, ...] | None = ()
    artifact: ArtifactRecord | None = None
    elements_path: Path | None = None
    replace_projection: bool = False


@dataclass(frozen=True, slots=True)
class ExistingCatalogueIdentities:
    """Immutable catalogue identities confirmed present before commit fencing."""

    urls: frozenset[str] = frozenset()
    documents: frozenset[str] = frozenset()
    artifacts: frozenset[str] = frozenset()


class CatalogueService:
    """The only application boundary for Atlas catalogue reads and writes."""

    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def record_crawl_batch(
        self,
        entries: Sequence[CatalogueBatchEntry],
    ) -> list[CatalogueWriteResult]:
        """Commit a microbatch with one append per logical DuckLake table."""

        if not entries:
            return []
        new_documents: dict[str, DocumentRecord] = {}
        new_artifacts: dict[str, ArtifactRecord] = {}
        new_urls: dict[str, UrlRecord] = {}
        replacement_documents: dict[str, DocumentRecord] = {}
        element_paths: dict[str, Path] = {}
        new_crawls: dict[UUID, CrawlRecord] = {}
        new_crawl_steps: dict[UUID, tuple[CrawlStepRecord, ...]] = {}
        new_crawl_attempts: dict[UUID, tuple[CrawlAttemptRecord, ...]] = {}
        document_created: list[bool] = []
        artifact_created: list[bool] = []
        crawl_created: list[bool] = []

        with self.catalogue.transaction():
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
            batch_urls = [url for entry in entries for url in entry.urls]
            urls_by_id = self.get_urls([url.url_id for url in batch_urls])
            crawl_steps_by_id = self._lookup_batch_crawl_steps(
                [entry.crawl.crawl_id for entry in entries]
            )
            crawl_attempts_by_id = self._lookup_batch_crawl_attempts(
                [entry.crawl.crawl_id for entry in entries]
            )

            for entry in entries:
                artifact = entry.artifact
                document = entry.document
                crawl = entry.crawl
                crawl_attempts = tuple(entry.crawl_attempts)
                if crawl_attempts:
                    _validate_crawl_attempts(crawl, crawl_attempts)
                for url in entry.urls:
                    _validate_url_identity(url)
                    existing_url = urls_by_id.get(url.url_id)
                    pending_url = new_urls.get(url.url_id)
                    if existing_url is not None:
                        _validate_canonical_url(existing_url, url)
                    elif pending_url is not None:
                        _validate_canonical_url(pending_url, url)
                    else:
                        new_urls[url.url_id] = url
                crawl_steps = (
                    None
                    if entry.crawl_steps is None
                    else tuple(entry.crawl_steps)
                )
                if crawl_steps is not None:
                    _validate_crawl_steps(crawl, crawl_steps)
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
                        and crawl.outcome not in {"failed", "skipped"}
                    ):
                        raise CatalogueValidationError(
                            "a contentless crawl must be failed or skipped"
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
                    if (
                        crawl_steps is not None
                        and crawl_steps_by_id.get(crawl.crawl_id, ()) != crawl_steps
                    ):
                        raise CatalogueConflictError(
                            f"crawl_id {str(crawl.crawl_id)!r} already has different completion steps"
                        )
                    if (
                        crawl_attempts
                        and crawl_attempts_by_id.get(crawl.crawl_id, ())
                        != crawl_attempts
                    ):
                        raise CatalogueConflictError(
                            f"crawl_id {str(crawl.crawl_id)!r} already has different attempts"
                        )
                    crawl_created.append(False)
                elif (pending := new_crawls.get(crawl.crawl_id)) is not None:
                    if pending != crawl:
                        raise CatalogueConflictError(
                            f"microbatch contains conflicting crawl {str(crawl.crawl_id)!r}"
                        )
                    if (
                        crawl_steps is not None
                        and new_crawl_steps.get(crawl.crawl_id) != crawl_steps
                    ):
                        raise CatalogueConflictError(
                            f"microbatch contains conflicting completion steps for "
                            f"crawl {str(crawl.crawl_id)!r}"
                        )
                    if new_crawl_attempts.get(crawl.crawl_id) != crawl_attempts:
                        raise CatalogueConflictError(
                            f"microbatch contains conflicting attempts for crawl "
                            f"{str(crawl.crawl_id)!r}"
                        )
                    crawl_created.append(False)
                else:
                    if crawl_steps is None:
                        raise CatalogueValidationError(
                            "projection-only ingestion requires an existing crawl"
                        )
                    if not crawl_attempts:
                        raise CatalogueValidationError(
                            "a new crawl requires typed acquisition attempts"
                        )
                    new_crawls[crawl.crawl_id] = crawl
                    new_crawl_steps[crawl.crawl_id] = crawl_steps
                    new_crawl_attempts[crawl.crawl_id] = crawl_attempts
                    crawl_created.append(True)

            available_url_ids = set(urls_by_id) | set(new_urls)
            for crawl in new_crawls.values():
                referenced = {crawl.requested_url_id}
                if crawl.final_url_id is not None:
                    referenced.add(crawl.final_url_id)
                referenced.update(
                    attempt.requested_url_id
                    for attempt in new_crawl_attempts[crawl.crawl_id]
                )
                referenced.update(
                    attempt.final_url_id
                    for attempt in new_crawl_attempts[crawl.crawl_id]
                    if attempt.final_url_id is not None
                )
                missing = referenced - available_url_ids
                if missing:
                    raise CatalogueValidationError(
                        "crawl and attempt URL identities must be included in the URL dimension"
                    )

            if replacement_documents:
                identifiers = list(replacement_documents)
                values = ", ".join(_sql_literal(value) for value in identifiers)
                self.catalogue.trusted_remote_execute(
                    f"DELETE FROM {self._table('elements')} "
                    f"WHERE document_id IN ({values})"
                )

            if new_urls:
                self._append(
                    "urls",
                    [_url_values(value) for value in new_urls.values()],
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
                self.catalogue.trusted_connection.execute(
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
                attempts = [
                    attempt
                    for crawl_id in new_crawls
                    for attempt in new_crawl_attempts[crawl_id]
                ]
                if attempts:
                    self._append(
                        CRAWL_ATTEMPTS_TABLE,
                        [_crawl_attempt_values(value) for value in attempts],
                    )
                steps = [
                    step
                    for crawl_id in new_crawls
                    for step in new_crawl_steps[crawl_id]
                ]
                if steps:
                    self._append(
                        CRAWL_STEPS_TABLE,
                        [_crawl_step_values(value) for value in steps],
                    )

            made_changes = bool(
                new_documents
                or new_urls
                or new_artifacts
                or element_paths
                or replacement_documents
                or new_crawls
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
        rows = self.catalogue.trusted_sql_dicts(
            f"SELECT * FROM {self._table('artifacts')} WHERE artifact_id = $artifact_id",
            {"artifact_id": artifact_id},
        )
        row = _one_or_none(rows, identity=f"artifact_id {artifact_id!r}")
        return ArtifactRecord.model_validate(row) if row is not None else None

    def preflight_existing_identities(
        self,
        *,
        url_ids: Sequence[str],
        document_ids: Sequence[str],
        artifact_ids: Sequence[str],
    ) -> ExistingCatalogueIdentities:
        """Resolve existing immutable identities in one bounded DuckLake scan."""

        requested = {
            "url": (
                "urls",
                "url_id",
                list(dict.fromkeys(url_ids)),
            ),
            "document": (
                "documents",
                "document_id",
                list(dict.fromkeys(document_ids)),
            ),
            "artifact": (
                "artifacts",
                "artifact_id",
                list(dict.fromkeys(artifact_ids)),
            ),
        }
        branches: list[str] = []
        for kind, (table_name, column_name, values) in requested.items():
            if not values:
                continue
            requested_values = ", ".join(
                f"({_sql_literal(value)})" for value in values
            )
            branches.append(
                f"SELECT '{kind}' AS identity_kind, "
                f"stored.{column_name} AS identity_value "
                f"FROM {self._table(table_name)} AS stored "
                f"JOIN (VALUES {requested_values}) AS requested(value) "
                f"ON requested.value = stored.{column_name}"
            )
        if not branches:
            return ExistingCatalogueIdentities()

        rows = self.catalogue.trusted_remote_rows(" UNION ALL ".join(branches))
        existing: dict[str, set[str]] = {
            "url": set(),
            "document": set(),
            "artifact": set(),
        }
        for kind, value in rows:
            identity_kind = str(kind)
            if identity_kind not in existing:
                raise CatalogueValidationError(
                    f"unexpected preflight identity kind {identity_kind!r}"
                )
            existing[identity_kind].add(str(value))
        return ExistingCatalogueIdentities(
            urls=frozenset(existing["url"]),
            documents=frozenset(existing["document"]),
            artifacts=frozenset(existing["artifact"]),
        )

    def get_urls(self, url_ids: Sequence[str]) -> dict[str, UrlRecord]:
        rows = self._lookup_rows_by_identity("urls", "url_id", url_ids)
        result: dict[str, UrlRecord] = {}
        for row in rows:
            url = UrlRecord.model_validate(row)
            if url.url_id in result:
                raise CatalogueConflictError(
                    f"catalogue contains duplicate rows for url_id {url.url_id!r}"
                )
            result[url.url_id] = url
        return result

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
        rows = self.catalogue.trusted_sql_dicts(
            f"SELECT * FROM {self._table('documents')} WHERE document_id = $document_id",
            {"document_id": document_id},
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
        rows = self.catalogue.trusted_sql_dicts(
            f"SELECT * FROM {self._table('crawls')} WHERE crawl_id = $crawl_id",
            {"crawl_id": crawl_id},
        )
        row = _one_or_none(rows, identity=f"crawl_id {str(crawl_id)!r}")
        return _crawl_from_row(row) if row is not None else None

    def find_cached_crawls(
        self,
        *,
        normalized_url: str,
        policy_config_hash: str,
        captured_after: datetime | None = None,
        captured_before: datetime | None = None,
        limit: int = 20,
    ) -> list[CrawlRecord]:
        """Return recent successful acquisition candidates in reuse order."""

        if limit <= 0:
            raise CatalogueValidationError("limit must be greater than zero")
        conditions = [
            "requested_url_id = $requested_url_id",
            "policy_config_hash = $policy_config_hash",
            "outcome = 'success'",
            "document_id IS NOT NULL",
            "(status_code IS NULL OR status_code BETWEEN 200 AND 399)",
        ]
        params: dict[str, object] = {
            "requested_url_id": UrlRecord.from_normalized_url(normalized_url).url_id,
            "policy_config_hash": policy_config_hash,
            "limit": limit,
        }
        if captured_after is not None:
            conditions.append("captured_at >= $captured_after")
            params["captured_after"] = captured_after
        if captured_before is not None:
            conditions.append("captured_at < $captured_before")
            params["captured_before"] = captured_before
        rows = self.catalogue.trusted_sql_dicts(
            f"SELECT * FROM {self._table('crawls')} WHERE "
            + " AND ".join(conditions)
            + " ORDER BY captured_at DESC LIMIT $limit",
            params,
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
        rows = self.catalogue.trusted_sql_dicts(sql, params)
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
        reader = self.catalogue.trusted_connection.execute(
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
        values = ", ".join(
            "("
            + ", ".join(
                _sql_literal(value)
                for value in (
                    document.document_id,
                    document.dom_schema_version,
                    document.parser_name,
                    document.parser_version,
                    document.parser_options_hash,
                    document.element_count,
                )
            )
            + ")"
            for document in documents
        )
        self.catalogue.trusted_remote_execute(
            f"UPDATE {self._table('documents')} AS target SET "
            "dom_schema_version = staged.dom_schema_version, "
            "parser_name = staged.parser_name, "
            "parser_version = staged.parser_version, "
            "parser_options_hash = staged.parser_options_hash, "
            "element_count = staged.element_count "
            f"FROM (VALUES {values}) AS staged("
            "document_id, dom_schema_version, parser_name, parser_version, "
            "parser_options_hash, element_count) "
            "WHERE target.document_id = staged.document_id"
        )

    def _fetch_rows(
        self,
        sql: str,
        parameters: Sequence[object],
    ) -> list[dict[str, Any]]:
        cursor = self.catalogue.trusted_connection.execute(sql, list(parameters))
        names = [description[0] for description in cursor.description]
        return [
            dict(zip(names, row, strict=True))
            for row in cursor.fetchall()
        ]

    def _append(self, table_name: str, rows: list[dict[str, object]]) -> None:
        self.catalogue.append(
            table_name,
            rows,
            schema_name=self.catalogue.config.schema,
        )

    def _append_internal(
        self, table_name: str, rows: list[dict[str, object]]
    ) -> None:
        self.catalogue.append(
            table_name,
            rows,
            schema_name=INTERNAL_SCHEMA,
        )

    def _table(self, table_name: str) -> str:
        return ".".join(
            _quote_identifier(part)
            for part in (self.catalogue.config.alias, self.catalogue.config.schema, table_name)
        )

    def _internal_table(self, table_name: str) -> str:
        return ".".join(
            _quote_identifier(part)
            for part in (self.catalogue.config.alias, INTERNAL_SCHEMA, table_name)
        )

    def _lookup_batch_crawl_steps(
        self, crawl_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[CrawlStepRecord, ...]]:
        unique = sorted(set(crawl_ids), key=str)
        if not unique:
            return {}
        placeholders = ", ".join("?" for _ in unique)
        rows = self._fetch_rows(
            f"SELECT * FROM {self._table(CRAWL_STEPS_TABLE)} "
            f"WHERE crawl_id IN ({placeholders}) "
            "ORDER BY crawl_id, attempt_number, step_ordinal",
            unique,
        )
        grouped: dict[UUID, list[CrawlStepRecord]] = {}
        for row in rows:
            record = _crawl_step_from_row(row)
            grouped.setdefault(record.crawl_id, []).append(record)
        return {crawl_id: tuple(values) for crawl_id, values in grouped.items()}

    def _lookup_batch_crawl_attempts(
        self, crawl_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[CrawlAttemptRecord, ...]]:
        unique = sorted(set(crawl_ids), key=str)
        if not unique:
            return {}
        placeholders = ", ".join("?" for _ in unique)
        rows = self._fetch_rows(
            f"SELECT * FROM {self._table(CRAWL_ATTEMPTS_TABLE)} "
            f"WHERE crawl_id IN ({placeholders}) "
            "ORDER BY crawl_id, attempt_number",
            unique,
        )
        grouped: dict[UUID, list[CrawlAttemptRecord]] = {}
        for row in rows:
            record = CrawlAttemptRecord.model_validate(row)
            grouped.setdefault(record.crawl_id, []).append(record)
        return {crawl_id: tuple(values) for crawl_id, values in grouped.items()}


def _document_from_row(row: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord.model_validate(row)


def _document_values(document: DocumentRecord) -> dict[str, object]:
    return document.model_dump(mode="python")


def _artifact_values(artifact: ArtifactRecord) -> dict[str, object]:
    return artifact.model_dump(mode="python")


def _url_values(url: UrlRecord) -> dict[str, object]:
    return url.model_dump(mode="python")


def _crawl_values(crawl: CrawlRecord) -> dict[str, object]:
    values = crawl.model_dump(mode="python")
    values["policy_config_json"] = json.dumps(
        values["policy_config_json"], separators=(",", ":"), sort_keys=True
    )
    return values


def _crawl_attempt_values(attempt: CrawlAttemptRecord) -> dict[str, object]:
    return attempt.model_dump(mode="python")


def _crawl_step_values(step: CrawlStepRecord) -> dict[str, object]:
    values = step.model_dump(mode="python")
    values["config_json"] = json.dumps(
        values["config_json"], separators=(",", ":"), sort_keys=True
    )
    return values


def _crawl_step_from_row(row: dict[str, Any]) -> CrawlStepRecord:
    values = dict(row)
    if isinstance(values.get("config_json"), str):
        values["config_json"] = json.loads(values["config_json"])
    return CrawlStepRecord.model_validate(values)


def _validate_crawl_steps(
    crawl: CrawlRecord, steps: tuple[CrawlStepRecord, ...]
) -> None:
    expected = sorted(steps, key=lambda step: (step.attempt_number, step.step_ordinal))
    if list(steps) != expected:
        raise CatalogueValidationError(
            "crawl completion steps must be ordered by attempt and ordinal"
        )
    identities: set[tuple[int, int]] = set()
    for step in steps:
        if step.crawl_id != crawl.crawl_id:
            raise CatalogueValidationError(
                "crawl completion step crawl_id must match its crawl"
            )
        identity = (step.attempt_number, step.step_ordinal)
        if identity in identities:
            raise CatalogueValidationError(
                "crawl completion step attempt and ordinal must be unique"
            )
        identities.add(identity)


def _validate_crawl_attempts(
    crawl: CrawlRecord, attempts: tuple[CrawlAttemptRecord, ...]
) -> None:
    if [value.attempt_number for value in attempts] != list(
        range(1, len(attempts) + 1)
    ):
        raise CatalogueValidationError(
            "crawl attempts must be ordered and contiguous from attempt 1"
        )
    if any(value.crawl_id != crawl.crawl_id for value in attempts):
        raise CatalogueValidationError("crawl attempt crawl_id must match its crawl")


def _validate_url_identity(url: UrlRecord) -> None:
    expected = UrlRecord.from_normalized_url(url.normalized_url)
    if url != expected:
        raise CatalogueValidationError(
            "URL rows must match the SHA-256 identity and canonical decomposition"
        )


def _validate_canonical_url(existing: UrlRecord, proposed: UrlRecord) -> None:
    if existing != proposed:
        raise CatalogueConflictError(
            f"url_id {existing.url_id!r} already has different canonical metadata"
        )


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
    )
    if any(getattr(existing, name) != getattr(proposed, name) for name in recipe):
        raise CatalogueConflictError(
            f"microbatch contains conflicting projections for document_id "
            f"{existing.document_id!r}"
        )


def _crawl_from_row(row: dict[str, Any]) -> CrawlRecord:
    values = dict(row)
    if isinstance(values.get("policy_config_json"), str):
        values["policy_config_json"] = json.loads(values["policy_config_json"])
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


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return "'" + value.isoformat().replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"
