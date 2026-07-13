"""Cross-process fencing for off-path catalogue maintenance."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from config import get_float, get_int
from nats.js.api import KeyValueConfig, StorageType
from nats.js.errors import BadRequestError, BucketNotFoundError
from pydantic import BaseModel, ConfigDict

MAINTENANCE_LEASE_BUCKET = "atlas_catalog_maintenance"

MaintenanceKind = Literal["compact", "cleanup"]


class MaintenanceLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str
    operation_id: str
    kind: MaintenanceKind
    acquired_at: datetime
    heartbeat_at: datetime


async def ensure_maintenance_storage(jetstream):
    """Return the only distributed state maintenance requires: its global lease."""

    try:
        return await jetstream.key_value(MAINTENANCE_LEASE_BUCKET)
    except BucketNotFoundError:
        config = KeyValueConfig(
            bucket=MAINTENANCE_LEASE_BUCKET,
            description="Global DuckLake maintenance lease",
            history=1,
            ttl=get_float("ATLAS_MAINTENANCE_LEASE_SECONDS"),
            storage=StorageType.FILE,
            replicas=get_int("ATLAS_MAINTENANCE_LEASE_REPLICAS"),
        )
        try:
            return await jetstream.create_key_value(config=config)
        except BadRequestError:
            return await jetstream.key_value(MAINTENANCE_LEASE_BUCKET)
