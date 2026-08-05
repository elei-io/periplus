"""Bounded ingestor configuration."""

from __future__ import annotations

from dataclasses import dataclass

from periplus.platform.config.performance import (
    INGEST_BATCH_MAX_ITEMS,
    INGEST_BATCH_MAX_WAIT_SECONDS,
)


@dataclass(frozen=True, slots=True)
class IngestionWorkerConfig:
    max_items: int = 100
    max_wait_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_items <= 0:
            raise ValueError("ingestion batch item limit must be positive")
        if self.max_wait_seconds <= 0:
            raise ValueError("ingestion batch wait must be positive")

    @classmethod
    def defaults(cls) -> IngestionWorkerConfig:
        return cls(
            max_items=INGEST_BATCH_MAX_ITEMS,
            max_wait_seconds=INGEST_BATCH_MAX_WAIT_SECONDS,
        )
