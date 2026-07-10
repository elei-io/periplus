from __future__ import annotations

import argparse
import asyncio
import logging
import multiprocessing
import os
import signal
import time
import traceback
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from artifacts.cleanup import artifact_cleanup_interval_seconds, run_artifact_cleanup_once
from db import SessionLocal
from tasks.executor import (
    recover_expired_runs,
    claim_next_run,
    release_task_run,
    release_worker_runs,
    run_worker_once_sync,
)
from tasks.models import TaskRun, TaskRunLease
from tasks.notifications import RunQueueListener
from tasks.executor import record_worker_heartbeat
from tasks.scheduler import run_scheduler_once
from worker.logging import (
    bind_worker_id,
    configure_worker_logging,
    dependency_recovered,
    dependency_unavailable,
    worker_log,
)


def _run_process_entry(worker_id: str, run_id: UUID, lease_token: UUID, result_connection) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    configure_worker_logging()
    bind_worker_id(worker_id)

    def send(message) -> None:
        try:
            result_connection.send(message)
        except (BrokenPipeError, EOFError, OSError):
            pass

    try:
        send(
            (
                "result",
                run_worker_once_sync(
                    SessionLocal,
                    worker_id,
                    claimed_run_id=run_id,
                    claimed_lease_token=lease_token,
                ),
            )
        )
    except BaseException:
        send(("error", traceback.format_exc()))
    finally:
        result_connection.close()


async def _run_isolated_once(worker_id: str, run_id: UUID, lease_token: UUID) -> bool:
    context = multiprocessing.get_context("spawn")
    result_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_run_process_entry,
        args=(worker_id, run_id, lease_token, child_connection),
        name=f"atlas-run-{worker_id}",
    )
    previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        process.start()
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
    child_connection.close()
    release_failure = False

    def read_messages():
        messages = []
        while result_connection.poll():
            try:
                messages.append(result_connection.recv())
            except EOFError:
                break
        return messages

    def cancellation_requested() -> bool:
        with SessionLocal() as session:
            lease = session.get(TaskRunLease, run_id)
            run = session.get(TaskRun, run_id)
            return (
                lease is None
                or lease.lease_token != lease_token
                or run is None
                or run.status != "running"
                or run.cancellation_requested_at is not None
            )

    try:
        while True:
            for message in read_messages():
                if message[0] == "result":
                    return bool(message[1])
                elif message[0] == "error":
                    release_failure = True
                    raise RuntimeError(str(message[1]))
            if not process.is_alive():
                for message in read_messages():
                    if message[0] == "result":
                        return bool(message[1])
                    if message[0] == "error":
                        release_failure = True
                        raise RuntimeError(str(message[1]))
                release_failure = True
                raise RuntimeError(
                    f"Worker subprocess exited with code {process.exitcode} without a result."
                )
            if await asyncio.to_thread(cancellation_requested):
                return True
            await asyncio.sleep(0.25)
    finally:
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join)
        def release_claim() -> None:
            with SessionLocal.begin() as session:
                release_task_run(
                    session,
                    run_id,
                    lease_token=lease_token,
                    count_failure=release_failure,
                )

        await asyncio.to_thread(release_claim)
        result_connection.close()


async def _run_isolated_slot(worker_id: str, run_id: UUID, lease_token: UUID) -> bool:
    timeout = float(os.getenv("ATLAS_TASK_RUN_TIMEOUT_SECONDS", "1800"))
    lease = float(os.getenv("ATLAS_WORKER_LEASE_SECONDS", "45"))
    try:
        return await asyncio.wait_for(
            _run_isolated_once(worker_id, run_id, lease_token),
            timeout=max(1.0, timeout + lease),
        )
    except TimeoutError as exc:
        raise RuntimeError(
            f"Worker subprocess exceeded the {timeout:g}-second task timeout."
        ) from exc


def _claim_available_run(worker_id: str, capacity: int) -> tuple[UUID, UUID] | None:
    with SessionLocal.begin() as session:
        run = claim_next_run(session, worker_id=worker_id)
        record_worker_heartbeat(session, worker_id, capacity=capacity)
        if run is None or run.lease is None:
            return None
        return run.id, run.lease.lease_token


async def _loop() -> None:
    poll_seconds = float(os.getenv("ATLAS_WORKER_POLL_SECONDS", "5"))
    scheduler_limit = int(os.getenv("ATLAS_SCHEDULER_BATCH_SIZE", "20"))
    worker_id = os.getenv("ATLAS_WORKER_ID") or f"{os.uname().nodename}:{os.getpid()}"
    cleanup_interval_seconds = artifact_cleanup_interval_seconds()
    next_cleanup_at = 0.0
    last_cleanup_error_signature: tuple[tuple[str, int], ...] | None = None
    stop = asyncio.Event()
    queue_listener = RunQueueListener()
    try:
        concurrency = max(1, int(os.getenv("ATLAS_WORKER_CONCURRENCY", "4")))
    except ValueError:
        concurrency = 4
    active_runs: set[asyncio.Task[bool]] = set()
    bind_worker_id(worker_id)

    def request_stop() -> None:
        stop.set()

    running_loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        running_loop.add_signal_handler(sig, request_stop)

    heartbeat_seconds = float(os.getenv("ATLAS_WORKER_HEARTBEAT_SECONDS", "10"))
    lease_seconds = float(os.getenv("ATLAS_WORKER_LEASE_SECONDS", "45"))
    if lease_seconds < heartbeat_seconds * 2:
        raise RuntimeError(
            "ATLAS_WORKER_LEASE_SECONDS must be at least twice ATLAS_WORKER_HEARTBEAT_SECONDS."
        )

    worker_log(
        logging.INFO,
        "worker.started",
        worker_id=worker_id,
        poll_seconds=poll_seconds,
        heartbeat_seconds=heartbeat_seconds,
        lease_seconds=lease_seconds,
        task_run_timeout_seconds=float(os.getenv("ATLAS_TASK_RUN_TIMEOUT_SECONDS", "1800")),
        concurrency=concurrency,
    )
    while not stop.is_set():
        try:
            def control_tick() -> int:
                enqueued = run_scheduler_once(SessionLocal, limit=scheduler_limit)
                with SessionLocal() as session:
                    recover_expired_runs(session)
                    record_worker_heartbeat(session, worker_id, capacity=concurrency)
                    session.commit()
                return enqueued

            enqueued = await asyncio.to_thread(control_tick)
            dependency_recovered("database")
            if enqueued:
                worker_log(logging.INFO, "scheduler.enqueued", worker_id=worker_id, count=enqueued)

            if cleanup_interval_seconds > 0 and time.monotonic() >= next_cleanup_at:
                next_cleanup_at = time.monotonic() + cleanup_interval_seconds
                try:
                    cleanup = await asyncio.to_thread(run_artifact_cleanup_once, SessionLocal)
                    if cleanup.changed:
                        anomaly_reasons: dict[str, int] = {}
                        for anomaly in cleanup.anomalies:
                            reason = anomaly["reason"]
                            anomaly_reasons[reason] = anomaly_reasons.get(reason, 0) + 1
                        error_signature = tuple(sorted(anomaly_reasons.items())) or None
                        has_cleanup_activity = any(
                            (
                                cleanup.invalidated,
                                cleanup.rows_deleted,
                                cleanup.files_deleted,
                                cleanup.missing_files,
                            )
                        )
                        if has_cleanup_activity or error_signature != last_cleanup_error_signature:
                            worker_log(
                                logging.WARNING if cleanup.errors else logging.INFO,
                                "artifact_cleanup.completed",
                                worker_id=worker_id,
                                invalidated=cleanup.invalidated,
                                rows_deleted=cleanup.rows_deleted,
                                files_deleted=cleanup.files_deleted,
                                missing_files=cleanup.missing_files,
                                errors=cleanup.errors,
                                error_reasons=anomaly_reasons or None,
                            )
                        last_cleanup_error_signature = error_signature
                except Exception:
                    worker_log(
                        logging.ERROR,
                        "artifact_cleanup.failed",
                        worker_id=worker_id,
                        exc_info=True,
                    )

            while len(active_runs) < concurrency:
                claimed = await asyncio.to_thread(_claim_available_run, worker_id, concurrency)
                if claimed is None:
                    break
                run_id, lease_token = claimed
                active_runs.add(
                    asyncio.create_task(_run_isolated_slot(worker_id, run_id, lease_token))
                )
            await asyncio.sleep(0)
        except SQLAlchemyError as exc:
            dependency_unavailable("database", exc)
        except Exception:
            worker_log(
                logging.ERROR,
                "worker.iteration_failed",
                worker_id=worker_id,
                exc_info=True,
            )

        completed = {task for task in active_runs if task.done()}
        for task in completed:
            try:
                task.result()
            except Exception:
                worker_log(logging.ERROR, "worker.iteration_failed", exc_info=True)
        active_runs.difference_update(completed)

        notification = asyncio.create_task(queue_listener.wait(poll_seconds))
        stopping = asyncio.create_task(stop.wait())
        waiters: set[asyncio.Task] = {*active_runs, notification, stopping}
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        for task in done & active_runs:
            try:
                task.result()
            except Exception:
                worker_log(logging.ERROR, "worker.iteration_failed", exc_info=True)
        active_runs.difference_update(done)
        for auxiliary in (notification, stopping):
            if not auxiliary.done():
                auxiliary.cancel()
        await asyncio.gather(notification, stopping, return_exceptions=True)

    worker_log(logging.INFO, "worker.stopping", worker_id=worker_id)
    if active_runs:
        grace_seconds = max(0.0, float(os.getenv("ATLAS_WORKER_SHUTDOWN_GRACE_SECONDS", "30")))
        _, pending = await asyncio.wait(active_runs, timeout=grace_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*active_runs, return_exceptions=True)
    await queue_listener.close()
    with SessionLocal() as session:
        released = release_worker_runs(session, worker_id)
        record_worker_heartbeat(session, worker_id, capacity=concurrency, stopping=True)
        session.commit()
    worker_log(logging.INFO, "worker.stopped", worker_id=worker_id, released_runs=released)


def _run() -> None:
    configure_worker_logging()
    asyncio.run(_loop())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Atlas task worker.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart the worker when Python files change (development only).",
    )
    args = parser.parse_args()

    if args.reload:
        from watchfiles import PythonFilter, run_process

        backend_root = Path(__file__).resolve().parents[1]
        run_process(backend_root, target=_run, watch_filter=PythonFilter())
        return

    _run()


if __name__ == "__main__":
    main()
