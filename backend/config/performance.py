"""Code-owned runtime sizing for Atlas workloads."""

from __future__ import annotations

import os
from pathlib import Path


# Local executor lanes.
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
# loop iteration.
CRAWL_DOMAIN_PERMIT_RETRY_SECONDS = 0.25
INGESTION_QUACK_CLIENTS = 4
MATERIALIZATION_QUACK_CLIENTS = 8
# Bootstrap may use several independent materialization lanes, while retaining
# headroom for live refreshes and other managed-lake work.
MATERIALIZATION_BOOTSTRAP_CONCURRENCY = 3
# Navigation retention is recovery cleanup, not a bulk-delete job. One bounded
# batch per housekeeping sweep keeps object-store pressure predictable.
NAVIGATION_CLEANUP_BATCH_SIZE = 500

# Adaptive microbatch bounds. A batch flushes on whichever bound is reached
# first, keeping latency bounded for small deployments and amortising commits
# automatically when replicas are busy.
INGEST_BATCH_MAX_ITEMS = 100
INGEST_BATCH_MAX_ELEMENT_ROWS = 250_000
INGEST_BATCH_MAX_BYTES = 256 * 1024 * 1024
INGEST_BATCH_MAX_WAIT_SECONDS = 10.0
# Native DuckDB calls cannot be cancelled safely after entering C++.
INGESTION_CATALOGUE_HARD_TIMEOUT_SECONDS = 300.0

# Durable consumers cap unacknowledged delivery at bounded executor capacity.
# Ingestion may hold at most one full batch per client lane; acquisition uses
# the fixed hostname look-ahead above. Replicas share the durable ceiling and
# therefore apply backpressure instead of hoarding an arbitrary 1,024 jobs.
GRAPH_CONSUMER_MAX_ACK_PENDING = 1024
INGESTION_CONSUMER_MAX_ACK_PENDING = (
    INGESTION_QUACK_CLIENTS * INGEST_BATCH_MAX_ITEMS
)
GRAPH_ACK_WAIT_SECONDS = 60.0
INGESTION_ACK_WAIT_SECONDS = 60.0

# Domain concurrency is a website-politeness contract, separate from generic
# infrastructure admission. Each hostname has an independent expiring state key.
DOMAIN_PERMIT_LEASE_SECONDS = 120.0
DOMAIN_PERMIT_HEARTBEAT_SECONDS = 30.0
OPERATIONAL_STATE_REPLICAS = 1
CATALOGUE_OPERATION_MAX_ATTEMPTS = 5
CATALOGUE_OPERATION_RETRY_INITIAL_SECONDS = 0.1
CATALOGUE_OPERATION_RETRY_MAX_SECONDS = 2.0
CATALOGUE_OPERATION_LEASE_SECONDS = 30.0
CATALOGUE_OPERATION_HEARTBEAT_SECONDS = 5.0
CATALOGUE_OPERATION_ACQUIRE_TIMEOUT_SECONDS = 1.0
CATALOGUE_OPERATION_LEASE_REPLICAS = 1
def process_cpu_count() -> int:
    """Return CPU capacity visible to this container/process."""

    return max(1, os.process_cpu_count() or 1)


def duckdb_threads() -> int:
    """Bound one DuckDB client so adding process replicas remains predictable."""

    return min(2, process_cpu_count())


def duckdb_memory_limit() -> str:
    """Derive a conservative per-client DuckDB limit from its cgroup."""

    memory_bytes = _cgroup_memory_limit() or 8 * 1024**3
    derived = memory_bytes // (INGESTION_QUACK_CLIENTS * 4)
    bounded = min(512 * 1024**2, max(128 * 1024**2, derived))
    return f"{bounded // (1024**2)}MB"


def materialization_duckdb_memory_limit() -> str:
    """Bound each client while leaving Basin responsible for analytical memory."""

    memory_bytes = _cgroup_memory_limit() or 8 * 1024**3
    derived = memory_bytes // (MATERIALIZATION_QUACK_CLIENTS * 2)
    bounded = min(1024 * 1024**2, max(256 * 1024**2, derived))
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
