from datetime import datetime
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


MaterializationDesiredState = Literal["live", "paused", "deleting"]
MaterializationRefreshStrategy = Literal["keyed", "append", "full"]
MaterializationObservedState = Literal[
    "creating",
    "backfilling",
    "live",
    "paused",
    "deleting",
    "blocked_schema",
    "failed",
]


class ViewMaterializationPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=63)
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    source_table: str = Field(min_length=1, max_length=63)
    refresh_strategy: MaterializationRefreshStrategy
    key_columns: list[str] = Field(default_factory=list, max_length=16)
    scope_relations: dict[str, list[str]] = Field(default_factory=dict)
    refresh_delay_seconds: float = Field(default=1.0, ge=0, le=3600)
    partition_column: str | None = Field(default=None, max_length=63)

    @model_validator(mode="after")
    def validate_refresh_strategy(self) -> "ViewMaterializationPut":
        if len(set(self.key_columns)) != len(self.key_columns):
            raise ValueError("Key columns must not contain duplicates.")
        if any(
            re.fullmatch(r"[a-z][a-z0-9_]{0,62}", column) is None
            for column in self.key_columns
        ):
            raise ValueError(
                "Key columns must use lower-case letters, numbers, and underscores."
            )
        if self.refresh_strategy in {"keyed", "append"} and not self.key_columns:
            raise ValueError(
                f"The {self.refresh_strategy} refresh strategy requires key columns."
            )
        if self.refresh_strategy == "full" and self.key_columns:
            raise ValueError("The full refresh strategy does not use key columns.")
        for relation, columns in self.scope_relations.items():
            if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", relation) is None:
                raise ValueError("Scope relation names must be safe identifiers.")
            if not columns or len(columns) > 16:
                raise ValueError("Each scope relation requires 1–16 columns.")
            if any(
                re.fullmatch(r"[a-z][a-z0-9_]{0,62}", column) is None
                for column in columns
            ):
                raise ValueError("Scope columns must be safe identifiers.")
        if self.refresh_strategy == "full" and self.scope_relations:
            raise ValueError("The full refresh strategy does not use scoped relations.")
        return self


class CatalogueMaterializationStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desired_state: Literal["live", "paused"] | None = None
    refresh_delay_seconds: float | None = Field(default=None, ge=0, le=3600)

    @model_validator(mode="after")
    def require_change(self) -> "CatalogueMaterializationStateUpdate":
        if not self.model_fields_set:
            raise ValueError("Choose at least one setting to change.")
        return self


class CatalogueMaterializationSummary(BaseModel):
    id: UUID
    status: MaterializationObservedState


class CatalogueMaterializationRecord(BaseModel):
    id: UUID
    name: str
    qualified_name: str
    display_name: str
    description: str | None
    view_reference_id: UUID
    view_uuid: UUID
    view_name: str
    source_table: str
    source_table_id: int
    source_table_uuid: UUID
    control_snapshot: int
    desired_state: MaterializationDesiredState
    observed_state: MaterializationObservedState
    nats_consumer_name: str
    refresh_delay_seconds: float
    refresh_strategy: MaterializationRefreshStrategy
    key_columns: list[str]
    scope_relations: dict[str, list[str]]
    partition_column: str | None
    target_table_id: int | None
    ducklake_table_uuid: UUID | None
    bootstrap_snapshot: int | None
    bootstrap_partition_count: int | None
    bootstrap_partition_cursor: int | None
    processed_snapshot: int | None
    last_refreshed_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class CatalogueMaterializationListResponse(BaseModel):
    items: list[CatalogueMaterializationRecord]
    total: int
