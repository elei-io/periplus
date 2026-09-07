"""Immutable causal and fulfillment evidence, separate from observation grain."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator


class LineageRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: UUID
    visibility: Literal["public", "private"]
    recorded_at: datetime


class CollectionDefinition(LineageRecord):
    kind: Literal["collection"] = "collection"
    collection_id: UUID
    specification: dict[str, JsonValue]

    @model_validator(mode="after")
    def identity(self):
        if self.record_id != self.collection_id:
            raise ValueError("collection definition identity must equal collection ID")
        return self


class DiscoveryProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str | None = None
    queries: tuple[str, ...] = Field(default=(), max_length=3)


class SeedProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_snapshot: str | None = None
    source_query_id: str | None = None
    selected_at: datetime | None = None
    discovery: DiscoveryProvenance | None = None
    candidates_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CollectionOutcome(LineageRecord):
    kind: Literal["collection_outcome"] = "collection_outcome"
    collection_id: UUID
    outcome: Literal["budget_reached", "eligible_links_exhausted", "deadline", "cancelled", "failed"]
    seed_provenance: SeedProvenance | None = None
    consumed_pages: int = Field(ge=0)
    supplied_pages: int = Field(ge=0)
    failed_pages: int = Field(ge=0)

    @model_validator(mode="after")
    def counts(self):
        if self.supplied_pages + self.failed_pages > self.consumed_pages:
            raise ValueError("page outcomes exceed consumed pages")
        if self.record_id != self.collection_id:
            raise ValueError("collection outcome identity must equal collection ID")
        return self


class FulfillmentRecord(LineageRecord):
    kind: Literal["fulfillment"] = "fulfillment"
    collection_id: UUID
    observation_id: UUID
    requested_url: str
    parent_observation_id: UUID | None = None
    depth: int = Field(ge=0)
    rule_id: str
    mode: Literal["acquired", "shared", "reused"]


class BackgroundSelectionProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    policy_version: int = Field(ge=1)
    source_snapshot: int = Field(ge=0)
    source_query_id: str = Field(min_length=1, max_length=200)


class AcquisitionReason(LineageRecord):
    kind: Literal["acquisition_reason"] = "acquisition_reason"
    observation_id: UUID
    collection_id: UUID | None = None
    parent_observation_id: UUID | None = None
    reason: Literal["collection", "background"]
    selection_provenance: BackgroundSelectionProvenance | None = None
    policy_version: str
    rule_id: str

    @model_validator(mode="after")
    def source(self):
        if (self.reason == "collection") != (self.collection_id is not None):
            raise ValueError("only collection reasons require a collection ID")
        if self.reason == "background" and self.visibility != "public":
            raise ValueError("background evidence must be public")
        if (self.reason == "background") != (self.selection_provenance is not None):
            raise ValueError("only background reasons require historical selection provenance")
        return self


LineageEvidence = Annotated[
    CollectionDefinition | CollectionOutcome | FulfillmentRecord | AcquisitionReason,
    Field(discriminator="kind"),
]
LINEAGE_ADAPTER = TypeAdapter(LineageEvidence)
