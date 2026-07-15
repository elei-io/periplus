"""Direct Prometheus metrics for repository ingestion."""

from prometheus_client import Counter, Gauge, Histogram

_raw_writes = Counter("atlas_repository_raw_writes_total", "Raw content write outcomes.", ("outcome",))
_attempts = Counter("atlas_repository_ingestion_attempts_total", "Repository ingestion outcomes.", ("outcome",))
_batches = Counter("atlas_repository_ingestion_batches_total", "Repository batch outcomes.", ("outcome",))
_duration = Histogram("atlas_repository_ingestion_duration_seconds", "Repository ingestion phase duration.", ("phase", "outcome"))
_batch_items = Histogram("atlas_repository_ingestion_batch_items", "Items per repository batch.")
_pending = Gauge("atlas_repository_ingestion_jobs_pending", "Repository jobs waiting in JetStream.")
_ack_pending = Gauge("atlas_repository_ingestion_jobs_ack_pending", "Delivered repository jobs awaiting acknowledgement.")
_redelivered = Gauge("atlas_repository_ingestion_jobs_redelivered", "Redelivered repository jobs.")
_oldest_pending_age = Gauge(
    "atlas_repository_ingestion_oldest_pending_age_seconds",
    "Lower-bound age of an ingestion queue that has not made progress.",
)
_compactions = Counter("atlas_repository_compactions_total", "Repository compaction checks.", ("outcome",))
_compaction_files = Counter("atlas_repository_compaction_files_total", "Repository files involved in compaction.", ("kind",))


def raw_write(*, outcome: str, duration_seconds: float, html_bytes: int | None = None, compressed_bytes: int | None = None) -> None:
    _raw_writes.labels(outcome).inc()
    _duration.labels("raw_write", outcome).observe(max(0.0, duration_seconds))


def attempt(*, outcome: str, queue_seconds: float) -> None:
    _attempts.labels(outcome).inc()
    _duration.labels("queue", outcome).observe(max(0.0, queue_seconds))


def preparation(*, outcome: str, duration_seconds: float) -> None:
    _duration.labels("preparation", outcome).observe(max(0.0, duration_seconds))


def batch(*, outcome: str, duration_seconds: float, items: int, element_rows: int, staged_bytes: int) -> None:
    _batches.labels(outcome).inc()
    _duration.labels("commit", outcome).observe(max(0.0, duration_seconds))
    _batch_items.observe(items)


def queue_state(
    *,
    pending: int,
    ack_pending: int,
    redelivered: int,
    oldest_pending_age_seconds: float = 0.0,
) -> None:
    _pending.set(pending)
    _ack_pending.set(ack_pending)
    _redelivered.set(redelivered)
    _oldest_pending_age.set(max(0.0, oldest_pending_age_seconds))


def compaction(
    *,
    outcome: str,
    duration_seconds: float,
    files_processed: int,
    files_created: int,
) -> None:
    _compactions.labels(outcome).inc()
    _duration.labels("compaction", outcome).observe(max(0.0, duration_seconds))
    _compaction_files.labels("processed").inc(files_processed)
    _compaction_files.labels("created").inc(files_created)
