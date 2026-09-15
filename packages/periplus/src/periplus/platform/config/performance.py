"""Code-owned runtime sizing for Periplus workloads."""

from __future__ import annotations


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
# Navigation retention is recovery cleanup, not a bulk-delete job. One bounded
# batch per janitor sweep keeps object-store pressure predictable.
NAVIGATION_CLEANUP_BATCH_SIZE = 500
# Yield between bounded frontier cleanup windows, rather than sleeping on backlog.
FRONTIER_CLEANUP_WINDOW_SECONDS = 30.0
FRONTIER_CLEANUP_RETRY_SECONDS = 1.0

# Domain concurrency is a website-politeness contract, separate from generic
# infrastructure admission. Each hostname has an independent expiring state key.
DOMAIN_PERMIT_LEASE_SECONDS = 120.0
DOMAIN_PERMIT_HEARTBEAT_SECONDS = 30.0
CATALOGUE_OPERATION_LEASE_SECONDS = 30.0
CATALOGUE_OPERATION_HEARTBEAT_SECONDS = 5.0
CATALOGUE_OPERATION_ACQUIRE_TIMEOUT_SECONDS = 1.0
# Bound remote transactions after a worker loses its connection. PostgreSQL 17+.
REMOTE_TRANSACTION_TIMEOUT_SECONDS = 240
POSTGRES_TRANSACTION_OPTIONS = (
    f"-c transaction_timeout={REMOTE_TRANSACTION_TIMEOUT_SECONDS * 1000} "
    f"-c statement_timeout={REMOTE_TRANSACTION_TIMEOUT_SECONDS * 1000} "
    f"-c idle_in_transaction_session_timeout={REMOTE_TRANSACTION_TIMEOUT_SECONDS * 1000}"
)
