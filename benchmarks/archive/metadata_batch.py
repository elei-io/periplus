"""Experimental metadata journal; payloads remain separate production HTML objects.

One conditional object creation publishes a bounded batch of complete envelopes.
There are 16 contiguous journals, no mutable pointer or full-corpus writer index.
Replay may append duplicate references; readers reject conflicting capture facts.
This is a benchmark, not a replacement for Archive (no retirement API yet).
"""

from hashlib import sha256
from io import BytesIO
from typing import Iterator

import zstandard

from periplus.ingestion.archive import Archive, ArchiveConflict
from periplus.ingestion.captures import Capture, canonical
from periplus.ingestion.objects.store import ObjectStore

PREFIX = "raw/metadata-benchmark/v1"
MAX_BATCH_BYTES = 8 * 1024 * 1024


def key(shard: int, sequence: int) -> str:
    return f"{PREFIX}/{shard:02x}/{sequence:020d}.json.zst"


class MetadataBatch:
    def __init__(self, store: ObjectStore):
        self.store = store
        self.heads: dict[int, int] = {}

    def head(self, shard: int) -> int:
        low = self.heads.get(shard, 0)
        high = low + 1
        if self.store.exists(key(shard, high)):
            low, high = high, max(2, high * 2)
            while self.store.exists(key(shard, high)):
                low, high = high, high * 2
            while high - low > 1:
                middle = (low + high) // 2
                if self.store.exists(key(shard, middle)):
                    low = middle
                else:
                    high = middle
        self.heads[shard] = low
        return low

    def read(self, shard: int, sequence: int) -> list[Capture]:
        with self.store.open(key(shard, sequence)) as stream:
            with zstandard.ZstdDecompressor().stream_reader(stream) as reader:
                data = reader.read(MAX_BATCH_BYTES + 1)
        if len(data) > MAX_BATCH_BYTES:
            raise ValueError("Metadata batch exceeds budget")
        import json

        record = json.loads(data)
        captures = [Capture.model_validate(c) for c in record["captures"]]
        if (record["shard"], record["sequence"]) != (shard, sequence):
            raise ArchiveConflict("Batch position differs")
        if record["digest"] != self.digest(captures):
            raise ArchiveConflict("Batch digest differs")
        if not 1 <= len(captures) <= 64:
            raise ValueError("Invalid batch size")
        return captures

    @staticmethod
    def digest(captures: list[Capture]) -> str:
        return sha256(
            canonical([c.model_dump(mode="json") for c in captures])
        ).hexdigest()

    def commit(self, captures: list[Capture], shard: int) -> tuple[int, int]:
        if not 0 <= shard < 16 or not 1 <= len(captures) <= 64:
            raise ValueError("Invalid batch")
        for capture in captures:
            Archive(self.store).verify_payload(capture)
        digest = self.digest(captures)
        while True:
            sequence = self.head(shard) + 1
            raw = canonical(
                dict(
                    shard=shard,
                    sequence=sequence,
                    digest=digest,
                    captures=[c.model_dump(mode="json") for c in captures],
                )
            )
            if len(raw) > MAX_BATCH_BYTES:
                raise ValueError("Metadata batch exceeds budget")
            created = self.store.put_if_absent(
                key(shard, sequence),
                BytesIO(zstandard.ZstdCompressor(level=6).compress(raw)),
            )
            existing = self.read(shard, sequence)
            self.heads[shard] = sequence
            if created or self.digest(existing) == digest:
                if self.digest(existing) != digest:
                    raise ArchiveConflict("Published batch differs")
                return shard, sequence

    def events(self) -> Iterator[Capture]:
        for shard in range(16):
            for sequence in range(1, self.head(shard) + 1):
                yield from self.read(shard, sequence)

    def recover(self) -> list[Capture]:
        # Verification helper only. Production material identities, not this
        # unbounded dictionary, must handle duplicate journal references.
        found: dict[str, Capture] = {}
        for capture in self.events():
            identity = str(capture.capture_id)
            if identity in found and found[identity].digest != capture.digest:
                raise ArchiveConflict("Conflicting capture identity")
            found[identity] = capture
        return list(found.values())
