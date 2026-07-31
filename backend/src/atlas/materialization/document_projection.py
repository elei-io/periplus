"""Shared one-pass context for independently registered projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pyarrow as pa

from atlas.ingestion.objects.document import ExactDocumentRepository
from atlas.ingestion.objects.html import RawHtmlRepository
from atlas.materialization.dom import (
    ElementRow,
    iter_html_byte_elements,
    iter_html_elements,
)


@dataclass(frozen=True, slots=True)
class DocumentObservation:
    visit_id: str
    document_id: str
    source_url: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentProjectionSource:
    content_sha256: str
    object_key: str
    storage_encoding: str
    content_bytes: int
    observations: tuple[DocumentObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class VisitBatchContext:
    """One memoized parse context shared by every discovered projection."""

    visits: tuple[tuple[object, ...], ...]
    documents: tuple[tuple[object, ...], ...]
    sources: tuple[DocumentProjectionSource, ...]
    parsed_elements_by_content: dict[str, tuple[ElementRow, ...]]
    observations_by_content: dict[str, tuple[DocumentObservation, ...]]
    content_output_hashes: frozenset[str]


def build_visit_batch_context(
    html_repository: RawHtmlRepository,
    sources: tuple[DocumentProjectionSource, ...],
    *,
    visits: tuple[tuple[object, ...], ...] = (),
    documents: tuple[tuple[object, ...], ...] = (),
    content_output_hashes: frozenset[str] | None = None,
) -> VisitBatchContext:
    """Read and parse each unique content body exactly once for this batch."""

    exact_repository = ExactDocumentRepository(html_repository.store)
    parsed: dict[str, tuple[ElementRow, ...]] = {}
    observations: dict[str, tuple[DocumentObservation, ...]] = {}
    for source in sources:
        if source.storage_encoding == "zstd":
            html: str | bytes = html_repository.read(source.object_key)
        elif source.storage_encoding == "identity":
            html = exact_repository.read_bytes(source.object_key)
        else:
            raise ValueError(
                "unsupported HTML storage encoding "
                f"{source.storage_encoding!r}"
            )
        parsed[source.content_sha256] = tuple(
            iter_html_byte_elements(html)
            if isinstance(html, bytes)
            else iter_html_elements(html)
        )
        observations[source.content_sha256] = source.observations
    return VisitBatchContext(
        visits=visits,
        documents=documents,
        sources=sources,
        parsed_elements_by_content=parsed,
        observations_by_content=observations,
        content_output_hashes=(
            frozenset(parsed)
            if content_output_hashes is None
            else content_output_hashes
        ),
    )


def table_from_rows(
    schema: pa.Schema,
    rows: list[tuple[object, ...]],
) -> pa.Table:
    """Build a correctly typed Arrow table, including the empty case."""

    return pa.Table.from_arrays(
        [
            pa.array(
                [row[index] for row in rows],
                type=field.type,
            )
            for index, field in enumerate(schema)
        ],
        schema=schema,
    )


def ducklake_varchar_bucket(value: str, buckets: int) -> int:
    """Iceberg-compatible bucket transform used by DuckLake VARCHAR keys."""

    if buckets < 1:
        raise ValueError("bucket count must be positive")
    return (_murmur3_x86_32(value.encode("utf-8")) & 0x7FFFFFFF) % buckets


def _murmur3_x86_32(data: bytes, seed: int = 0) -> int:
    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    value = seed & 0xFFFFFFFF
    block_end = len(data) & ~3
    for offset in range(0, block_end, 4):
        block = int.from_bytes(data[offset : offset + 4], "little")
        block = (block * c1) & 0xFFFFFFFF
        block = ((block << 15) | (block >> 17)) & 0xFFFFFFFF
        block = (block * c2) & 0xFFFFFFFF
        value ^= block
        value = ((value << 13) | (value >> 19)) & 0xFFFFFFFF
        value = (value * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[block_end:]
    block = 0
    if len(tail) == 3:
        block ^= tail[2] << 16
    if len(tail) >= 2:
        block ^= tail[1] << 8
    if tail:
        block ^= tail[0]
        block = (block * c1) & 0xFFFFFFFF
        block = ((block << 15) | (block >> 17)) & 0xFFFFFFFF
        block = (block * c2) & 0xFFFFFFFF
        value ^= block
    value ^= len(data)
    value ^= value >> 16
    value = (value * 0x85EBCA6B) & 0xFFFFFFFF
    value ^= value >> 13
    value = (value * 0xC2B2AE35) & 0xFFFFFFFF
    value ^= value >> 16
    return value
