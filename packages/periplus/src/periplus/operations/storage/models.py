"""Read-only storage dashboard contracts. Unknown measurements remain null."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StorageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Footprint(StorageModel):
    id: str
    name: str
    bytes: int | None = Field(default=None, ge=0)
    complete: bool = False
    basis: str
    reason: str | None = None


class Evidence(StorageModel):
    observations: int
    documents: int
    observations_with_documents: int
    unique_objects: int
    referenced_bytes: int
    unique_bytes: int
    median_bytes: float | None
    p95_bytes: float | None


class LakeTable(StorageModel):
    schema_name: str
    name: str
    role: Literal["evidence", "projection", "bookkeeping", "other"]
    generation: Literal["current", "rebuilding", "retired"]
    estimated_rows: int
    bytes: int
    data_bytes: int
    delete_bytes: int
    files: int
    delete_files: int


class DatabaseTable(StorageModel):
    name: str
    table_bytes: int
    index_bytes: int
    total_bytes: int
    estimated_rows: int


class Stream(StorageModel):
    name: str
    bytes: int | None
    messages: int | None


class RetirementStage(StorageModel):
    id: str
    name: str
    objects: int
    expected_bytes: int
    oldest_at: datetime | None


class Retention(StorageModel):
    stages: list[RetirementStage]
    retired_observations: int
    retired_requests: int
    latest_retirement_at: datetime | None
    oldest_snapshot_at: datetime | None
    snapshots: int


class StorageReport(StorageModel):
    as_of: datetime
    collected_at: datetime
    sources: list[Footprint]
    evidence: Evidence | None = None
    tables: list[LakeTable] = Field(default_factory=list)
    tables_complete: bool = False
    control_tables: list[DatabaseTable] = Field(default_factory=list)
    streams: list[Stream] = Field(default_factory=list)
    retention: Retention | None = None
    issues: list[str] = Field(default_factory=list)
