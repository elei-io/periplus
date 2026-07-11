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
    "ATLAS_CATALOGUE_DUCKDB_THREADS": None,
    "ATLAS_CATALOGUE_DUCKDB_MEMORY_LIMIT": None,
    "ATLAS_CATALOGUE_DUCKDB_MAX_TEMP_SIZE": None,
    "ATLAS_CATALOGUE_DUCKDB_TEMP_DIRECTORY": None,
    "ATLAS_CATALOGUE_OVERRIDE_DATA_PATH": None,
    "ATLAS_CATALOGUE_DATA_INLINING_ROW_LIMIT": "0",
    "ATLAS_CATALOGUE_READ_POOL_SIZE": "2",
    "ATLAS_CATALOGUE_READ_THREADS": "2",
    "ATLAS_CATALOGUE_READ_POOL_WAIT_SECONDS": "5",
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
    "ATLAS_REPOSITORY_MAX_DOCUMENT_ELEMENTS": "1000000",
    "ATLAS_REPOSITORY_MAX_DOCUMENT_STAGED_BYTES": str(256 * 1024 * 1024),
    "NATS_URL": "nats://127.0.0.1:4222",
    "NATS_RETENTION": "5m",
    "ATLAS_NATS_MAX_ENVELOPE_BYTES": str(900 * 1024),
    "ATLAS_INGEST_BATCH_ITEMS": "100",
    "ATLAS_INGEST_BATCH_ELEMENT_ROWS": "250000",
    "ATLAS_INGEST_BATCH_BYTES": str(256 * 1024 * 1024),
    "ATLAS_INGEST_BATCH_WAIT_SECONDS": "0.5",
    "ATLAS_INGEST_ACK_WAIT_SECONDS": "600",
    "ATLAS_INGEST_MAX_ACK_PENDING": "1000",
    "ATLAS_INGEST_MAX_DELIVER": "5",
    "ATLAS_INGEST_STREAM_REPLICAS": "1",
    "ATLAS_INGEST_RESULT_POLL_SECONDS": "0.5",
    "ATLAS_INGEST_RESULT_TTL_SECONDS": str(7 * 24 * 60 * 60),
    "ATLAS_INGEST_RESULT_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_INGEST_RESULT_REPLICAS": "1",
    "ATLAS_INGEST_STAGING_CLEANUP_INTERVAL_SECONDS": "900",
    "ATLAS_INGEST_STAGING_GRACE_SECONDS": "3600",
    "ATLAS_REPOSITORY_COMPACTION_ENABLED": "true",
    "ATLAS_REPOSITORY_COMPACTION_INTERVAL_SECONDS": "300",
    "ATLAS_REPOSITORY_COMPACTION_MIN_FILES": "64",
    "ATLAS_REPOSITORY_COMPACTION_MAX_INPUT_FILE_BYTES": str(1024 * 1024),
    "ATLAS_REPOSITORY_COMPACTION_TARGET_FILE_BYTES": str(32 * 1024 * 1024),
    "ATLAS_REPOSITORY_COMPACTION_MAX_OUTPUT_FILES": "4",
    "ATLAS_RUNTIME_WORKER_CONCURRENCY": "4",
    "ATLAS_RUNTIME_WORKER_ID": None,
    "ATLAS_RUNTIME_WORKER_POLL_SECONDS": "5",
    "ATLAS_TASK_RUN_TIMEOUT_SECONDS": "1800",
    "ATLAS_TASK_RESULT_MAX_BYTES": str(5 * 1024 * 1024),
    "ATLAS_SCHEDULER_BATCH_SIZE": "20",
    "ATLAS_TASK_STREAM_REPLICAS": "1",
    "ATLAS_TASK_ACK_WAIT_SECONDS": "60",
    "ATLAS_TASK_RUN_STATE_MAX_BYTES": str(256 * 1024 * 1024),
    "ATLAS_RUNTIME_WORKER_PRESENCE_TTL_SECONDS": "30",
    "ATLAS_CRAWL_CONCURRENCY_PER_RUN": "3",
    "ATLAS_BROWSER_CONCURRENCY": "12",
    "ATLAS_CRAWL_PERMIT_TIMEOUT_SECONDS": "120",
    "ATLAS_CACHE_MAX_AGE_SECONDS": "120",
    "ATLAS_CACHE_STALE_IF_ERROR_SECONDS": None,
    "ATLAS_INDEX_PAGE_BATCH_SIZE": "32",
    "ATLAS_INDEX_RESULT_LIMIT": "10000",
    "ATLAS_EXTRACT_MAX_ATTEMPTS": "2",
    "ATLAS_LOG_FORMAT": "text",
    "ATLAS_LOG_LEVEL": "INFO",
    "ATLAS_METRICS_ENABLED": "true",
    "ATLAS_METRICS_HOST": "0.0.0.0",
    "ATLAS_RUNTIME_WORKER_METRICS_PORT": "9090",
    "ATLAS_REPOSITORY_WORKER_METRICS_PORT": "9091",
    "ATLAS_REPOSITORY_WORKER_HEALTH_HOST": "0.0.0.0",
    "ATLAS_REPOSITORY_WORKER_HEALTH_PORT": "9092",
    "ATLAS_REPOSITORY_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
    "ATLAS_REPOSITORY_WORKER_HEALTH_PROBE_INTERVAL_SECONDS": "10",
    "ATLAS_REPOSITORY_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS": "2",
    "LLM_PROVIDER": "openai/gpt-5.5",
    "OPENROUTER_SCHEMA_MODEL": "openai/gpt-5.5",
    "OPENROUTER_SEARCH_EXTRACTOR_MODEL": "openai/gpt-5.5",
    "OPENROUTER_QUERY_PARAM_MODEL": "openai/gpt-5.5",
    "OPENROUTER_API_KEY": None,
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
