"""Immutable captures and a contiguous sharded archive journal.

Conditional creation of the next occupied slot is the commit point. Journal
positions belong to object storage, never Postgres or NATS. Lost replies can
produce duplicate references, but never different evidence for one capture ID.
"""

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from typing import Annotated, Iterator, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
import zstandard

from periplus.ingestion.captures import Capture, canonical
from periplus.ingestion.objects.store import ObjectStore, ObjectWriteHeaders
from periplus.ingestion.objects.exceptions import RepositoryObjectNotFound
from periplus.ingestion.objects.html import RawHtmlRepository, HtmlIdentity
from periplus.ingestion.objects.document import (
    ExactDocumentRepository,
    ExactDocumentIdentity,
)

PREFIX = "raw/corpus/v1/"
SHARDS = 16
MAX_RECORD_BYTES = 2 * 1024 * 1024


class ArchiveConflict(ValueError):
    pass


class CaptureRetired(ValueError):
    pass


class ArchiveRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    shard: int = Field(ge=0, lt=SHARDS)
    sequence: int = Field(gt=0)
    capture_id: UUID
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArchiveEvent(ArchiveRef):
    kind: Literal["capture", "retirement"]
    committed_at: AwareDatetime


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


def capture_key(identity: UUID) -> str:
    return f"{PREFIX}captures/{identity.hex[:2]}/{identity}.json.zst"


def event_key(shard: int, sequence: int) -> str:
    if not 0 <= shard < SHARDS or sequence < 1:
        raise ValueError("Invalid archive position")
    return f"{PREFIX}journal/{shard:02x}/{sequence:020d}.json.zst"


class Archive:
    def __init__(self, store: ObjectStore):
        self.store = store
        self._heads: dict[int, int] = {}

    def _put(self, key: str, value: BaseModel) -> bool:
        encoded = canonical(value.model_dump(mode="json"))
        if len(encoded) > MAX_RECORD_BYTES:
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
            compressed = stream.read(MAX_RECORD_BYTES + 65537)
        if len(compressed) > MAX_RECORD_BYTES + 65536:
            raise ValueError("Archive compressed record exceeds byte budget")
        with zstandard.ZstdDecompressor().stream_reader(BytesIO(compressed)) as stream:
            value = stream.read(MAX_RECORD_BYTES + 1)
        if len(value) > MAX_RECORD_BYTES:
            raise ValueError("Archive expanded record exceeds byte budget")
        return model.model_validate_json(value)

    def head(self, shard: int) -> int:
        # Logarithmic discovery after losing all process caches. No LIST inventory,
        # mutable head pointer or per-capture database lookup is required.
        low = self._heads.get(shard, 0)
        if low and not self.store.exists(event_key(shard, low)):
            raise ValueError("Committed archive journal entry disappeared")
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
        self._heads[shard] = low
        return low

    def heads(self) -> tuple[int, ...]:
        return tuple(self.head(shard) for shard in range(SHARDS))

    def event(self, shard: int, sequence: int) -> ArchiveEvent:
        event = self._read(event_key(shard, sequence), ArchiveEvent)
        if (event.shard, event.sequence) != (shard, sequence):
            raise ValueError("Archive journal identity mismatch")
        return event

    def read(self, identity: UUID, expected_digest: str | None = None) -> Capture:
        capture = self._read(capture_key(identity), Capture)
        if capture.capture_id != identity or (
            expected_digest and capture.digest != expected_digest
        ):
            raise ArchiveConflict("Archive capture identity/digest mismatch")
        return capture

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
        self, capture: Capture, kind: Literal["capture", "retirement"]
    ) -> ArchiveEvent:
        shard = capture.capture_id.int % SHARDS
        while True:
            sequence = self.head(shard) + 1
            event = ArchiveEvent(
                shard=shard,
                sequence=sequence,
                capture_id=capture.capture_id,
                digest=capture.digest,
                kind=kind,
                committed_at=datetime.now(UTC),
            )
            if self._put(event_key(shard, sequence), event):
                self._heads[shard] = sequence
                return event
            # Another writer won this slot; immutable existing bytes settle it.
            self.event(shard, sequence)
            self._heads[shard] = sequence

    def receipt(self, identity: UUID) -> ArchiveEvent | None:
        try:
            event = self._read(
                f"{PREFIX}committed/{identity.hex[:2]}/{identity}.json.zst",
                ArchiveEvent,
            )
        except RepositoryObjectNotFound:
            return None
        if (
            self.event(event.shard, event.sequence) != event
            or event.capture_id != identity
        ):
            raise ArchiveConflict("Archive receipt differs from its journal commit")
        return event

    def commit(self, capture: Capture) -> ArchiveEvent:
        if self.retired(capture.capture_id):
            raise CaptureRetired("Capture has been retired")
        existing = self.receipt(capture.capture_id)
        if existing:
            self.read(capture.capture_id, capture.digest)
            if existing.digest != capture.digest:
                raise ArchiveConflict("Conflicting capture identity")
            return existing
        self.verify_payload(capture)
        self._put(capture_key(capture.capture_id), capture)
        self.read(capture.capture_id, capture.digest)
        event = self._append(capture, "capture")
        self._put(
            f"{PREFIX}committed/{capture.capture_id.hex[:2]}/{capture.capture_id}.json.zst",
            event,
        )
        return self.receipt(capture.capture_id)

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
        # Membership changes before the notification. A crash here can delay
        # removal from a serving target but can never resurrect during replay.
        receipt = f"{PREFIX}retirement-commits/{identity.hex[:2]}/{identity}.json.zst"
        try:
            return self._read(receipt, ArchiveEvent)
        except RepositoryObjectNotFound:
            event = self._append(capture, "retirement")
            self._put(receipt, event)
            return self._read(receipt, ArchiveEvent)

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

    def events(
        self, heads: tuple[Annotated[int, Field(ge=0)], ...]
    ) -> Iterator[ArchiveEvent]:
        for shard, upper in enumerate(heads):
            for sequence in range(1, upper + 1):
                yield self.event(shard, sequence)
