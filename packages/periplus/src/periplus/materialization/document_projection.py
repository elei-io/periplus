"""Shared one-pass context for independently registered projections."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from itertools import islice

import pyarrow as pa

from periplus.ingestion.objects.document import ExactDocumentRepository
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.materialization.metrics import step
from periplus.materialization.search_text import SearchText, build_search_text
from periplus.materialization.dom import (
    ElementRow,
)

from periplus.materialization.dom.nodes import NodeRow, parse_document


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
    parsed_nodes_by_content: dict[str, tuple[NodeRow, ...]]
    observations_by_content: dict[str, tuple[DocumentObservation, ...]]
    content_output_hashes: frozenset[str]
    search_text_by_content: dict[str, SearchText] = field(default_factory=dict)

    def search_text(self, content_id: str) -> SearchText:
        if content_id not in self.search_text_by_content:
            with step("search_text"):
                self.search_text_by_content[content_id] = build_search_text(self.parsed_nodes_by_content[content_id])
        return self.search_text_by_content[content_id]


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
    nodes: dict[str, tuple[NodeRow, ...]] = {}
    observations: dict[str, tuple[DocumentObservation, ...]] = {}
    for source in sources:
        with step("html_read_decode"):
            if source.storage_encoding == "zstd":
                html: str | bytes = html_repository.read(source.object_key)
            elif source.storage_encoding == "identity":
                html = exact_repository.read_bytes(source.object_key)
            else:
                raise ValueError(
                    "unsupported HTML storage encoding "
                    f"{source.storage_encoding!r}"
                )
        with step("html_parse"):
            nodes[source.content_sha256], parsed[source.content_sha256] = parse_document(html)
        observations[source.content_sha256] = source.observations
    return VisitBatchContext(
        visits=visits,
        documents=documents,
        sources=sources,
        parsed_elements_by_content=parsed,
        parsed_nodes_by_content=nodes,
        observations_by_content=observations,
        content_output_hashes=(
            frozenset(parsed)
            if content_output_hashes is None
            else content_output_hashes
        ),
    )


def table_from_rows(
    schema: pa.Schema,
    rows: Iterable[tuple[object, ...]],
) -> pa.Table:
    """Convert bounded row chunks without retaining a second full Python table."""
    iterator = iter(rows)
    batches: list[pa.RecordBatch] = []
    while chunk := list(islice(iterator, 8192)):
        batches.append(
            pa.RecordBatch.from_arrays(
                [
                    pa.array([row[index] for row in chunk], type=field.type)
                    for index, field in enumerate(schema)
                ],
                schema=schema,
            )
        )
    return pa.Table.from_batches(batches, schema=schema)


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
