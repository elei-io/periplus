"""Immutable metadata batches over separate content-addressed payloads.

The next conditional journal-segment creation is the only capture publication
point. Logical event sequences remain contiguous inside variable-sized segments.
An expendable SQLite lookup cache enforces exact capture identities before ACK;
archive-only readers can stream segments without that cache or any database.
"""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from threading import RLock
from typing import Annotated, Iterator, Literal
from uuid import UUID
import re

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
import zstandard

from periplus.ingestion.captures import Capture, canonical
from periplus.ingestion.objects.store import ObjectStore, ObjectWriteHeaders
from periplus.ingestion.objects.exceptions import RepositoryObjectNotFound
from periplus.ingestion.objects.html import RawHtmlRepository, HtmlIdentity
from periplus.ingestion.objects.document import (
    ExactDocumentRepository,
    ExactDocumentIdentity,
)
from periplus.ingestion.archive_index import index_for

PREFIX = "raw/corpus/v1/"
SHARDS = 16
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_BATCH_RECORDS = 64
SEGMENTS_PER_DIRECTORY = 4096


class ArchiveConflict(ValueError):
    pass


class CaptureRetired(ValueError):
    pass


class ArchiveRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    shard: int = Field(ge=0, lt=SHARDS)
    sequence: int = Field(gt=0)
    segment: int = Field(gt=0)
    offset: int = Field(ge=0, lt=MAX_BATCH_RECORDS)
    capture_id: UUID
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArchiveEvent(ArchiveRef):
    kind: Literal["capture", "retirement"]
    committed_at: AwareDatetime


class JournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["capture", "retirement"]
    capture: Capture


class MetadataBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    shard: int = Field(ge=0, lt=SHARDS)
    segment: int = Field(gt=0)
    start: int = Field(gt=0)
    committed_at: AwareDatetime
    records: tuple[JournalRecord, ...] = Field(
        min_length=1, max_length=MAX_BATCH_RECORDS
    )
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def end(self) -> int:
        return self.start + len(self.records) - 1

    @model_validator(mode="after")
    def validate_records(self):
        seen = set()
        for record in self.records:
            capture = record.capture
            if (
                capture.capture_id.int % SHARDS != self.shard
                or capture.capture_id in seen
            ):
                raise ValueError("Invalid or duplicate capture in metadata shard")
            seen.add(capture.capture_id)
            if len(canonical(capture.model_dump(mode="json"))) > MAX_RECORD_BYTES:
                raise ValueError("Archive capture exceeds byte budget")
        if (
            self.digest
            != sha256(
                canonical([r.model_dump(mode="json") for r in self.records])
            ).hexdigest()
        ):
            raise ArchiveConflict("Metadata batch digest mismatch")
        return self

    def event(self, offset: int) -> ArchiveEvent:
        record = self.records[offset]
        return ArchiveEvent(
            shard=self.shard,
            sequence=self.start + offset,
            segment=self.segment,
            offset=offset,
            capture_id=record.capture.capture_id,
            digest=record.capture.digest,
            kind=record.kind,
            committed_at=self.committed_at,
        )


class Retirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    capture_id: UUID
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    retired_at: AwareDatetime


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    format_version: Literal[1] = 1
    heads: tuple[Annotated[int, Field(ge=0)], ...] = Field(
        min_length=SHARDS, max_length=SHARDS
    )
    recipe: str = Field(pattern=r"^[0-9a-f]{64}$")
    software_key: str
    public_schema: Literal["public_v1"] = "public_v1"


def event_key(shard: int, segment: int) -> str:
    if not 0 <= shard < SHARDS or segment < 1:
        raise ValueError("Invalid archive segment")
    return f"{PREFIX}journal/{shard:02x}/{(segment - 1) // SEGMENTS_PER_DIRECTORY:010d}/{segment:020d}.json.zst"


def capture_key(reference: ArchiveRef) -> str:
    return f"{event_key(reference.shard, reference.segment)}#{reference.offset}"


class Archive:
    def __init__(self, store: ObjectStore):
        self.store = store
        self._segments: dict[int, int] = {}
        self._batches: OrderedDict[tuple[int, int], MetadataBatch] = OrderedDict()
        self._locations: OrderedDict[UUID, ArchiveEvent] = OrderedDict()
        self._cache_lock = RLock()

    def _put(self, key: str, value: BaseModel) -> bool:
        encoded = canonical(value.model_dump(mode="json"))
        if len(encoded) > MAX_BATCH_BYTES:
            raise ValueError("Archive record exceeds byte budget")
        return self.store.put_if_absent(
            key,
            BytesIO(zstandard.ZstdCompressor(level=6).compress(encoded)),
            headers=ObjectWriteHeaders(
                content_type="application/json", content_encoding="zstd"
            ),
        )

    def _read(self, key: str, model):
        with self.store.open(key) as stream:
            compressed = stream.read(MAX_BATCH_BYTES + 65537)
        if len(compressed) > MAX_BATCH_BYTES + 65536:
            raise ValueError("Archive compressed record exceeds byte budget")
        with zstandard.ZstdDecompressor().stream_reader(BytesIO(compressed)) as stream:
            value = stream.read(MAX_BATCH_BYTES + 1)
        if len(value) > MAX_BATCH_BYTES:
            raise ValueError("Archive expanded record exceeds byte budget")
        return model.model_validate_json(value)

    def segment_head(self, shard: int) -> int:
        low = self._segments.get(shard, 0)
        if low and not self.store.exists(event_key(shard, low)):
            raise RepositoryObjectNotFound(event_key(shard, low))
        high = low + 1
        if self.store.exists(event_key(shard, high)):
            low, high = high, max(2, high * 2)
            while self.store.exists(event_key(shard, high)):
                low, high = high, high * 2
            while high - low > 1:
                middle = (low + high) // 2
                if self.store.exists(event_key(shard, middle)):
                    low = middle
                else:
                    high = middle
        self._segments[shard] = low
        return low

    def batch(self, shard: int, segment: int) -> MetadataBatch:
        position = (shard, segment)
        with self._cache_lock:
            cached = self._batches.get(position)
            if cached:
                self._batches.move_to_end(position)
                return cached
        batch = self._read(event_key(shard, segment), MetadataBatch)
        if (batch.shard, batch.segment) != position:
            raise ArchiveConflict("Archive segment identity mismatch")
        with self._cache_lock:
            self._batches[position] = batch
            while len(self._batches) > 16:
                self._batches.popitem(last=False)
        return batch

    def head(self, shard: int) -> int:
        segment = self.segment_head(shard)
        return self.batch(shard, segment).end if segment else 0

    def heads(self) -> tuple[int, ...]:
        return tuple(self.head(shard) for shard in range(SHARDS))

    def range_events(self, shard: int, start: int, end: int) -> Iterator[ArchiveEvent]:
        if start < 1 or end < start:
            raise ValueError("Invalid archive range")
        segment = 1
        if start > 1:
            low, high = 1, self.segment_head(shard)
            while low < high:
                middle = (low + high) // 2
                if self.batch(shard, middle).end < start:
                    low = middle + 1
                else:
                    high = middle
            segment = low
        expected = start
        while expected <= end:
            batch = self.batch(shard, segment)
            if not batch.start <= expected <= batch.end:
                raise ArchiveConflict("Archive logical event range has a gap")
            for offset in range(
                expected - batch.start, min(len(batch.records), end - batch.start + 1)
            ):
                yield batch.event(offset)
                expected += 1
            segment += 1

    def event(self, shard: int, sequence: int) -> ArchiveEvent:
        return next(self.range_events(shard, sequence, sequence))

    def read_event(self, event: ArchiveEvent) -> Capture:
        batch = self.batch(event.shard, event.segment)
        if event.offset >= len(batch.records) or batch.event(event.offset) != event:
            raise ArchiveConflict(
                "Archive reference differs from its committed segment"
            )
        capture = batch.records[event.offset].capture
        with self._cache_lock:
            self._locations[capture.capture_id] = event
            self._locations.move_to_end(capture.capture_id)
            while len(self._locations) > 256:
                self._locations.popitem(last=False)
        return capture

    def _synchronize(self, shard: int):
        index = index_for(self.store)
        segment = self.segment_head(shard)
        if segment < index.cursors[shard][0]:
            raise ArchiveConflict("Committed archive tail disappeared")
        for number in range(index.cursors[shard][0] + 1, segment + 1):
            index.apply(self.batch(shard, number))
        return index

    def warm_index(self) -> None:
        """Replay metadata before serving publication; never hold write claims."""
        index = index_for(self.store)

        def warm(shard):
            with index.shards[shard]:
                self._synchronize(shard)

        with ThreadPoolExecutor(max_workers=SHARDS) as pool:
            list(pool.map(warm, range(SHARDS)))

    def receipt(
        self, identity: UUID, *, retirement: bool = False
    ) -> ArchiveEvent | None:
        index = index_for(self.store)
        shard = identity.int % SHARDS
        with index.shards[shard]:
            self._synchronize(shard)
            row = index.lookup(identity, retirement=retirement)
            if row is None:
                return None
            digest, sequence, segment, offset = row
            event = self.batch(shard, segment).event(offset)
            if (event.capture_id, event.digest, event.sequence) != (
                identity,
                digest.hex(),
                sequence,
            ):
                raise ArchiveConflict("Derived capture lookup differs from archive")
            return event

    def read(self, identity: UUID, expected_digest: str | None = None) -> Capture:
        with self._cache_lock:
            event = self._locations.get(identity)
        event = event or self.receipt(identity)
        if event is None:
            raise RepositoryObjectNotFound(str(identity))
        capture = self.read_event(event)
        if expected_digest and capture.digest != expected_digest:
            raise ArchiveConflict("Archive capture digest mismatch")
        return capture

    def location(self, capture: Capture) -> str:
        self.read(capture.capture_id, capture.digest)
        with self._cache_lock:
            return capture_key(self._locations[capture.capture_id])

    def read_location(self, key: str, identity: UUID, digest: str) -> Capture:
        match = re.fullmatch(
            re.escape(PREFIX)
            + r"journal/([0-9a-f]{2})/([0-9]{10})/([0-9]{20})\.json\.zst#([0-9]+)",
            key,
        )
        if not match:
            raise ValueError("Invalid capture locator")
        shard, segment, offset = int(match[1], 16), int(match[3]), int(match[4])
        if int(match[2]) != (segment - 1) // SEGMENTS_PER_DIRECTORY:
            raise ValueError("Invalid capture locator directory")
        batch = self.batch(shard, segment)
        if not 0 <= offset < len(batch.records):
            raise ValueError("Invalid capture locator offset")
        event = batch.event(offset)
        if event.capture_id != identity or event.digest != digest:
            raise ArchiveConflict("Capture locator identity/digest differs")
        return self.read_event(event)

    def retired(self, identity: UUID) -> bool:
        return self.store.exists(
            f"{PREFIX}retired/{identity.hex[:2]}/{identity}.json.zst"
        )

    def verify_payload(self, capture: Capture) -> None:
        payload = capture.payload
        if payload is None:
            return
        if payload.byte_length > 256 * 1024 * 1024:
            raise ValueError("Capture payload exceeds ingestion budget")
        if payload.storage_encoding == "zstd":
            RawHtmlRepository(self.store).verify(
                payload.object_key,
                expected=HtmlIdentity(
                    sha256=payload.content_id, size_bytes=payload.byte_length
                ),
            )
        else:
            ExactDocumentRepository(self.store).verify(
                payload.object_key,
                expected=ExactDocumentIdentity(
                    sha256=payload.content_id, size_bytes=payload.byte_length
                ),
            )
        if self.store.size(payload.object_key) != payload.stored_bytes:
            raise ValueError("Archive payload stored length mismatch")

    def _append(
        self, captures: list[Capture], kind: Literal["capture", "retirement"]
    ) -> list[ArchiveEvent]:
        shard = captures[0].capture_id.int % SHARDS
        index = index_for(self.store)
        with index.shards[shard]:
            while True:
                self._synchronize(shard)
                existing, missing = {}, []
                for capture in captures:
                    row = index.lookup(capture.capture_id)
                    if row and row[0].hex() != capture.digest:
                        raise ArchiveConflict("Conflicting capture identity")
                    if kind == "capture" and self.retired(capture.capture_id):
                        raise CaptureRetired("Capture has been retired")
                    selected = index.lookup(
                        capture.capture_id, retirement=kind == "retirement"
                    )
                    if selected:
                        existing[capture.capture_id] = self.batch(
                            shard, selected[2]
                        ).event(selected[3])
                    else:
                        missing.append(capture)
                if not missing:
                    return [existing[c.capture_id] for c in captures]
                records = tuple(JournalRecord(kind=kind, capture=c) for c in missing)
                previous, sequence = index.cursors[shard]
                batch = MetadataBatch(
                    shard=shard,
                    segment=previous + 1,
                    start=sequence + 1,
                    committed_at=datetime.now(UTC),
                    records=records,
                    digest=sha256(
                        canonical([r.model_dump(mode="json") for r in records])
                    ).hexdigest(),
                )
                created = self._put(event_key(shard, batch.segment), batch)
                # Read the actual conditional-write winner. A competing writer may
                # have committed different records, so its facts must be indexed
                # before checking our identities again. No local index is authority.
                with self._cache_lock:
                    self._batches.pop((shard, batch.segment), None)
                actual = self.batch(shard, batch.segment)
                if created and actual != batch:
                    raise ArchiveConflict("Published metadata batch differs")
                index.apply(actual)
                self._segments[shard] = actual.segment
                if created:
                    existing.update(
                        {
                            event.capture_id: event
                            for event in (
                                actual.event(i) for i in range(len(actual.records))
                            )
                        }
                    )
                    return [existing[c.capture_id] for c in captures]

    def commit_many(self, captures: list[Capture]) -> list[ArchiveEvent]:
        if not 1 <= len(captures) <= MAX_BATCH_RECORDS:
            raise ValueError("Archive publication accepts 1–64 captures")
        unique = {}
        for capture in captures:
            if (
                capture.capture_id in unique
                and unique[capture.capture_id].digest != capture.digest
            ):
                raise ArchiveConflict("Conflicting capture identities within batch")
            unique[capture.capture_id] = capture
        groups = [
            [c for c in unique.values() if c.capture_id.int % SHARDS == shard]
            for shard in range(SHARDS)
        ]
        groups = [group for group in groups if group]

        def preflight(group):
            shard = group[0].capture_id.int % SHARDS
            index = index_for(self.store)
            with index.shards[shard]:
                self._synchronize(shard)
                for capture in group:
                    existing = index.lookup(capture.capture_id)
                    if existing and existing[0].hex() != capture.digest:
                        raise ArchiveConflict("Conflicting capture identity")
                    if (
                        len(canonical(capture.model_dump(mode="json")))
                        > MAX_RECORD_BYTES
                    ):
                        raise ValueError("Archive capture exceeds byte budget")

        def verify(capture):
            if self.retired(capture.capture_id):
                raise CaptureRetired("Capture has been retired")
            self.verify_payload(capture)

        # Preflight all identities and bodies before this request writes any
        # segment. Conditional shard publication rechecks concurrent winners.
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(preflight, groups))
            list(pool.map(verify, unique.values()))

        def publish(group):
            result = []
            pending, size = [], 0
            for capture in group:
                amount = len(canonical(capture.model_dump(mode="json"))) + 128
                if pending and size + amount > MAX_BATCH_BYTES - 4096:
                    result.extend(self._append(pending, "capture"))
                    pending, size = [], 0
                pending.append(capture)
                size += amount
            if pending:
                result.extend(self._append(pending, "capture"))
            return result

        with ThreadPoolExecutor(max_workers=min(16, len(groups))) as pool:
            result = {
                event.capture_id: event
                for events in pool.map(publish, groups)
                for event in events
            }
        return [result[c.capture_id] for c in captures]

    def commit(self, capture: Capture) -> ArchiveEvent:
        return self.commit_many([capture])[0]

    def retire(self, identity: UUID) -> ArchiveEvent:
        capture = self.read(identity)
        key = f"{PREFIX}retired/{identity.hex[:2]}/{identity}.json.zst"
        self._put(
            key,
            Retirement(
                capture_id=identity, digest=capture.digest, retired_at=datetime.now(UTC)
            ),
        )
        tombstone = self._read(key, Retirement)
        if tombstone.capture_id != identity or tombstone.digest != capture.digest:
            raise ArchiveConflict("Retirement identity mismatch")
        return self._append([capture], "retirement")[0]

    def manifest(self, recipe: str, software_key: str) -> tuple[str, Manifest]:
        manifest = Manifest(
            heads=self.heads(), recipe=recipe, software_key=software_key
        )
        digest = sha256(canonical(manifest.model_dump(mode="json"))).hexdigest()
        key = f"{PREFIX}manifests/{digest}.json.zst"
        self._put(key, manifest)
        return key, self.read_manifest(key)

    def read_manifest(self, key: str) -> Manifest:
        if not key.startswith(f"{PREFIX}manifests/"):
            raise ValueError("Manifest must belong to the corpus archive")
        value = self._read(key, Manifest)
        digest = sha256(canonical(value.model_dump(mode="json"))).hexdigest()
        if key != f"{PREFIX}manifests/{digest}.json.zst":
            raise ValueError("Manifest digest mismatch")
        return value

    def events(self, heads: tuple[int, ...]) -> Iterator[ArchiveEvent]:
        if len(heads) != SHARDS or any(h < 0 for h in heads):
            raise ValueError("Invalid archive cut")
        for shard, upper in enumerate(heads):
            if upper:
                yield from self.range_events(shard, 1, upper)
