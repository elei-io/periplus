"""The fixed, all-or-nothing materialization schema."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
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


class LiveBatchWork(BaseModel):
    """One frozen, replayable active-generation visit batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["live"] = "live"
    batch_id: UUID
    generation_id: UUID
    ordinal: int
    snapshot: int
    visit_ids: tuple[str, ...]
