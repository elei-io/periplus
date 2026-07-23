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
# Keep a small delivery look-ahead so a saturated hostname cannot hide other
# ready hostnames behind its own politeness waiters. This is deliberately
# process-local and bounded well below the durable consumer delivery ceiling.
CRAWL_DISPATCH_WINDOW = CRAWL_ACQUISITION_LANES * 4
# Edge traversal may keep this many crawl requests for one run in acquisition
# (queued, retrying, or crawling). This bounds its position in the shared FIFO;
# completed acquisitions no longer consume the window while their edges run.
CRAWL_RUN_ACQUISITION_PENDING_LIMIT = CRAWL_DISPATCH_WINDOW
# A denied nonblocking domain probe is retried soon, but not on every worker
# loop iteration. Permit release remains responsive while saturated hosts avoid
# generating avoidable Resource Governor reads and admission metrics.
CRAWL_DOMAIN_PERMIT_RETRY_SECONDS = 0.25
CATALOGUE_EXECUTOR_LANES = 1
# Navigation retention is recovery cleanup, not a bulk-delete job. One bounded
# batch per housekeeping sweep keeps object-store pressure predictable.
NAVIGATION_CLEANUP_BATCH_SIZE = 500

# Durable consumers use a generous internal delivery ceiling. Most pull loops
# claim their local lane count; acquisition uses the fixed, bounded hostname
# look-ahead above. Replicas still add capacity without making either value an
# operator setting or allowing one process to hoard the consumer ceiling.
GRAPH_CONSUMER_MAX_ACK_PENDING = 1024
INGESTION_CONSUMER_MAX_ACK_PENDING = 1024
GRAPH_ACK_WAIT_SECONDS = 60.0
INGESTION_ACK_WAIT_SECONDS = 60.0

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
# Adaptive microbatch bounds.  A batch flushes on whichever bound is reached
# first, keeping latency bounded for small deployments and amortising commits
# automatically when replicas are busy.
INGEST_BATCH_MAX_ITEMS = 100
INGEST_BATCH_MAX_ELEMENT_ROWS = 250_000
INGEST_BATCH_MAX_BYTES = 256 * 1024 * 1024
INGEST_BATCH_MAX_WAIT_SECONDS = 10.0


def catalogue_max_concurrency() -> int:
    return get_int("ATLAS_CATALOGUE_MAX_CONCURRENCY")


def object_io_max_concurrency() -> int:
    return get_int("ATLAS_OBJECT_IO_MAX_CONCURRENCY")


def process_cpu_count() -> int:
    """Return CPU capacity visible to this container/process."""

    return max(1, os.process_cpu_count() or 1)


def duckdb_threads() -> int:
    """Bound one DuckDB client so adding process replicas remains predictable."""

    return min(2, process_cpu_count())


def duckdb_memory_limit() -> str:
    """Derive a conservative per-process DuckDB limit from its cgroup."""

    memory_bytes = _cgroup_memory_limit() or 8 * 1024**3
    derived = memory_bytes // 20
    bounded = min(2 * 1024**3, max(512 * 1024**2, derived))
    return f"{bounded // (1024**2)}MB"


def materialization_duckdb_memory_limit() -> str:
    """Allow whole-table builds enough memory without widening every worker."""

    memory_bytes = _cgroup_memory_limit() or 8 * 1024**3
    derived = memory_bytes // 8
    bounded = min(4 * 1024**3, max(1024 * 1024**2, derived))
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
