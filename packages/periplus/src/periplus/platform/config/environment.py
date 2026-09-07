"""Single environment loading, defaulting, and validation boundary for Periplus."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

PROJECT_ROOT: Final = Path(__file__).resolve().parents[6]
DEFAULT_REPOSITORY_ROOT: Final = PROJECT_ROOT / ".periplus" / "repository"

# Process-provided values win. The root file exists only for local development.
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULTS: Final[dict[str, str | None]] = {
    "PERIPLUS_API_URL": None,
    "PERIPLUS_QUERY_URL": "http://127.0.0.1:8010",
    "PERIPLUS_QUERY_API_TOKEN": None,
    "BRAVE_SEARCH_API_KEY": None,
    "OPENAI_API_KEY": None,
    "PERIPLUS_DISCOVERY_MODEL": None,
    "PERIPLUS_ADMIN_API_TOKEN": None,
    "PERIPLUS_PUBLIC_API_TOKEN": None,
    "PERIPLUS_CONTROL_DATABASE_URL": None,
    "PERIPLUS_CONTROL_POSTGRES_POOL_SIZE": "8",
    "PERIPLUS_CONTROL_POSTGRES_POOL_TIMEOUT_SECONDS": "10",
    "PERIPLUS_REPOSITORY_STORAGE": "disk",
    "PERIPLUS_REPOSITORY_ROOT": str(DEFAULT_REPOSITORY_ROOT),
    "PERIPLUS_REPOSITORY_S3_PREFIX": "",
    "PERIPLUS_REPOSITORY_S3_BUCKET": None,
    "PERIPLUS_REPOSITORY_S3_ENDPOINT": None,
    "PERIPLUS_REPOSITORY_S3_REGION": None,
    "PERIPLUS_REPOSITORY_S3_KEY_ID": None,
    "PERIPLUS_REPOSITORY_S3_SECRET_ACCESS_KEY": None,
    "PERIPLUS_REPOSITORY_S3_SESSION_TOKEN": None,
    "PERIPLUS_REPOSITORY_S3_URL_STYLE": None,
    "PERIPLUS_REPOSITORY_S3_USE_SSL": None,
    "AWS_REGION": None,
    "AWS_ACCESS_KEY_ID": None,
    "AWS_SECRET_ACCESS_KEY": None,
    "AWS_SESSION_TOKEN": None,
    "PERIPLUS_DUCKLAKE_S3_ENDPOINT": None,
    "PERIPLUS_DUCKLAKE_S3_REGION": None,
    "PERIPLUS_DUCKLAKE_S3_KEY_ID": None,
    "PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY": None,
    "PERIPLUS_DUCKLAKE_S3_SESSION_TOKEN": None,
    "PERIPLUS_DUCKLAKE_S3_URL_STYLE": None,
    "PERIPLUS_DUCKLAKE_S3_USE_SSL": None,
    "PERIPLUS_REPOSITORY_MAX_HTML_BYTES": str(64 * 1024 * 1024),
    "PERIPLUS_NATS_URL": None,
    "PERIPLUS_NATS_SEED": None,
    "PERIPLUS_NATS_MAX_ENVELOPE_BYTES": str(900 * 1024),
    "PERIPLUS_INGEST_MAX_DELIVER": "5",
    "PERIPLUS_INGEST_RESULT_POLL_SECONDS": "0.5",
    "PERIPLUS_INGEST_RESULT_TTL_SECONDS": str(7 * 24 * 60 * 60),
    "PERIPLUS_INGEST_RESULT_MAX_BYTES": str(256 * 1024 * 1024),
    "PERIPLUS_INGEST_RESULT_REPLICAS": "1",
    "PERIPLUS_CATALOGUE_WORK_STREAM_REPLICAS": "1",
    "PERIPLUS_CATALOGUE_WORK_MAX_BYTES": str(512 * 1024 * 1024),
    "PERIPLUS_DEAD_LETTER_TTL_SECONDS": str(30 * 24 * 60 * 60),
    "PERIPLUS_DEAD_LETTER_MAX_BYTES": str(256 * 1024 * 1024),
    "PERIPLUS_JANITOR_INTERVAL_SECONDS": "300",
    "PERIPLUS_CRAWLER_ID": None,
    "PERIPLUS_CDP_URL": "http://127.0.0.1:9222",
    "PERIPLUS_NAVIGATION_MAX_PACKAGE_BYTES": str(16 * 1024 * 1024),
    "PERIPLUS_NAVIGATION_CLEANUP_GRACE_SECONDS": "60",
    "PERIPLUS_EDGE_MAX_OUTPUT_ROWS": "100000",
    "PERIPLUS_EDGE_MAX_OUTPUT_BYTES": str(16 * 1024 * 1024),
    "PERIPLUS_EDGE_QUERY_MEMORY_LIMIT": "512MB",
    "PERIPLUS_EDGE_QUERY_TIMEOUT_SECONDS": "30",
    "PERIPLUS_CRAWLER_PRESENCE_TTL_SECONDS": "30",
    "PERIPLUS_CATALOGUE_WORKER_PRESENCE_TTL_SECONDS": "30",
    "PERIPLUS_DOMAIN_PACING_MAX_BYTES": str(256 * 1024 * 1024),
    "PERIPLUS_CATALOGUE_WORKER_MAX_BYTES": str(64 * 1024 * 1024),
    "PERIPLUS_OPERATION_LEASE_MAX_BYTES": str(64 * 1024 * 1024),
    "PERIPLUS_LOG_LEVEL": "INFO",
    "PERIPLUS_METRICS_ENABLED": "true",
    "PERIPLUS_METRICS_HOST": "0.0.0.0",
    "PERIPLUS_WORKER_QUEUE_STALL_SECONDS": "300",
    "PERIPLUS_CRAWLER_HEALTH_HOST": "0.0.0.0",
    "PERIPLUS_CRAWLER_HEALTH_PORT": "9099",
    "PERIPLUS_CRAWLER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "10",
    "PERIPLUS_CRAWLER_METRICS_PORT": "9090",
    "PERIPLUS_INGESTOR_METRICS_PORT": "9091",
    "PERIPLUS_INGESTOR_HEALTH_HOST": "0.0.0.0",
    "PERIPLUS_INGESTOR_HEALTH_PORT": "9092",
    "PERIPLUS_INGESTOR_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
    "PERIPLUS_INGESTOR_CONCURRENCY": "4",
    "PERIPLUS_MATERIALIZER_METRICS_PORT": "9093",
    "PERIPLUS_MATERIALIZER_HEALTH_HOST": "0.0.0.0",
    "PERIPLUS_MATERIALIZER_HEALTH_PORT": "9096",
    "PERIPLUS_MATERIALIZER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
    "PERIPLUS_MATERIALIZER_CONCURRENCY": "2",
    "PERIPLUS_JANITOR_METRICS_PORT": "9094",
    "PERIPLUS_JANITOR_HEALTH_HOST": "0.0.0.0",
    "PERIPLUS_JANITOR_HEALTH_PORT": "9095",
    "PERIPLUS_JANITOR_HEALTH_HEARTBEAT_TIMEOUT_SECONDS": "5",
}


class ConfigurationError(ValueError):
    """A Periplus setting is missing or invalid."""


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
        raise ConfigurationError(f"Unknown Periplus setting: {name}")
