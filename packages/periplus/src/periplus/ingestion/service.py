"""Evidence-only ingestion across immutable objects and ClickHouse."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from periplus.platform.catalogue.records import DocumentRecord
from periplus.platform.catalogue.lineage import FulfillmentRecord, AcquisitionReason
from periplus.platform.clickhouse import connect_clickhouse
from periplus.ingestion.storage import EvidenceStore, EvidenceReceipt, evidence_digest
from periplus.ingestion.queue import IngestionJob
from periplus.ingestion.objects.publication import claim
from periplus.retention.identities import retired, EvidenceRetired
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
        evidence_store: EvidenceStore,
        limits: RepositoryLimits | None = None,
    ) -> None:
        self.html_repository = html_repository
        self.document_repository = document_repository
        self.evidence_store = evidence_store
        self.limits = limits or RepositoryLimits()

    def validate(self) -> None:
        self.evidence_store.validate()

    def probe(self) -> None:
        self.evidence_store.client.execute("SELECT 1")

    def prepare(self, job: IngestionJob) -> PreparedIngestion:
        from periplus.ingestion.archive import journal
        if job.kind == "visit":
            assert job.visit is not None
            if retired("observation", str(job.visit.visit.visit_id)):
                raise EvidenceRetired("observation has been retired")
            if job.visit.document is not None:
                claim(self.html_repository.store, job.visit.document.content_sha256, job.visit.visit.visit_id)
                self._verify_document(job.visit.document)
        journal(self.html_repository.store, job)
        return PreparedIngestion(job=job)

    def commit_prepared_batch(
        self,
        prepared: list[PreparedIngestion],
    ) -> list[EvidenceReceipt]:
        # Each frozen evidence identity is its own atomic insert. If a later
        # member fails, replay reconciles already committed members by digest.
        results = []
        for value in prepared:
            job = value.job
            if job.visit is not None:
                result = self.evidence_store.record_visit(job.visit)
            elif isinstance(job.lineage, (FulfillmentRecord, AcquisitionReason)):
                result = self.evidence_store.record_lineage(job.lineage)
            else:
                raise ValueError("collection control records do not belong in ingestion")
            results.append(result)
        return results

    def reconcile_commit(self, job: IngestionJob) -> EvidenceReceipt | None:
        evidence = job.visit if job.visit is not None else job.lineage
        if job.visit is not None:
            kind = "visit"
        elif isinstance(job.lineage, (FulfillmentRecord, AcquisitionReason)):
            kind = job.lineage.kind
        else:
            raise ValueError("collection control records do not belong in ingestion")
        return self.evidence_store.receipt(kind, job.identity, evidence_digest(evidence))

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
        self.evidence_store.client.close()

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
        evidence_store=EvidenceStore(connect_clickhouse()),
    )
