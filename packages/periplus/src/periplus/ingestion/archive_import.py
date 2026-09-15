"""External observations join the ordinary immutable-object ingestion boundary."""

import asyncio

from periplus.ingestion.archive import journal
from periplus.ingestion.common_crawl import CommonCrawlClient, CommonCrawlRecord, DecodedCapture, decode_record
from periplus.ingestion.objects.html import HtmlIdentity, RawHtmlRepository
from periplus.ingestion.objects.store import ObjectStore
from periplus.ingestion.queue import IngestionQueueClient, visit_ingestion_job
from periplus.platform.catalogue.records import DocumentRecord, VisitEvidence, VisitRecord, document_id_for
from periplus.retention.identities import EvidenceRetired, retired


def archive_record(store: ObjectStore, item: CommonCrawlRecord, data: bytes) -> VisitEvidence:
    return archive_capture(store, decode_record(item, data))


def archive_capture(store: ObjectStore, capture: DecodedCapture) -> VisitEvidence:
    identity, observed = capture.source.capture_id, capture.captured_at
    if retired("observation", str(identity)):
        raise EvidenceRetired("archived observation was evicted; automatic reimport is forbidden")
    stored = RawHtmlRepository(store).put(capture.html, source_url=capture.url,
        visit_id=identity, observed_at=observed, content_type="text/html")
    RawHtmlRepository(store).verify(stored.object_key,
        expected=HtmlIdentity(sha256=stored.sha256, size_bytes=stored.size_bytes))
    # The archive provides a timestamped observation, not a measured execution
    # interval. Equal timestamps encode that point; no native attempts are added.
    evidence = VisitEvidence(visit=VisitRecord(visit_id=identity,
        requested_url=capture.url, effective_url=capture.url, admitted_at=observed,
        started_at=observed, observed_at=observed, finished_at=observed,
        outcome="succeeded", status_code=200, document_id=document_id_for(identity),
        archive_source=capture.source), attempts=(), document=DocumentRecord(
        document_id=document_id_for(identity), visit_id=identity, observed_at=observed,
        representation="response_body", declared_media_type=capture.content_type,
        detected_media_type="text/html", charset="utf-8", content_sha256=stored.sha256,
        content_bytes=stored.size_bytes, object_key=stored.object_key,
        storage_encoding="zstd", stored_bytes=stored.compressed_size_bytes))
    journal(store, visit_ingestion_job(evidence))
    return evidence


async def import_record(client: CommonCrawlClient, store: ObjectStore,
                        queue: IngestionQueueClient, item: CommonCrawlRecord) -> VisitEvidence:
    data = await asyncio.to_thread(client.fetch, item)
    evidence = await asyncio.to_thread(archive_record, store, item, data)
    # Losing this publish does not lose the capture: archive replay republishes
    # the same frozen identity through the normal ingestor/materializer workers.
    await queue.reconcile(visit_ingestion_job(evidence))
    return evidence
