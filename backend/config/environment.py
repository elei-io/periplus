"""Single environment loading, defaulting, and validation boundary for Atlas."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

PROJECT_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_REPOSITORY_ROOT: Final = PROJECT_ROOT / ".atlas" / "repository"

# Process-provided values win. The root file exists only for local development.
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULTS: Final[dict[str, str | None]] = {
    "ATLAS_API_URL": None,
    "DATABASE_URL": "postgresql://atlas:atlas@127.0.0.1:5432/atlas",
    "ATLAS_CATALOGUE_CATALOG": "postgres",
    "ATLAS_CATALOGUE_CATALOG_PATH": None,
    "ATLAS_CATALOGUE_CATALOG_DSN": "host=127.0.0.1 port=5432 dbname=atlas_catalogue user=atlas password=atlas",
    "ATLAS_CATALOGUE_ROOT": str(DEFAULT_REPOSITORY_ROOT),
    "ATLAS_CATALOGUE_ALIAS": "atlas",
    "ATLAS_CATALOGUE_SCHEMA": "main",
    "ATLAS_CATALOGUE_DUCKDB_DATABASE": ":memory:",
    "ATLAS_CATALOGUE_DUCKDB_MAX_TEMP_SIZE": None,
    "ATLAS_CATALOGUE_DUCKDB_TEMP_DIRECTORY": None,
    "ATLAS_CATALOGUE_OVERRIDE_DATA_PATH": None,
    "ATLAS_CATALOGUE_DATA_INLINING_ROW_LIMIT": "0",
    "ATLAS_QUACK_URI": None,
    "ATLAS_QUACK_TOKEN": None,
    "ATLAS_QUACK_DISABLE_SSL": "false",
    "ATLAS_QUACK_MAX_CONCURRENCY": "4",
    "ATLAS_QUACK_POOL_WAIT_SECONDS": "5",
    "ATLAS_QUACK_QUERY_TIMEOUT_SECONDS": "30",
    "ATLAS_QUACK_QUERY_MAX_ROWS": "100000",
    "ATLAS_QUACK_QUERY_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_QUACK_QUERY_STATE_TTL_SECONDS": "3600",
    "ATLAS_QUACK_QUERY_STATE_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_SEARCH_MODEL": "openai:gpt-5.6-luna",
    "ATLAS_SEARCH_MODEL_TIMEOUT_SECONDS": "60",
    "ATLAS_SEARCH_MODEL_REQUEST_LIMIT": "32",
    "ATLAS_SEARCH_TOOL_CALL_LIMIT": "24",
    "BRAVE_SEARCH_API_KEY": None,
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
    "ATLAS_REPOSITORY_STAGING_ROOT": None,
    "AWS_REGION": None,
    "AWS_ACCESS_KEY_ID": None,
    "AWS_SECRET_ACCESS_KEY": None,
    "AWS_SESSION_TOKEN": None,
    "ATLAS_REPOSITORY_MAX_HTML_BYTES": str(64 * 1024 * 1024),
    "ATLAS_REPOSITORY_MAX_ARTIFACT_BYTES": str(256 * 1024 * 1024),
    "ATLAS_REPOSITORY_MAX_DOCUMENT_ELEMENTS": "1000000",
    "ATLAS_REPOSITORY_MAX_DOCUMENT_STAGED_BYTES": str(256 * 1024 * 1024),
    "NATS_URL": "nats://127.0.0.1:4222",
    "NATS_SEED": None,
    "ATLAS_NATS_MAX_ENVELOPE_BYTES": str(900 * 1024),
    "ATLAS_INGEST_MAX_DELIVER": "5",
    "ATLAS_INGEST_RESULT_POLL_SECONDS": "0.5",
    "ATLAS_INGEST_RESULT_TTL_SECONDS": str(7 * 24 * 60 * 60),
    "ATLAS_INGEST_RESULT_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_INGEST_RESULT_REPLICAS": "1",
    "ATLAS_CATALOGUE_WORK_STREAM_REPLICAS": "1",
    "ATLAS_CATALOGUE_WORK_MAX_BYTES": str(1024 * 1024 * 1024),
    "ATLAS_CATALOGUE_EVENT_STREAM_REPLICAS": "1",
    "ATLAS_CATALOGUE_EVENT_MAX_BYTES": str(4 * 1024 * 1024 * 1024),
    "ATLAS_CATALOGUE_EVENT_TTL_SECONDS": str(30 * 24 * 60 * 60),
    "ATLAS_DEAD_LETTER_TTL_SECONDS": str(30 * 24 * 60 * 60),
    "ATLAS_DEAD_LETTER_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_INGEST_STAGING_GRACE_SECONDS": "3600",
    "ATLAS_REPOSITORY_COMPACTION_INTERVAL_SECONDS": "300",
    "ATLAS_REPOSITORY_COMPACTION_DEBOUNCE_SECONDS": "15",
    "ATLAS_REPOSITORY_COMPACTION_MAX_DELAY_SECONDS": "120",
    "ATLAS_REPOSITORY_COMPACTION_RETRY_SECONDS": "5",
    "ATLAS_REPOSITORY_COMPACTION_MIN_FILES": "64",
    "ATLAS_REPOSITORY_COMPACTION_MAX_TABLES_PER_PASS": "1",
    "ATLAS_REPOSITORY_COMPACTION_MAX_INPUT_FILE_BYTES": str(1024 * 1024),
    "ATLAS_REPOSITORY_COMPACTION_TARGET_FILE_BYTES": str(32 * 1024 * 1024),
    "ATLAS_REPOSITORY_COMPACTION_MAX_OUTPUT_FILES": "4",
    "ATLAS_REPOSITORY_COMPACTION_MAX_OPERATION_BYTES": str(256 * 1024 * 1024),
    "ATLAS_REPOSITORY_CLEANUP_OLD_FILES_SECONDS": str(7 * 24 * 60 * 60),
    "ATLAS_ACQUISITION_WORKER_ID": None,
    "CDP_URL": "http://127.0.0.1:9222",
    "ATLAS_GRAPH_MAX_RUN_SECONDS": str(7 * 24 * 60 * 60),
    "ATLAS_GRAPH_STREAM_REPLICAS": "1",
    "ATLAS_GRAPH_WORK_MAX_BYTES": str(8 * 1024 * 1024 * 1024),
    "ATLAS_CRAWL_MAX_DELIVER": "5",
    "ATLAS_GRAPH_STATE_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_GRAPH_WORKER_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_GRAPH_RUN_TTL_SECONDS": str(30 * 24 * 60 * 60),
    "ATLAS_CRAWL_REQUEST_TTL_SECONDS": str(30 * 24 * 60 * 60),
    "ATLAS_GRAPH_PROGRESS_TTL_SECONDS": str(30 * 24 * 60 * 60),
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
    "ATLAS_MATERIALIZATION_CONTROL_POLL_SECONDS": "1",
    "ATLAS_MATERIALIZATION_REFRESH_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_CATALOGUE_RELAY_RECONCILE_SECONDS": "5",
    "ATLAS_DOMAIN_PACING_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_CATALOGUE_WORKER_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_OPERATION_LEASE_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_RESOURCE_GRANT_MAX_BYTES": str(64 * 1024 * 1024),
    "ATLAS_CATALOGUE_MAX_CONCURRENCY": "64",
    "ATLAS_OBJECT_IO_MAX_CONCURRENCY": "64",
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
    "ATLAS_MATERIALIZATION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS": "10",
    "ATLAS_MATERIALIZATION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS": "2",
    "ATLAS_CATALOGUE_RELAY_WORKER_METRICS_PORT": "9097",
    "ATLAS_CATALOGUE_RELAY_WORKER_HEALTH_HOST": "0.0.0.0",
    "ATLAS_CATALOGUE_RELAY_WORKER_HEALTH_PORT": "9098",
    "ATLAS_CATALOGUE_RELAY_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
    "ATLAS_MAINTENANCE_WORKER_METRICS_PORT": "9094",
    "ATLAS_MAINTENANCE_WORKER_HEALTH_HOST": "0.0.0.0",
    "ATLAS_MAINTENANCE_WORKER_HEALTH_PORT": "9095",
    "ATLAS_MAINTENANCE_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
    "ATLAS_DUCKLAKE_CDC_VERSION": "ducklake_cdc 0.6.1",
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


def get_float(name: str, *, minimum: float | None = 0.0, exclusive: bool = True) -> float:
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
