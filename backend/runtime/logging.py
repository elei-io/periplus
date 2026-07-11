from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from config import get_str

_LOGGER = logging.getLogger("atlas.worker")
_LOGGER.addHandler(logging.NullHandler())
_LOGGER.propagate = False
_DEGRADED: set[str] = set()
_WORKER_ID: str | None = None


class _WorkerFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "service": "atlas-runtime-worker",
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if get_str("ATLAS_LOG_FORMAT").lower() == "json":
            return json.dumps(payload, default=str, separators=(",", ":"))
        fields = " ".join(
            f"{key}={json.dumps(value, default=str)}"
            for key, value in payload.items()
            if key not in {"timestamp", "level", "event", "service"}
        )
        prefix = f"{payload['timestamp']} {payload['level']} {payload['event']}"
        return f"{prefix} {fields}" if fields else prefix


def configure_worker_logging() -> None:
    level_name = get_str("ATLAS_LOG_LEVEL").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(_WorkerFormatter())
    _LOGGER.handlers.clear()
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(level)
    _LOGGER.propagate = False


def bind_worker_id(worker_id: str) -> None:
    global _WORKER_ID
    _WORKER_ID = worker_id


def worker_log(
    level: int,
    event: str,
    *,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    if _WORKER_ID is not None:
        fields.setdefault("worker_id", _WORKER_ID)
    _LOGGER.log(level, event, extra={"fields": fields}, exc_info=exc_info)


def dependency_unavailable(dependency: str, error: object) -> None:
    if dependency in _DEGRADED:
        return
    _DEGRADED.add(dependency)
    worker_log(logging.WARNING, f"{dependency}.unavailable", error=str(error))


def dependency_recovered(dependency: str) -> None:
    if dependency not in _DEGRADED:
        return
    _DEGRADED.remove(dependency)
    worker_log(logging.INFO, f"{dependency}.recovered")
