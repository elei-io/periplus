"""One-pass local projections derived from immutable HTML bytes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import pyarrow as pa
import tldextract
from atlas.urls import normalize_url
from atlas.materialization.dom import (
    ElementRow,
    iter_html_byte_elements,
    iter_html_elements,
    links_from_elements,
)
from atlas.platform.catalogue import link_id_for, page_id_for
from atlas.ingestion.objects.document import ExactDocumentRepository
from atlas.ingestion.objects.html import RawHtmlRepository

_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())

HTML_ELEMENT_SCHEMA = pa.schema(
    [
        pa.field("content_sha256", pa.string(), nullable=False),
        pa.field("element_index", pa.int32(), nullable=False),
        pa.field("parent_index", pa.int32()),
        pa.field("subtree_end_index", pa.int32(), nullable=False),
        pa.field("depth", pa.int32(), nullable=False),
        pa.field("child_index", pa.int32(), nullable=False),
        pa.field("tag", pa.string(), nullable=False),
        pa.field("namespace", pa.string(), nullable=False),
        pa.field(
            "attributes",
            pa.map_(pa.string(), pa.string()),
            nullable=False,
        ),
        pa.field("text_direct", pa.string(), nullable=False),
        pa.field("text_tail", pa.string(), nullable=False),
    ]
)
JSONLD_SCHEMA = pa.schema(
    [
        pa.field("content_sha256", pa.string(), nullable=False),
        pa.field("element_index", pa.int32(), nullable=False),
        pa.field("type_terms", pa.list_(pa.string()), nullable=False),
        # The writer casts this canonical JSON string to JSON and then VARIANT.
        pa.field("value", pa.string(), nullable=False),
    ]
)
LINK_SCHEMA = pa.schema(
    [
        pa.field("link_id", pa.string(), nullable=False),
        pa.field("source_page_id", pa.string(), nullable=False),
        pa.field("target_page_id", pa.string(), nullable=False),
        pa.field("source_url", pa.string(), nullable=False),
        pa.field("target_url", pa.string(), nullable=False),
        pa.field("relation_scope", pa.string(), nullable=False),
    ]
)
LINK_OBSERVATION_SCHEMA = pa.schema(
    [
        pa.field("link_id", pa.string(), nullable=False),
        pa.field("document_id", pa.string(), nullable=False),
        pa.field("content_sha256", pa.string(), nullable=False),
        pa.field("element_index", pa.int32(), nullable=False),
        pa.field("raw_href", pa.string(), nullable=False),
        pa.field(
            "observed_at",
            pa.timestamp("us", tz="UTC"),
            nullable=False,
        ),
    ]
)


@dataclass(frozen=True, slots=True)
class DocumentObservation:
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
class DocumentProjection:
    """Typed rows produced by one read and one parse of each source body."""

    content_hashes: frozenset[str]
    document_ids: frozenset[str]
    html_elements: pa.Table
    jsonld_values: pa.Table
    links: pa.Table
    link_observations: pa.Table

    def bytes_for(self, targets: frozenset[str]) -> int:
        return sum(
            getattr(self, target).nbytes
            for target in targets
        )


def ducklake_varchar_bucket(value: str, buckets: int) -> int:
    """Iceberg-compatible bucket transform used by DuckLake VARCHAR keys."""

    if buckets < 1:
        raise ValueError("bucket count must be positive")
    return (_murmur3_x86_32(value.encode("utf-8")) & 0x7FFFFFFF) % buckets


def project_documents(
    html_repository: RawHtmlRepository,
    sources: tuple[DocumentProjectionSource, ...],
) -> DocumentProjection:
    """Read and parse every distinct content body exactly once."""

    exact_repository = ExactDocumentRepository(html_repository.store)
    html_columns: list[list[object]] = [[] for _ in HTML_ELEMENT_SCHEMA]
    jsonld_columns: list[list[object]] = [[] for _ in JSONLD_SCHEMA]
    link_rows: dict[str, tuple[object, ...]] = {}
    observation_rows: dict[tuple[str, int], tuple[object, ...]] = {}
    content_hashes: set[str] = set()
    document_ids: set[str] = set()

    for source in sources:
        content_hashes.add(source.content_sha256)
        if source.storage_encoding == "zstd":
            html: str | bytes = html_repository.read(source.object_key)
        elif source.storage_encoding == "identity":
            html = exact_repository.read_bytes(source.object_key)
        else:
            raise ValueError(
                "unsupported HTML storage encoding "
                f"{source.storage_encoding!r}"
            )
        elements = tuple(
            iter_html_byte_elements(html)
            if isinstance(html, bytes)
            else iter_html_elements(html)
        )
        _append_html_columns(
            html_columns,
            content_sha256=source.content_sha256,
            elements=elements,
        )
        _append_jsonld_columns(
            jsonld_columns,
            content_sha256=source.content_sha256,
            elements=elements,
        )
        for observation in source.observations:
            document_ids.add(observation.document_id)
            grouped = links_from_elements(
                elements,
                page_url=observation.source_url,
            )
            for link in (*grouped["internal"], *grouped["external"]):
                source_url = str(link["source_url"])
                target_url = str(link["target_url"])
                source_page_id = page_id_for(source_url)
                target_page_id = page_id_for(target_url)
                link_id = str(
                    link_id_for(source_page_id, target_page_id)
                )
                link_rows[link_id] = (
                    link_id,
                    str(source_page_id),
                    str(target_page_id),
                    source_url,
                    target_url,
                    _relation_scope(source_url, target_url),
                )
                element_index = int(link["element_index"])
                observation_rows[
                    (observation.document_id, element_index)
                ] = (
                    link_id,
                    observation.document_id,
                    source.content_sha256,
                    element_index,
                    str(link["raw_href"]),
                    observation.observed_at,
                )

    return DocumentProjection(
        content_hashes=frozenset(content_hashes),
        document_ids=frozenset(document_ids),
        html_elements=_table_from_columns(
            HTML_ELEMENT_SCHEMA,
            html_columns,
        ),
        jsonld_values=_table_from_columns(
            JSONLD_SCHEMA,
            jsonld_columns,
        ),
        links=_table_from_rows(
            LINK_SCHEMA,
            [link_rows[key] for key in sorted(link_rows)],
        ),
        link_observations=_table_from_rows(
            LINK_OBSERVATION_SCHEMA,
            [
                observation_rows[key]
                for key in sorted(observation_rows)
            ],
        ),
    )


def _append_html_columns(
    columns: list[list[object]],
    *,
    content_sha256: str,
    elements: tuple[ElementRow, ...],
) -> None:
    for element in elements:
        values = (
            content_sha256,
            element.element_index,
            element.parent_index,
            element.subtree_end_index,
            element.depth,
            element.child_index,
            element.tag.lower(),
            _namespace_name(element.namespace_uri),
            list(element.attributes.items()),
            element.text_direct,
            element.text_tail,
        )
        for column, value in zip(columns, values, strict=True):
            column.append(value)


def _append_jsonld_columns(
    columns: list[list[object]],
    *,
    content_sha256: str,
    elements: tuple[ElementRow, ...],
) -> None:
    for element in elements:
        if element.tag.lower() != "script":
            continue
        media_type = (
            str(element.attributes.get("type") or "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if media_type != "application/ld+json":
            continue
        try:
            value = json.loads(element.text_direct)
        except (TypeError, ValueError):
            continue
        values = (
            content_sha256,
            element.element_index,
            sorted(_jsonld_type_terms(value)),
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        for column, item in zip(columns, values, strict=True):
            column.append(item)


def _table_from_columns(
    schema: pa.Schema,
    columns: list[list[object]],
) -> pa.Table:
    return pa.Table.from_arrays(
        [
            pa.array(values, type=field.type)
            for field, values in zip(schema, columns, strict=True)
        ],
        schema=schema,
    )


def _table_from_rows(
    schema: pa.Schema,
    rows: list[tuple[object, ...]],
) -> pa.Table:
    columns = [
        [row[index] for row in rows]
        for index in range(len(schema))
    ]
    return _table_from_columns(schema, columns)


def _namespace_name(namespace_uri: str | None) -> str:
    return {
        None: "NONE",
        "http://www.w3.org/1999/xhtml": "HTML",
        "http://www.w3.org/2000/svg": "SVG",
        "http://www.w3.org/1998/Math/MathML": "MATHML",
    }.get(namespace_uri, namespace_uri or "NONE")


def _jsonld_type_terms(value: object) -> set[str]:
    terms: set[str] = set()
    if isinstance(value, dict):
        raw_type = value.get("@type")
        if isinstance(raw_type, str):
            terms.add(raw_type)
        elif isinstance(raw_type, list):
            terms.update(item for item in raw_type if isinstance(item, str))
        for child in value.values():
            terms.update(_jsonld_type_terms(child))
    elif isinstance(value, list):
        for child in value:
            terms.update(_jsonld_type_terms(child))
    return terms


def _relation_scope(source_url: str, target_url: str) -> str:
    source = urlsplit(normalize_url(source_url))
    target = urlsplit(normalize_url(target_url))
    if source.geturl() == target.geturl():
        return "self"
    if (
        source.scheme,
        source.hostname,
        source.port,
    ) == (
        target.scheme,
        target.hostname,
        target.port,
    ):
        return "same_origin"
    if source.hostname == target.hostname:
        return "same_host"
    source_domain = _TLD_EXTRACT(source.hostname or "").top_domain_under_public_suffix
    target_domain = _TLD_EXTRACT(target.hostname or "").top_domain_under_public_suffix
    if source_domain and source_domain == target_domain:
        return "same_site"
    return "external"


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
