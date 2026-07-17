"""Code-owned runtime sizing for Atlas workloads.

Replica counts are the normal scaling control.  The two deployment-wide maxima
below are safety rails for shared infrastructure, not per-worker tuning knobs.
Everything else is deliberately derived or fixed by workload type.
"""

from __future__ import annotations

import os
from pathlib import Path

from config.environment import get_int


# Local executor lanes. Catalogue-owning processes remain single-lane by
# architecture; acquisition workers keep bounded process-local coordination.
CRAWL_ACQUISITION_LANES = 12
CATALOGUE_EXECUTOR_LANES = 1

# Durable consumers use a generous internal delivery ceiling.  Pull loops only
# claim their local lane count, so replicas add capacity without making this an
# operator setting or allowing one process to hoard work.
GRAPH_CONSUMER_MAX_ACK_PENDING = 1024
INGESTION_CONSUMER_MAX_ACK_PENDING = 1024
GRAPH_ACK_WAIT_SECONDS = 60.0
INGESTION_ACK_WAIT_SECONDS = 60.0
MATERIALIZATION_ACK_WAIT_SECONDS = 60.0
CATALOGUE_READ_POOL_WAIT_SECONDS = 5.0
CATALOGUE_READ_MAX_ATTEMPTS = 3
CATALOGUE_READ_RETRY_SECONDS = 0.1

# Resource-governor mechanics are protocol constants.  Operators size only the
# shared maximum pressure accepted by their state/storage platform.
RESOURCE_LEASE_SECONDS = 120.0
RESOURCE_HEARTBEAT_SECONDS = 30.0
RESOURCE_ACQUIRE_TIMEOUT_SECONDS = 10.0
RESOURCE_STATE_REPLICAS = 1
OBJECT_IO_UNIT_BYTES = 8 * 1024 * 1024
CATALOGUE_OPERATION_LOCK_TIMEOUT_SECONDS = 60.0
CATALOGUE_OPERATION_MAX_ATTEMPTS = 5
CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS = 0.1
CATALOGUE_OPERATION_RETRY_MAX_SECONDS = 2.0
CATALOGUE_OPERATION_LEASE_SECONDS = 30.0
CATALOGUE_OPERATION_HEARTBEAT_SECONDS = 5.0
CATALOGUE_OPERATION_ACQUIRE_TIMEOUT_SECONDS = 1.0
CATALOGUE_OPERATION_LEASE_REPLICAS = 1
CATALOGUE_POSTGRES_POOL_MAX_CONNECTIONS = 2
CATALOGUE_POSTGRES_POOL_IDLE_TIMEOUT_MS = 5_000
CATALOGUE_POSTGRES_POOL_MAX_LIFETIME_MS = 60_000
CATALOGUE_POSTGRES_POOL_WAIT_TIMEOUT_MS = 10_000

# Adaptive microbatch bounds.  A batch flushes on whichever bound is reached
# first, keeping latency bounded for small deployments and amortising commits
# automatically when replicas are busy.
INGEST_BATCH_MAX_ITEMS = 100
INGEST_BATCH_MAX_ELEMENT_ROWS = 250_000
INGEST_BATCH_MAX_BYTES = 256 * 1024 * 1024
INGEST_BATCH_MAX_WAIT_SECONDS = 5.0


def catalogue_max_concurrency() -> int:
    return get_int("ATLAS_CATALOGUE_MAX_CONCURRENCY")


def object_io_max_concurrency() -> int:
    return get_int("ATLAS_OBJECT_IO_MAX_CONCURRENCY")


def process_cpu_count() -> int:
    """Return CPU capacity visible to this container/process."""

    return max(1, os.process_cpu_count() or 1)


def duckdb_threads() -> int:
    """Bound an embedded executor so adding replicas remains predictable."""

    return min(2, process_cpu_count())


def catalogue_read_pool_size() -> int:
    return min(4, max(1, process_cpu_count() // 2))


def catalogue_read_threads() -> int:
    return 1


def duckdb_memory_limit() -> str:
    """Derive a conservative per-process DuckDB limit from its cgroup."""

    memory_bytes = _cgroup_memory_limit() or 8 * 1024**3
    derived = memory_bytes // 20
    bounded = min(2 * 1024**3, max(512 * 1024**2, derived))
    return f"{bounded // (1024**2)}MB"


def _cgroup_memory_limit() -> int | None:
    for path in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            raw = path.read_text().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        # cgroup v1 commonly reports a near-u64 sentinel for no limit.
        if 0 < value < 1 << 60:
            return value
    return None
