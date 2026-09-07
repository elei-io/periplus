"""Evidence-only ingestion across immutable objects and DuckLake."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from periplus.platform.catalogue import (
    Catalogue,
    CatalogueService,
    DocumentRecord,
    IngestionWriteResult,
    VisitEvidence,
    catalogue_from_env,
)
from periplus.ingestion.queue import IngestionJob
from periplus.ingestion.objects.document import (
    ExactDocumentIdentity,
    ExactDocumentRepository,
)
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.html import HtmlIdentity, RawHtmlRepository


@dataclass(frozen=True, slots=True)
class RepositoryLimits:
    max_document_bytes: int = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreparedIngestion:
    job: IngestionJob


class RepositoryIngestor:
    """Verify immutable bytes and commit observed evidence without interpretation."""

    def __init__(
        self,
        *,
        html_repository: RawHtmlRepository,
        document_repository: ExactDocumentRepository,
        catalogue: Catalogue,
        limits: RepositoryLimits | None = None,
    ) -> None:
        self.html_repository = html_repository
        self.document_repository = document_repository
        self.catalogue = catalogue
        self.catalogue_service = CatalogueService(catalogue)
        self.limits = limits or RepositoryLimits()

    def validate(self) -> None:
        self.catalogue.validate_schema()

    def probe(self) -> None:
        self.catalogue.latest_snapshot()

    def prepare(self, job: IngestionJob) -> PreparedIngestion:
        if job.kind == "visit":
            assert job.visit is not None
            if job.visit.document is not None:
                self._verify_document(job.visit.document)
        return PreparedIngestion(job=job)

    def commit_prepared_batch(
        self,
        prepared: list[PreparedIngestion],
    ) -> list[IngestionWriteResult]:
        results: dict[str, IngestionWriteResult] = {}
        visits = [
            value.job.visit
            for value in prepared
            if value.job.kind == "visit" and value.job.visit is not None
        ]
        if visits:
            for job, result in zip(
                (
                    value.job
                    for value in prepared
                    if value.job.kind == "visit"
                ),
                self.catalogue_service.record_visits(visits),
                strict=True,
            ):
                results[job.request_id] = result
        lineage_jobs = [value.job for value in prepared if value.job.kind == "lineage"]
        if lineage_jobs:
            for job, result in zip(lineage_jobs, self.catalogue_service.record_lineage(
                [job.lineage for job in lineage_jobs]
            ), strict=True):
                results[job.request_id] = result
        return [results[value.job.request_id] for value in prepared]

    def reconcile_commit(
        self,
        job: IngestionJob,
    ) -> IngestionWriteResult | None:
        if job.kind == "lineage":
            assert job.lineage is not None
            durable = self.catalogue_service.get_lineage(job.lineage.kind, job.identity)
            expected = job.lineage
        else:
            assert job.visit is not None
            durable = self.catalogue_service.get_visit_evidence(
                [job.visit.visit.visit_id]
            ).get(job.visit.visit.visit_id)
            expected = job.visit
        if durable is None:
            return None
        if durable != expected:
            from periplus.platform.catalogue import CatalogueConflictError

            raise CatalogueConflictError(
                f"ingestion {job.request_id} has different durable evidence"
            )
        snapshot = self.catalogue.latest_snapshot()
        if snapshot is None:
            raise RuntimeError("DuckLake has no repository snapshot")
        return IngestionWriteResult(
            kind=job.kind,
            identity=job.identity,
            created=False,
            repository_snapshot=snapshot,
        )

    def _verify_document(self, document: DocumentRecord) -> None:
        if document.content_bytes > self.limits.max_document_bytes:
            raise ValueError(
                "document exceeds the configured ingestion byte budget"
            )
        if document.storage_encoding == "zstd":
            identity = self.html_repository.verify(
                document.object_key,
                expected=HtmlIdentity(
                    sha256=document.content_sha256,
                    size_bytes=document.content_bytes,
                ),
            )
        elif document.storage_encoding == "identity":
            identity = self.document_repository.verify(
                document.object_key,
                expected=ExactDocumentIdentity(
                    sha256=document.content_sha256,
                    size_bytes=document.content_bytes,
                ),
            )
        else:
            raise ValueError(
                f"unsupported document storage encoding {document.storage_encoding!r}"
            )
        if identity.size_bytes != document.content_bytes:
            raise ValueError("document content size does not match immutable bytes")
        stored_bytes = self.html_repository.store.size(document.object_key)
        if stored_bytes != document.stored_bytes:
            raise ValueError("document stored size does not match immutable object")

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
    store = object_store_from_env()
    return RepositoryIngestor(
        html_repository=RawHtmlRepository(store),
        document_repository=ExactDocumentRepository(store),
        catalogue=catalogue_from_env(),
    )
