"""Bounded ingestion worker configuration."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.platform.config.performance import (
    INGEST_BATCH_MAX_BYTES,
    INGEST_BATCH_MAX_ITEMS,
    INGEST_BATCH_MAX_WAIT_SECONDS,
)


@dataclass(frozen=True, slots=True)
class IngestionWorkerConfig:
    max_items: int = 100
    max_envelope_bytes: int = 256 * 1024 * 1024
    max_wait_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_items <= 0:
            raise ValueError("ingestion batch item limit must be positive")
        if self.max_envelope_bytes <= 0:
            raise ValueError("ingestion batch byte limit must be positive")
        if self.max_wait_seconds <= 0:
            raise ValueError("ingestion batch wait must be positive")

    @classmethod
    def defaults(cls) -> IngestionWorkerConfig:
        return cls(
            max_items=INGEST_BATCH_MAX_ITEMS,
            max_envelope_bytes=INGEST_BATCH_MAX_BYTES,
            max_wait_seconds=INGEST_BATCH_MAX_WAIT_SECONDS,
        )
