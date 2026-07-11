"""Repository ingestion across raw storage, DOM projection, and DuckLake."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

from repository.ducklake import (
    Catalogue,
    CatalogueBatchEntry,
    CatalogueConflictError,
    CatalogueService,
    CatalogueValidationError,
    CatalogueWriteResult,
    CrawlRecord,
    DocumentRecord,
    catalogue_config_from_env,
)
from dom import (
    DOM_SCHEMA_VERSION,
    PARSER_NAME,
    PARSER_OPTIONS_HASH,
    PARSER_VERSION,
    GroupedLinkPayload,
    write_dom_parquet,
)
from repository.config import object_store_from_env, staging_root_from_env
from repository.html import HtmlIdentity, RawHtmlRepository, StoredHtml, html_object_key


class ProjectionRebuildRequired(RuntimeError):
    """Signal that the dedicated ingestor must rebuild a cached projection."""

    def __init__(self, crawl: CrawlRecord) -> None:
        super().__init__(f"document {crawl.document_id} needs DOM reprojection")
        self.crawl = crawl


@dataclass(frozen=True, slots=True)
class RepositoryLimits:
    max_html_bytes: int = 64 * 1024 * 1024
    max_document_elements: int = 1_000_000
    max_document_staged_bytes: int = 256 * 1024 * 1024

    @classmethod
    def from_env(cls) -> RepositoryLimits:
        return cls(
            max_html_bytes=_positive_env_int(
                "ATLAS_REPOSITORY_MAX_HTML_BYTES", 64 * 1024 * 1024
            ),
            max_document_elements=_positive_env_int(
                "ATLAS_REPOSITORY_MAX_DOCUMENT_ELEMENTS", 1_000_000
            ),
            max_document_staged_bytes=_positive_env_int(
                "ATLAS_REPOSITORY_MAX_DOCUMENT_STAGED_BYTES", 256 * 1024 * 1024
            ),
        )


class RepositoryIngestor:
    """Commit one captured page to Atlas's durable repository."""

    def __init__(
        self,
        *,
        html_repository: RawHtmlRepository,
        catalogue: Catalogue,
        staging_root: Path | None = None,
        limits: RepositoryLimits | None = None,
    ) -> None:
        self.html_repository = html_repository
        self.catalogue = catalogue
        self.catalogue_service = CatalogueService(catalogue)
        self.staging_root = staging_root or staging_root_from_env()
        self.limits = limits or RepositoryLimits.from_env()

    def validate(self) -> None:
        """Attach to an initialized repository and validate its schema contract."""

        self.catalogue.validate_schema()

    def prepare_from_raw(
        self,
        *,
        crawl: CrawlRecord,
        known_documents: Mapping[str, DocumentRecord] | None = None,
    ) -> PreparedIngestion:
        """Prepare a queued ingestion using only its durable raw object reference."""

        if crawl.document_id is None:
            return PreparedIngestion(
                document=None,
                crawl=crawl,
            )

        prefix = "sha256:"
        if not crawl.document_id.startswith(prefix):
            raise ValueError("crawl.document_id must be a SHA-256 content identity")
        sha256 = crawl.document_id.removeprefix(prefix)
        object_key = html_object_key(sha256)
        if not self.html_repository.store.exists(object_key):
            raise FileNotFoundError(f"raw HTML object is missing: {object_key}")
        existing = (
            self.catalogue_service.get_document(crawl.document_id)
            if known_documents is None
            else known_documents.get(crawl.document_id)
        )
        if existing is not None and self._projection_is_current(existing):
            self.html_repository.verify(
                object_key,
                expected=HtmlIdentity(
                    sha256=existing.html_sha256,
                    size_bytes=existing.html_size_bytes,
                ),
            )
            return PreparedIngestion(
                document=existing,
                crawl=crawl,
            )

        captured_html = self.html_repository.read(object_key)
        identity = self.html_repository.identify(captured_html)
        self._validate_html_size(identity)
        if identity.sha256 != sha256:
            raise ValueError("raw HTML does not match crawl.document_id")
        compressed_size = self.html_repository.store.size(object_key)
        parquet_path = self.staging_root / f"{crawl.crawl_id}-{uuid4().hex}.dom.parquet"
        projection = write_dom_parquet(
            captured_html,
            document_id=crawl.document_id,
            path=parquet_path,
            max_rows=self.limits.max_document_elements,
            max_bytes=self.limits.max_document_staged_bytes,
        )
        if existing is None:
            document = DocumentRecord(
                document_id=crawl.document_id,
                html_sha256=sha256,
                html_object_key=object_key,
                html_content_type="text/html",
                html_encoding="utf-8",
                html_size_bytes=identity.size_bytes,
                html_compressed_size_bytes=compressed_size,
                compression="zstd",
                dom_schema_version=DOM_SCHEMA_VERSION,
                parser_name=PARSER_NAME,
                parser_version=PARSER_VERSION,
                parser_options_hash=PARSER_OPTIONS_HASH,
                element_count=projection.element_count,
                created_at=crawl.captured_at,
            )
        else:
            document = existing.model_copy(
                update={
                    "html_compressed_size_bytes": compressed_size,
                    "dom_schema_version": DOM_SCHEMA_VERSION,
                    "parser_name": PARSER_NAME,
                    "parser_version": PARSER_VERSION,
                    "parser_options_hash": PARSER_OPTIONS_HASH,
                    "element_count": projection.element_count,
                }
            )
        return PreparedIngestion(
            document=document,
            crawl=crawl,
            elements_path=projection.path,
            element_count=projection.element_count,
            staged_bytes=projection.size_bytes,
            replace_projection=existing is not None,
        )

    def commit_prepared_batch(
        self,
        prepared: list[PreparedIngestion],
        *,
        cleanup_on_error: bool = True,
    ) -> list[CatalogueWriteResult]:
        """Commit one microbatch, optionally retaining failed staging for isolation."""

        committed = False
        try:
            results = self.catalogue_service.record_crawl_batch(
                [
                    CatalogueBatchEntry(
                        document=value.document,
                        crawl=value.crawl,
                        elements_path=value.elements_path,
                        replace_projection=value.replace_projection,
                    )
                    for value in prepared
                ]
            )
            committed = True
            return results
        finally:
            if committed or cleanup_on_error:
                self.discard_prepared(prepared)

    def reconcile_crawl_commit(
        self,
        *,
        crawl: CrawlRecord,
    ) -> CatalogueWriteResult | None:
        """Return a success result only when the complete crawl job is durable."""

        existing_crawl = self.catalogue_service.get_crawl(crawl.crawl_id)
        if existing_crawl is None:
            return None
        if existing_crawl != crawl:
            raise CatalogueConflictError(
                f"crawl_id {str(crawl.crawl_id)!r} has different durable provenance"
            )

        if crawl.document_id is not None:
            document = self.catalogue_service.get_document(crawl.document_id)
            if document is None or not self._projection_is_current(document):
                return None

        snapshot = self.catalogue.latest_snapshot()
        if snapshot is None:
            raise CatalogueValidationError("DuckLake did not publish a repository snapshot")
        return CatalogueWriteResult(
            document_id=crawl.document_id,
            crawl_id=crawl.crawl_id,
            document_created=False,
            crawl_created=False,
            repository_snapshot=snapshot,
        )

    @staticmethod
    def discard_prepared(prepared: list[PreparedIngestion]) -> None:
        """Remove page-local staging after commit or terminal rejection."""

        for value in prepared:
            if value.elements_path is not None:
                value.elements_path.unlink(missing_ok=True)

    def store_raw(
        self,
        captured_html: str,
        *,
        identity: HtmlIdentity | None = None,
    ) -> StoredHtml:
        identity = identity or self.html_repository.identify(captured_html)
        self._validate_html_size(identity)
        return self.html_repository.put(captured_html, identity=identity)

    def _validate_html_size(self, identity: HtmlIdentity) -> None:
        if identity.size_bytes > self.limits.max_html_bytes:
            raise ValueError(
                f"HTML exceeded its {self.limits.max_html_bytes} byte repository budget"
            )

    def cleanup_staging(self, *, older_than_seconds: float) -> int:
        """Remove abandoned local projection files after a safety grace period."""

        if older_than_seconds <= 0:
            raise ValueError("older_than_seconds must be greater than zero")
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        deleted = 0
        for path in self.staging_root.glob("*.parquet"):
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                if modified_at >= cutoff:
                    continue
                path.unlink()
                deleted += 1
            except FileNotFoundError:
                continue
        return deleted

    def resolve_cached_page(
        self,
        *,
        normalized_url: str,
        input_hash: str,
        cache_block_rules: dict[str, Any] | None = None,
        captured_after: datetime | None = None,
        captured_before: datetime | None = None,
        include_html: bool = True,
        include_links: bool = True,
        repair_projection: bool = True,
    ) -> RepositoryCacheHit | None:
        """Resolve reusable structural data, repairing it from raw HTML when needed."""

        for crawl in self.catalogue_service.find_cached_crawls(
            normalized_url=normalized_url,
            input_hash=input_hash,
            captured_after=captured_after,
            captured_before=captured_before,
        ):
            if _blocked_by_cache_rules(crawl, cache_block_rules):
                continue
            hit = self._resolve_crawl_record(
                crawl,
                include_html=include_html,
                include_links=include_links,
                repair_projection=repair_projection,
                require_complete=False,
            )
            if hit is not None:
                return hit
        return None

    def resolve_crawl(
        self,
        crawl_id: UUID,
        *,
        include_html: bool = True,
        include_links: bool = True,
        repair_projection: bool = True,
    ) -> RepositoryCacheHit | None:
        """Resolve one committed logical acquisition by its retry-stable identity."""

        crawl = self.catalogue_service.get_crawl(crawl_id)
        if crawl is None:
            return None
        return self._resolve_crawl_record(
            crawl,
            include_html=include_html,
            include_links=include_links,
            repair_projection=repair_projection,
            require_complete=True,
        )

    def _resolve_crawl_record(
        self,
        crawl: CrawlRecord,
        *,
        include_html: bool,
        include_links: bool,
        repair_projection: bool,
        require_complete: bool,
    ) -> RepositoryCacheHit | None:
        if crawl.document_id is None:
            repository_snapshot = self.catalogue.latest_snapshot()
            if repository_snapshot is None:
                raise RuntimeError("DuckLake repository has no snapshot")
            return RepositoryCacheHit(
                crawl=crawl,
                document=None,
                html=None,
                links=None,
                projection_rebuilt=False,
                repository_snapshot=repository_snapshot,
            )
        document = self.catalogue_service.get_document(crawl.document_id)
        if document is None:
            if require_complete:
                raise RuntimeError(
                    f"crawl {crawl.crawl_id} references missing document {crawl.document_id}"
                )
            return None
        if not self.html_repository.store.exists(document.html_object_key):
            if require_complete:
                raise RuntimeError(
                    f"crawl {crawl.crawl_id} references missing raw HTML "
                    f"{document.html_object_key}"
                )
            return None

        projection_rebuilt = not self._projection_is_current(document)
        if projection_rebuilt and not repair_projection:
            raise ProjectionRebuildRequired(crawl)
        html = (
            self.html_repository.read(document.html_object_key)
            if include_html or projection_rebuilt
            else None
        )
        if html is None:
            self.html_repository.verify(
                document.html_object_key,
                expected=HtmlIdentity(
                    sha256=document.html_sha256,
                    size_bytes=document.html_size_bytes,
                ),
            )
        repository_snapshot = self.catalogue.latest_snapshot()
        if projection_rebuilt:
            assert html is not None
            parquet_path = self.staging_root / (
                f"rebuild-{document.document_id[7:]}-{uuid4().hex}.parquet"
            )
            projection = write_dom_parquet(
                html,
                document_id=document.document_id,
                path=parquet_path,
                max_rows=self.limits.max_document_elements,
                max_bytes=self.limits.max_document_staged_bytes,
            )
            document = document.model_copy(
                update={
                    "dom_schema_version": DOM_SCHEMA_VERSION,
                    "parser_name": PARSER_NAME,
                    "parser_version": PARSER_VERSION,
                    "parser_options_hash": PARSER_OPTIONS_HASH,
                    "element_count": projection.element_count,
                }
            )
            repository_snapshot = self.commit_prepared_batch(
                [
                    PreparedIngestion(
                        document=document,
                        crawl=crawl,
                        elements_path=projection.path,
                        element_count=projection.element_count,
                        staged_bytes=projection.size_bytes,
                        replace_projection=True,
                    )
                ]
            )[0].repository_snapshot
        if repository_snapshot is None:
            raise RuntimeError("DuckLake repository has no snapshot")
        return RepositoryCacheHit(
            crawl=crawl,
            document=document,
            html=html,
            links=(
                self.catalogue_service.get_projected_links(
                    document.document_id,
                    page_url=crawl.final_url or crawl.normalized_url,
                )
                if include_links
                else None
            ),
            projection_rebuilt=projection_rebuilt,
            repository_snapshot=repository_snapshot,
        )

    def _projection_is_current(self, document: DocumentRecord) -> bool:
        """Trust the recipe written atomically with the document's element rows.

        Repository ingestion inserts or replaces the document metadata and its DOM rows in
        one DuckLake transaction. Counting a document's elements on every read turns that
        commit invariant into a corpus scan and does not provide useful concurrent safety.
        Explicit repository validation may still compare ``element_count`` with physical rows.
        """

        return (
            document.dom_schema_version == DOM_SCHEMA_VERSION
            and document.parser_name == PARSER_NAME
            and document.parser_version == PARSER_VERSION
            and document.parser_options_hash == PARSER_OPTIONS_HASH
        )

    def close(self) -> None:
        self.catalogue.close()

    def __enter__(self) -> RepositoryIngestor:
        self.validate()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def repository_ingestor_from_env() -> RepositoryIngestor:
    return RepositoryIngestor(
        html_repository=RawHtmlRepository(object_store_from_env()),
        catalogue=Catalogue(catalogue_config_from_env()),
        staging_root=staging_root_from_env(),
        limits=RepositoryLimits.from_env(),
    )


@dataclass(frozen=True, slots=True)
class RepositoryCacheHit:
    crawl: CrawlRecord
    document: DocumentRecord | None
    html: str | None
    links: GroupedLinkPayload | None
    projection_rebuilt: bool
    repository_snapshot: int


@dataclass(frozen=True, slots=True)
class PreparedIngestion:
    document: DocumentRecord | None
    crawl: CrawlRecord
    elements_path: Path | None = None
    element_count: int = 0
    staged_bytes: int = 0
    replace_projection: bool = False


def _blocked_by_cache_rules(
    crawl: CrawlRecord,
    cache_block_rules: dict[str, Any] | None,
) -> bool:
    configured = (cache_block_rules or {}).get("quality_warning_codes", [])
    blocking = {value for value in configured if isinstance(value, str)}
    if not blocking:
        return False
    present = {
        str(warning.get("code"))
        for warning in crawl.warnings_json
        if isinstance(warning, dict) and warning.get("code") is not None
    }
    return bool(blocking & present)


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value
