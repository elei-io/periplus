from __future__ import annotations

from .recorder import record


def queue_wait(*, primitive: str, seconds: float) -> None:
    record("atlas_task_run_queue_duration_seconds", seconds, primitive=primitive)


def terminal_run(
    *,
    primitive: str,
    status: str,
    trigger_kind: str,
    duration_seconds: float,
) -> None:
    record(
        "atlas_task_run_execution_duration_seconds",
        duration_seconds,
        primitive=primitive,
        status=status,
    )
    record(
        "atlas_task_runs_total",
        primitive=primitive,
        status=status,
        trigger_kind=trigger_kind,
    )


def attempt_finished(*, primitive: str, outcome: str) -> None:
    record("atlas_task_run_attempts_total", primitive=primitive, outcome=outcome)


def recovered(*, reason: str) -> None:
    record("atlas_task_run_recoveries_total", reason=reason)


def cancelled(*, phase: str) -> None:
    record("atlas_task_run_cancellations_total", phase=phase)
