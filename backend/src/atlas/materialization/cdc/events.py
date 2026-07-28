"""Durable DuckLake CDC contracts and JetStream topology."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from atlas.platform.config import get_float, get_int
from nats.js.api import (
    DiscardPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from pydantic import BaseModel, ConfigDict, model_validator
from atlas.platform.messaging.topology import ensure_stream_contract

EVENT_STREAM = "ATLAS_CDC"
DML_SUBJECT_PREFIX = "atlas.cdc.dml"
DML_ALL_SUBJECT = f"{DML_SUBJECT_PREFIX}.*"
DDL_SUBJECT = "atlas.cdc.ddl"
EVENT_SUBJECTS = (DML_ALL_SUBJECT, DDL_SUBJECT)


class DMLTick(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start_snapshot: int
    end_snapshot: int
    table_id: int
    table_uuid: UUID
    schema_name: str
    table_name: str
    snapshot_id: int
    snapshot_time: datetime | None
    schema_version: int

    @model_validator(mode="after")
    def validate_snapshot_window(self) -> DMLTick:
        if not self.start_snapshot <= self.snapshot_id <= self.end_snapshot:
            raise ValueError("snapshot_id must fall within the source snapshot window")
        return self

    @property
    def message_id(self) -> str:
        return f"dml:{self.table_uuid}:{self.snapshot_id}"


class DDLEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_kind: str
    object_kind: str
    snapshot_id: int
    snapshot_time: datetime | None
    schema_id: int | None
    schema_name: str | None
    object_id: int | None
    object_name: str | None
    details: str | None

    @property
    def message_id(self) -> str:
        return (
            f"ddl:{self.snapshot_id}:{self.object_kind}:"
            f"{self.object_id}:{self.event_kind}"
        )


def dml_subject(table_uuid: UUID) -> str:
    return f"{DML_SUBJECT_PREFIX}.{table_uuid.hex}"


def basin_ddl_subject(lake: str) -> str:
    return f"basin.cdc.{lake}.lake.ddl"


def basin_dml_subject(lake: str) -> str:
    return f"basin.cdc.{lake}.lake.dml_ticks"


def basin_ddl_durable(lake: str) -> str:
    return f"atlas-basin-{lake}-ddl"


def basin_dml_durable(lake: str) -> str:
    return f"atlas-basin-{lake}-dml"


async def ensure_cdc_stream(jetstream) -> None:
    expected = StreamConfig(
        name=EVENT_STREAM,
        subjects=list(EVENT_SUBJECTS),
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        num_replicas=get_int("ATLAS_CDC_STREAM_REPLICAS"),
        max_age=get_float("ATLAS_CDC_TTL_SECONDS"),
        max_bytes=get_int("ATLAS_CDC_MAX_BYTES"),
        discard=DiscardPolicy.OLD,
    )
    await ensure_stream_contract(jetstream, expected)
