"""Durable DuckLake DML and DDL event topology."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from config import get_float, get_int
from nats.js.api import (
    DiscardPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import BadRequestError, NotFoundError
from pydantic import BaseModel, ConfigDict


EVENT_STREAM = "ATLAS_CATALOGUE_EVENTS"
DML_SUBJECT_PREFIX = "atlas.catalogue.dml"
DML_ALL_SUBJECT = f"{DML_SUBJECT_PREFIX}.*"
DDL_SUBJECT = "atlas.catalogue.ddl"
EVENT_SUBJECTS = (DML_ALL_SUBJECT, DDL_SUBJECT)
MATERIALIZATION_DURABLE_PREFIX = "atlas-materialization-"
DDL_RECONCILER_DURABLE = "atlas-materialization-ddl-reconciler"


class CatalogueDMLTick(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    table_id: int
    table_uuid: UUID
    schema_name: str
    table_name: str
    snapshot_id: int
    snapshot_time: datetime | None
    schema_version: int

    @property
    def message_id(self) -> str:
        return f"dml:{self.table_uuid}:{self.snapshot_id}"


class CatalogueDDLEvent(BaseModel):
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


def materialization_durable(materialization_id: UUID) -> str:
    return f"{MATERIALIZATION_DURABLE_PREFIX}{materialization_id.hex}"


def basin_ddl_subject(lake: str) -> str:
    return f"basin.cdc.{lake}.lake.ddl"


def basin_dml_subject(lake: str) -> str:
    return f"basin.cdc.{lake}.lake.dml_ticks"


def basin_ddl_durable(lake: str) -> str:
    return f"atlas-basin-{lake}-ddl"


def basin_dml_durable(lake: str) -> str:
    return f"atlas-basin-{lake}-dml"


async def ensure_catalogue_event_stream(jetstream) -> None:
    expected = StreamConfig(
        name=EVENT_STREAM,
        subjects=list(EVENT_SUBJECTS),
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        num_replicas=get_int("ATLAS_CATALOGUE_EVENT_STREAM_REPLICAS"),
        max_age=get_float("ATLAS_CATALOGUE_EVENT_TTL_SECONDS"),
        max_bytes=get_int("ATLAS_CATALOGUE_EVENT_MAX_BYTES"),
        discard=DiscardPolicy.OLD,
    )
    try:
        info = await jetstream.stream_info(EVENT_STREAM)
    except NotFoundError:
        try:
            await jetstream.add_stream(config=expected)
        except BadRequestError:
            # Worker roles start independently and may race to establish the
            # one shared stream. The loser attaches and validates below.
            info = await jetstream.stream_info(EVENT_STREAM)
        else:
            return
    actual = info.config
    mismatches: list[str] = []
    if set(actual.subjects) != set(expected.subjects):
        mismatches.append(f"subjects={list(expected.subjects)}")
    if actual.retention != expected.retention:
        mismatches.append("limits retention")
    if actual.storage != expected.storage:
        mismatches.append("file storage")
    if actual.num_replicas != expected.num_replicas:
        mismatches.append(f"replicas={expected.num_replicas}")
    if actual.max_age != expected.max_age:
        mismatches.append(f"max_age={expected.max_age:g}s")
    if actual.max_bytes != expected.max_bytes:
        mismatches.append(f"max_bytes={expected.max_bytes}")
    if actual.discard != expected.discard:
        mismatches.append("discard old")
    if mismatches:
        raise RuntimeError(
            f"JetStream {EVENT_STREAM} must use " + ", ".join(mismatches)
        )
