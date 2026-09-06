"""Append-only parsed JSON-LD projection."""

from __future__ import annotations

import json

import pyarrow as pa

from periplus.materialization.document_projection import (
    VisitBatchContext,
    table_from_rows,
)
from periplus.materialization.registry import (
    PartitionTransform,
    ProjectionColumn,
    ProjectionSpec,
)


def _type_terms(value: object) -> set[str]:
    terms: set[str] = set()
    if isinstance(value, dict):
        raw_type = value.get("@type")
        if isinstance(raw_type, str):
            terms.add(raw_type)
        elif isinstance(raw_type, list):
            terms.update(item for item in raw_type if isinstance(item, str))
        for child in value.values():
            terms.update(_type_terms(child))
    elif isinstance(value, list):
        for child in value:
            terms.update(_type_terms(child))
    return terms


def project(context: VisitBatchContext) -> pa.Table:
    rows: list[tuple[object, ...]] = []
    for content_sha256 in sorted(context.content_output_hashes):
        for element in context.parsed_elements_by_content.get(
            content_sha256,
            (),
        ):
            media_type = (
                str(element.attributes.get("type") or "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if element.tag.lower() != "script" or (
                media_type != "application/ld+json"
            ):
                continue
            try:
                value = json.loads(element.text_direct)
            except (TypeError, ValueError):
                continue
            rows.append(
                (
                    content_sha256,
                    element.element_index,
                    sorted(_type_terms(value)),
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
    return table_from_rows(PROJECTION.arrow_schema, rows)


PROJECTION = ProjectionSpec(
    name="jsonld_values",
    ownership_grain="content",
    columns=(
        ProjectionColumn(
            "content_sha256", pa.string(), "VARCHAR",
            "Identity of the containing immutable HTML bytes.", False,
        ),
        ProjectionColumn(
            "element_index", pa.int32(), "INTEGER",
            "Source script element in the immutable HTML.", False,
        ),
        ProjectionColumn(
            "type_terms", pa.list_(pa.string()), "VARCHAR[]",
            "Distinct raw @type strings found in the payload.", False,
        ),
        ProjectionColumn(
            "value", pa.string(), "JSON",
            "Complete parsed JSON-LD payload.", False,
        ),
    ),
    partitioning=(PartitionTransform("bucket", "content_sha256", buckets=8),),
    sort_order=("content_sha256 ASC", "element_index ASC"),
    projector=project,
    description="Append-only parsed JSON-LD embedded in immutable HTML content.",
    identity_columns=("content_sha256", "element_index"),
)
