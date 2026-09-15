"""Immutable, independently recoverable evidence journal in the raw repository."""

from datetime import UTC, datetime
from io import BytesIO
from typing import Iterator, Literal

from pydantic import BaseModel, ConfigDict
import zstandard

from periplus.ingestion.objects.store import ObjectStore, ObjectWriteHeaders
from periplus.ingestion.queue import IngestionJob

PREFIX = "raw/v1/evidence/"
MAX_ENVELOPE_BYTES = 1024 * 1024


class ArchivedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    job: IngestionJob


def archive_key(job: IngestionJob) -> str:
    source = job.visit.visit.archive_source if job.visit else None
    provider, dataset = (source.provider, source.dataset) if source else ("periplus", "native")
    return f"{PREFIX}{provider}/{dataset}/{job.identity.hex[:2]}/{job.request_id}.json.zst"


def journal(store: ObjectStore, job: IngestionJob) -> str:
    # Queue timestamps are delivery state, not observation evidence. Normalize
    # them so archive retries are independent of the importing process clock.
    frozen = job.model_copy(update={"enqueued_at": datetime(1970, 1, 1, tzinfo=UTC)})
    envelope = ArchivedEvidence(job=frozen)
    encoded = envelope.model_dump_json().encode()
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise ValueError("raw envelope exceeds byte limit")
    key = archive_key(job)
    data = zstandard.ZstdCompressor(level=6).compress(encoded)
    store.put_if_absent(key, BytesIO(data), headers=ObjectWriteHeaders(
        content_type="application/json", content_encoding="zstd"))
    if read_archive(store, key) != envelope:
        raise ValueError("conflicting immutable raw evidence identity")
    return key


def read_archive(store: ObjectStore, key: str) -> ArchivedEvidence:
    if not key.startswith(PREFIX) or not key.endswith(".json.zst"):
        raise ValueError("not a raw evidence object")
    with store.open(key) as stream:
        compressed = stream.read(MAX_ENVELOPE_BYTES + 1)
    if len(compressed) > MAX_ENVELOPE_BYTES:
        raise ValueError("raw envelope exceeds byte limit")
    with zstandard.ZstdDecompressor().stream_reader(BytesIO(compressed)) as reader:
        data = reader.read(MAX_ENVELOPE_BYTES + 1)
    if len(data) > MAX_ENVELOPE_BYTES:
        raise ValueError("expanded raw envelope exceeds byte limit")
    envelope = ArchivedEvidence.model_validate_json(data)
    if archive_key(envelope.job) != key:
        raise ValueError("raw evidence identity differs from its object key")
    return envelope


def archived_jobs(store: ObjectStore, prefix: str = PREFIX) -> Iterator[IngestionJob]:
    if not prefix.startswith(PREFIX):
        raise ValueError("recovery prefix must be inside raw evidence")
    for item in store.list_objects(prefix):
        if item.key.endswith(".json.zst"):
            yield read_archive(store, item.key).job.model_copy(
                update={"enqueued_at": datetime.now(UTC)})
