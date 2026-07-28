"""Public contracts shared by fixed materialization workloads."""

from __future__ import annotations

from typing import Literal

from atlas.platform.catalogue.schema import (
    HTML_DOCUMENTS,
    HTML_ELEMENTS,
    JSONLD_VALUES,
    LINK_OBSERVATIONS,
    LINKS,
    PAGE_OBSERVATIONS,
    PAGES,
    RelationName,
)

ProjectionName = Literal[
    "html_documents",
    "html_elements",
    "jsonld_values",
    "links",
    "link_observations",
    "pages",
    "page_observations",
]

DOCUMENT_PROJECTIONS: tuple[ProjectionName, ...] = (
    "html_documents",
    "html_elements",
    "jsonld_values",
    "links",
    "link_observations",
)
VISIT_PROJECTIONS: tuple[ProjectionName, ...] = (
    "pages",
    "page_observations",
)
PROJECTION_ORDER = (*DOCUMENT_PROJECTIONS, *VISIT_PROJECTIONS)

RELATIONS: dict[ProjectionName, RelationName] = {
    "html_documents": HTML_DOCUMENTS,
    "html_elements": HTML_ELEMENTS,
    "jsonld_values": JSONLD_VALUES,
    "links": LINKS,
    "link_observations": LINK_OBSERVATIONS,
    "pages": PAGES,
    "page_observations": PAGE_OBSERVATIONS,
}

PROJECTOR_VERSIONS: dict[ProjectionName, int] = {
    "html_documents": 1,
    "html_elements": 1,
    "jsonld_values": 1,
    "links": 2,
    "link_observations": 1,
    "pages": 1,
    "page_observations": 1,
}


def ordered_projections(
    requested: set[ProjectionName],
) -> tuple[ProjectionName, ...]:
    return tuple(
        projection
        for projection in PROJECTION_ORDER
        if projection in requested
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
