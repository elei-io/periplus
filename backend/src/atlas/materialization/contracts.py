"""Public contracts shared by fixed materialization workloads."""

from __future__ import annotations

from typing import Literal

from atlas.platform.catalogue.schema import (
    CONTENT_STATS,
    HTML_ELEMENTS,
    JSONLD_VALUES,
    LINK_OCCURRENCES,
    LINKS,
    PAGE_HEADS,
    PAGE_OBSERVATIONS,
    PAGES,
    RelationName,
)

ProjectionName = Literal[
    "content_stats",
    "html_elements",
    "jsonld_values",
    "links",
    "link_occurrences",
    "pages",
    "page_observations",
    "page_heads",
]

DOCUMENT_PROJECTIONS: tuple[ProjectionName, ...] = (
    "content_stats",
    "html_elements",
    "jsonld_values",
    "links",
    "link_occurrences",
)
VISIT_PROJECTIONS: tuple[ProjectionName, ...] = (
    "pages",
    "page_observations",
    "page_heads",
)
PROJECTION_ORDER = (*DOCUMENT_PROJECTIONS, *VISIT_PROJECTIONS)

RELATIONS: dict[ProjectionName, RelationName] = {
    "content_stats": CONTENT_STATS,
    "html_elements": HTML_ELEMENTS,
    "jsonld_values": JSONLD_VALUES,
    "links": LINKS,
    "link_occurrences": LINK_OCCURRENCES,
    "pages": PAGES,
    "page_observations": PAGE_OBSERVATIONS,
    "page_heads": PAGE_HEADS,
}

PROJECTOR_VERSIONS: dict[ProjectionName, int] = {
    "content_stats": 1,
    "html_elements": 1,
    "jsonld_values": 1,
    "links": 2,
    "link_occurrences": 1,
    "pages": 1,
    "page_observations": 1,
    "page_heads": 1,
}


def ordered_projections(
    requested: set[ProjectionName],
) -> tuple[ProjectionName, ...]:
    expanded = set(requested)
    if expanded & {"page_observations", "page_heads"}:
        expanded.update({"page_observations", "page_heads"})
    if expanded & {"links", "link_occurrences"}:
        expanded.update({"links", "link_occurrences"})
    return tuple(
        projection
        for projection in PROJECTION_ORDER
        if projection in expanded
    )


def workload_projections(
    stages: tuple[ProjectionName, ...],
    active_stage: ProjectionName,
) -> tuple[ProjectionName, ...]:
    group = (
        DOCUMENT_PROJECTIONS
        if active_stage in DOCUMENT_PROJECTIONS
        else VISIT_PROJECTIONS
    )
    return tuple(stage for stage in group if stage in stages)
