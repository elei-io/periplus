"""Single environment loading, defaulting, and validation boundary for Atlas."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

PROJECT_ROOT: Final = Path(__file__).resolve().parents[5]
DEFAULT_REPOSITORY_ROOT: Final = PROJECT_ROOT / ".atlas" / "repository"

# Process-provided values win. The root file exists only for local development.
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULTS: Final[dict[str, str | None]] = {
    "ATLAS_API_URL": None,
    "DATABASE_URL": None,
    "ATLAS_POSTGRES_POOL_SIZE": "8",
    "ATLAS_POSTGRES_POOL_TIMEOUT_SECONDS": "10",
    "DUCKBASIN_URL": None,
    "DUCKBASIN_LAKE": None,
    "DUCKBASIN_TOKEN_ENDPOINT": None,
    "DUCKBASIN_CLIENT_ID": None,
    "DUCKBASIN_CLIENT_SECRET": None,
    "DUCKBASIN_REQUEST_TIMEOUT_SECONDS": "15",
    "DUCKBASIN_TOKEN_REFRESH_SECONDS": "60",
    "DUCKBASIN_NATS_URL": None,
    "DUCKBASIN_NATS_SEED": None,
    "ATLAS_REPOSITORY_STORAGE": "disk",
    "ATLAS_REPOSITORY_ROOT": str(DEFAULT_REPOSITORY_ROOT),
    "ATLAS_REPOSITORY_S3_PREFIX": "",
    "ATLAS_REPOSITORY_S3_BUCKET": None,
    "ATLAS_REPOSITORY_S3_ENDPOINT": None,
    "ATLAS_REPOSITORY_S3_REGION": None,
    "ATLAS_REPOSITORY_S3_KEY_ID": None,
    "ATLAS_REPOSITORY_S3_SECRET_ACCESS_KEY": None,
    "ATLAS_REPOSITORY_S3_SESSION_TOKEN": None,
    "ATLAS_REPOSITORY_S3_URL_STYLE": None,
    "ATLAS_REPOSITORY_S3_USE_SSL": None,
    "AWS_REGION": None,
    "AWS_ACCESS_KEY_ID": None,
    "AWS_SECRET_ACCESS_KEY": None,
    "AWS_SESSION_TOKEN": None,
    "ATLAS_REPOSITORY_MAX_HTML_BYTES": str(64 * 1024 * 1024),
    "ATLAS_NATS_URL": None,
    "ATLAS_NATS_SEED": None,
    "ATLAS_NATS_MAX_ENVELOPE_BYTES": str(900 * 1024),
    "ATLAS_INGEST_MAX_DELIVER": "5",
    "ATLAS_INGEST_RESULT_POLL_SECONDS": "0.5",
    "ATLAS_INGEST_RESULT_TTL_SECONDS": str(7 * 24 * 60 * 60),
    "ATLAS_INGEST_RESULT_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_INGEST_RESULT_REPLICAS": "1",
    "ATLAS_CATALOGUE_WORK_STREAM_REPLICAS": "1",
    "ATLAS_CATALOGUE_WORK_MAX_BYTES": str(1024 * 1024 * 1024),
    "ATLAS_CDC_STREAM_REPLICAS": "1",
    "ATLAS_CDC_MAX_BYTES": str(4 * 1024 * 1024 * 1024),
    "ATLAS_CDC_TTL_SECONDS": str(30 * 24 * 60 * 60),
    "ATLAS_DEAD_LETTER_TTL_SECONDS": str(30 * 24 * 60 * 60),
    "ATLAS_DEAD_LETTER_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_HOUSEKEEPING_INTERVAL_SECONDS": "300",
    "ATLAS_ACQUISITION_WORKER_ID": None,
    "CDP_URL": "http://127.0.0.1:9222",
    "ATLAS_GRAPH_MAX_RUN_SECONDS": str(7 * 24 * 60 * 60),
    "ATLAS_GRAPH_RUN_RETENTION_SECONDS": str(30 * 24 * 60 * 60),
    "ATLAS_GRAPH_STREAM_REPLICAS": "1",
    "ATLAS_GRAPH_WORK_MAX_BYTES": str(8 * 1024 * 1024 * 1024),
    "ATLAS_CRAWL_MAX_DELIVER": "5",
    "ATLAS_GRAPH_WORKER_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_SCHEDULE_POLL_SECONDS": "5",
    "ATLAS_SCHEDULE_MISFIRE_GRACE_SECONDS": "60",
    "ATLAS_NAVIGATION_MAX_PACKAGE_BYTES": str(16 * 1024 * 1024),
    "ATLAS_NAVIGATION_CLEANUP_GRACE_SECONDS": "60",
    "ATLAS_EDGE_MAX_OUTPUT_ROWS": "100000",
    "ATLAS_EDGE_MAX_OUTPUT_BYTES": str(16 * 1024 * 1024),
    "ATLAS_EDGE_QUERY_MEMORY_LIMIT": "512MB",
    "ATLAS_EDGE_QUERY_TIMEOUT_SECONDS": "30",
    "ATLAS_ACQUISITION_WORKER_PRESENCE_TTL_SECONDS": "30",
    "ATLAS_CATALOGUE_WORKER_PRESENCE_TTL_SECONDS": "30",
    "ATLAS_DOMAIN_PACING_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_CATALOGUE_WORKER_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_OPERATION_LEASE_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_LOG_LEVEL": "INFO",
    "ATLAS_METRICS_ENABLED": "true",
    "ATLAS_METRICS_HOST": "0.0.0.0",
    "ATLAS_WORKER_QUEUE_STALL_SECONDS": "300",
    "ATLAS_ACQUISITION_WORKER_HEALTH_HOST": "0.0.0.0",
    "ATLAS_ACQUISITION_WORKER_HEALTH_PORT": "9099",
    "ATLAS_ACQUISITION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "10",
    "ATLAS_ACQUISITION_WORKER_METRICS_PORT": "9090",
    "ATLAS_INGESTION_WORKER_METRICS_PORT": "9091",
    "ATLAS_INGESTION_WORKER_HEALTH_HOST": "0.0.0.0",
    "ATLAS_INGESTION_WORKER_HEALTH_PORT": "9092",
    "ATLAS_INGESTION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
    "ATLAS_INGESTION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS": "10",
    "ATLAS_INGESTION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS": "2",
    "ATLAS_MATERIALIZATION_WORKER_METRICS_PORT": "9093",
    "ATLAS_MATERIALIZATION_WORKER_HEALTH_HOST": "0.0.0.0",
    "ATLAS_MATERIALIZATION_WORKER_HEALTH_PORT": "9096",
    "ATLAS_MATERIALIZATION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
    "ATLAS_CDC_WORKER_METRICS_PORT": "9097",
    "ATLAS_CDC_WORKER_HEALTH_HOST": "0.0.0.0",
    "ATLAS_CDC_WORKER_HEALTH_PORT": "9098",
    "ATLAS_CDC_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
    "ATLAS_HOUSEKEEPING_WORKER_METRICS_PORT": "9094",
    "ATLAS_HOUSEKEEPING_WORKER_HEALTH_HOST": "0.0.0.0",
    "ATLAS_HOUSEKEEPING_WORKER_HEALTH_PORT": "9095",
    "ATLAS_HOUSEKEEPING_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
}


class ConfigurationError(ValueError):
    """An Atlas setting is missing or invalid."""


def get_optional(name: str) -> str | None:
    _assert_known(name)
    value = os.getenv(name, DEFAULTS[name])
    return value.strip() if value is not None and value.strip() else None


def get_str(name: str) -> str:
    value = get_optional(name)
    if value is None:
        raise ConfigurationError(f"{name} is required")
    return value


def get_int(name: str, *, minimum: int | None = 1) -> int:
    raw = get_str(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def get_float(
    name: str, *, minimum: float | None = 0.0, exclusive: bool = True
) -> float:
    raw = get_str(name)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        qualifier = "greater than" if exclusive else "at least"
        raise ConfigurationError(f"{name} must be {qualifier} {minimum}")
    return value


def get_bool(name: str) -> bool:
    value = get_str(name).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")


def get_path(name: str) -> Path:
    return Path(get_str(name)).expanduser()


def _assert_known(name: str) -> None:
    if name not in DEFAULTS:
        raise ConfigurationError(f"Unknown Atlas setting: {name}")
