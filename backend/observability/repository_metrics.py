"""Bounded operational observations for the repository ingestion path."""

from __future__ import annotations

from .recorder import record, snapshot


def raw_write(
    *,
    outcome: str,
    duration_seconds: float,
    html_bytes: int | None = None,
    compressed_bytes: int | None = None,
) -> None:
    record("atlas_repository_raw_writes_total", outcome=outcome)
    record(
        "atlas_repository_raw_write_duration_seconds",
        max(0.0, duration_seconds),
        outcome=outcome,
    )
    if html_bytes is not None:
        record("atlas_repository_raw_html_bytes", html_bytes)
    if compressed_bytes is not None:
        record("atlas_repository_raw_compressed_bytes", compressed_bytes)


def attempt(*, outcome: str, queue_seconds: float) -> None:
    record("atlas_repository_ingestion_attempts_total", outcome=outcome)
    record(
        "atlas_repository_ingestion_queue_duration_seconds",
        max(0.0, queue_seconds),
        outcome=outcome,
    )


def preparation(*, outcome: str, duration_seconds: float) -> None:
    record(
        "atlas_repository_ingestion_preparation_duration_seconds",
        max(0.0, duration_seconds),
        outcome=outcome,
    )


def batch(
    *,
    outcome: str,
    duration_seconds: float,
    items: int,
    element_rows: int,
    staged_bytes: int,
) -> None:
    record("atlas_repository_ingestion_batches_total", outcome=outcome)
    record(
        "atlas_repository_ingestion_commit_duration_seconds",
        max(0.0, duration_seconds),
        outcome=outcome,
    )
    record("atlas_repository_ingestion_batch_items", items)
    record("atlas_repository_ingestion_batch_element_rows", element_rows)
    record("atlas_repository_ingestion_batch_staged_bytes", staged_bytes)


def queue_state(*, pending: int, ack_pending: int, redelivered: int) -> None:
    snapshot("atlas_repository_ingestion_jobs_pending", pending)
    snapshot("atlas_repository_ingestion_jobs_ack_pending", ack_pending)
    snapshot("atlas_repository_ingestion_jobs_redelivered", redelivered)
