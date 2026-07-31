"""Append-only resolved link observations."""

from __future__ import annotations

from uuid import UUID

import pyarrow as pa

from atlas.materialization.document_projection import (
    VisitBatchContext,
    table_from_rows,
)
from atlas.materialization.dom import links_from_elements
from atlas.materialization.registry import (
    PartitionTransform,
    ProjectionColumn,
    ProjectionSpec,
)
from atlas.platform.catalogue.records import link_id_for, link_occurrence_id_for

_RELATION_SCOPES = {
    "same_url": "self",
    "same_path": "same_origin",
    "same_origin": "same_origin",
    "same_host": "same_host",
    "same_site": "same_site",
    "external": "external",
}


def project(context: VisitBatchContext) -> pa.Table:
    rows: dict[tuple[str, int], tuple[object, ...]] = {}
    for source in context.sources:
        elements = context.parsed_elements_by_content[source.content_sha256]
        by_source_url: dict[str, dict[str, list[dict[str, object]]]] = {}
        for observation in context.observations_by_content[
            source.content_sha256
        ]:
            grouped = by_source_url.get(observation.source_url)
            if grouped is None:
                grouped = links_from_elements(
                    elements,
                    page_url=observation.source_url,
                )
                by_source_url[observation.source_url] = grouped
            for link in (*grouped["internal"], *grouped["external"]):
                source_url = str(link["source_url"])
                target_url = str(link["target_url"])
                element_index = int(link["element_index"])
                rows[(observation.document_id, element_index)] = (
                    str(
                        link_occurrence_id_for(
                            UUID(observation.document_id),
                            element_index,
                        )
                    ),
                    str(link_id_for(source_url, target_url)),
                    observation.visit_id,
                    observation.document_id,
                    source.content_sha256,
                    element_index,
                    observation.observed_at,
                    str(link["raw_href"]),
                    source_url,
                    target_url,
                    _RELATION_SCOPES[str(link["relation_kind"])],
                )
    return table_from_rows(
        PROJECTION.arrow_schema,
        [rows[key] for key in sorted(rows)],
    )


PROJECTION = ProjectionSpec(
    name="link_occurrences",
    ownership_grain="visit",
    columns=(
        ProjectionColumn(
            "occurrence_id", pa.string(), "UUID",
            "Stable identity of this document element occurrence.", False,
        ),
        ProjectionColumn(
            "link_id", pa.string(), "UUID",
            "Deterministic convenience identity of the directed URL pair.", False,
        ),
        ProjectionColumn(
            "visit_id", pa.string(), "UUID",
            "Visit during which this occurrence was observed.", False,
        ),
        ProjectionColumn(
            "document_id", pa.string(), "UUID",
            "Document observation that owns this occurrence.", False,
        ),
        ProjectionColumn(
            "content_sha256", pa.string(), "VARCHAR",
            "Immutable HTML content containing the source anchor.", False,
        ),
        ProjectionColumn(
            "element_index", pa.int32(), "INTEGER",
            "Source anchor position in the immutable HTML.", False,
        ),
        ProjectionColumn(
            "observed_at", pa.timestamp("us", tz="UTC"), "TIMESTAMPTZ",
            "Time the containing document representation was captured.", False,
        ),
        ProjectionColumn(
            "raw_href", pa.string(), "VARCHAR",
            "Exact non-empty href observed on the source anchor.", False,
        ),
        ProjectionColumn(
            "source_url", pa.string(), "VARCHAR",
            "Normalized URL where the link was observed.", False,
        ),
        ProjectionColumn(
            "target_url", pa.string(), "VARCHAR",
            "Normalized URL resolved from raw_href.", False,
        ),
        ProjectionColumn(
            "relation_scope", pa.string(), "VARCHAR",
            "Most-specific deterministic URL relationship.", False,
        ),
    ),
    partitioning=(PartitionTransform("month", "observed_at"),),
    sort_order=(
        "source_url ASC",
        "target_url ASC",
        "observed_at ASC",
        "occurrence_id ASC",
    ),
    projector=project,
    description=(
        "Append-only, visit-owned HTML link observations with resolved identity."
    ),
    identity_columns=("occurrence_id",),
    validation_queries=(
        """
        SELECT count(*)
        FROM material.link_occurrences AS occurrence
        LEFT JOIN ingest.visits AS visit USING (visit_id)
        LEFT JOIN ingest.documents AS document
          ON document.document_id = occurrence.document_id
         AND document.visit_id = occurrence.visit_id
         AND document.content_sha256 = occurrence.content_sha256
        WHERE visit.visit_id IS NULL
           OR document.document_id IS NULL
        """,
    ),
)
