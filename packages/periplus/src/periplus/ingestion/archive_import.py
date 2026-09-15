"""External observations enter the same operationally independent raw archive."""

import asyncio
from periplus.ingestion.archive import Archive
from periplus.ingestion.captures import Capture, Payload
from periplus.ingestion.common_crawl import (
    CommonCrawlClient,
    CommonCrawlRecord,
    DecodedCapture,
    decode_record,
)
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.store import ObjectStore
from periplus.ingestion.queue import ArchivePublisher
from periplus.retention.identities import write_claims


def archive_record(store: ObjectStore, item: CommonCrawlRecord, data: bytes) -> Capture:
    return archive_capture(store, decode_record(item, data))


def archive_capture(store: ObjectStore, decoded: DecodedCapture) -> Capture:
    identity = decoded.source.capture_id
    stored = RawHtmlRepository(store).put(
        decoded.html,
        source_url=decoded.url,
        visit_id=identity,
        observed_at=decoded.captured_at,
        content_type="text/html",
    )
    capture = Capture(
        capture_id=identity,
        requested_url=decoded.url,
        effective_url=decoded.url,
        captured_at=decoded.captured_at,
        timestamp_precision="second",
        http_status=200,
        completeness="complete",
        source=decoded.source,
        payload=Payload(
            content_id=stored.sha256,
            byte_length=stored.size_bytes,
            object_key=stored.object_key,
            storage_encoding="zstd",
            stored_bytes=stored.compressed_size_bytes,
            representation="response_body",
            media_type="text/html",
            declared_media_type=decoded.content_type,
            charset="utf-8",
        ),
    )
    with write_claims({"capture": [str(identity)], "content": [stored.sha256]}):
        Archive(store).commit(capture)
    return capture


async def import_record(
    client: CommonCrawlClient,
    store: ObjectStore,
    queue: ArchivePublisher,
    item: CommonCrawlRecord,
) -> Capture:
    data = await asyncio.to_thread(client.fetch, item)
    evidence = await asyncio.to_thread(archive_record, store, item, data)
    await queue.publish(evidence)
    return evidence
