from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from .models import WorkerHeartbeat


def _env_seconds(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def worker_heartbeat_retention_seconds() -> int:
    return _env_seconds("ATLAS_WORKER_HEARTBEAT_RETENTION_SECONDS", 24 * 60 * 60)


def worker_heartbeat_cleanup_interval_seconds() -> int:
    return _env_seconds("ATLAS_WORKER_HEARTBEAT_CLEANUP_INTERVAL_SECONDS", 60 * 60)


def worker_heartbeat_stale_after_seconds() -> float:
    try:
        heartbeat_seconds = max(
            1.0, float(os.getenv("ATLAS_WORKER_HEARTBEAT_SECONDS", "10"))
        )
    except ValueError:
        heartbeat_seconds = 10.0
    return heartbeat_seconds * 2


def cleanup_expired_worker_heartbeats(
    session: Session,
    *,
    now: datetime | None = None,
    retention_seconds: int | None = None,
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(
        seconds=retention_seconds or worker_heartbeat_retention_seconds()
    )
    result = session.execute(
        delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen_at < cutoff)
    )
    return int(result.rowcount or 0)


def purge_stale_worker_heartbeats(
    session: Session,
    *,
    now: datetime | None = None,
    stale_after_seconds: float | None = None,
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(
        seconds=(
            stale_after_seconds
            if stale_after_seconds is not None
            else worker_heartbeat_stale_after_seconds()
        )
    )
    result = session.execute(
        delete(WorkerHeartbeat).where(
            or_(
                WorkerHeartbeat.stopping.is_(True),
                WorkerHeartbeat.last_seen_at < cutoff,
            )
        )
    )
    return int(result.rowcount or 0)
