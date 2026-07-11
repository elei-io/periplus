"""NATS-backed Atlas runtime worker."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from datetime import UTC, datetime

from nats.errors import TimeoutError as NatsTimeoutError
from prometheus_client import start_http_server
from config import get_bool, get_float, get_int, get_optional, get_str

from runtime.progress import ProgressPublisher
from actions.shared.progress import ProgressEvent, ProgressReporter
from db import SessionLocal
from runtime.executor import TaskRunCancelled, execute_task
from control.tasks.models import Task
from runtime.task_queue import TASK_CONSUMER, TASK_STREAM, TASK_SUBJECT, TaskRunState, TaskWork, WorkerState, connect_nats, ensure_task_storage, get_run, release_task, update_run
from runtime.scheduler import run_scheduler_once


async def _process(message, runs, worker_id: str) -> None:
    work = TaskWork.model_validate_json(message.data)
    current = await get_run(runs, work.run_id)
    if current is None or current.status in {"succeeded", "failed", "cancelled", "skipped"}:
        await message.ack()
        return
    token = __import__("uuid").uuid4()
    now = datetime.now(UTC)

    def claim(state: TaskRunState) -> TaskRunState:
        if state.status not in {"queued", "running"}:
            return state
        return state.model_copy(update={"status": "running", "started_at": state.started_at or now, "attempt": state.attempt + 1, "worker_id": worker_id, "execution_token": token, "updated_at": now})

    run = await update_run(runs, work.run_id, claim)
    if run.execution_token != token:
        await message.ack()
        return

    async def keep_alive() -> None:
        interval = max(1.0, get_float("ATLAS_TASK_ACK_WAIT_SECONDS") / 3)
        while True:
            await asyncio.sleep(interval)
            await message.in_progress()

    heartbeat = asyncio.create_task(keep_alive())
    try:
        async with ProgressPublisher(run.id, attempt=run.attempt) as publisher:
            async def check_cancelled() -> None:
                state = await get_run(runs, run.id)
                if state is None or state.cancellation_requested_at is not None or state.execution_token != token:
                    raise TaskRunCancelled("Task run cancellation requested or ownership lost.")

            reporter = ProgressReporter(sink=publisher.progress, cancellation_check=check_cancelled)
            await reporter.emit(ProgressEvent(operation_id=f"{run.id}:task:{run.attempt}", phase="task", status="started", message=f"{run.primitive.title()} started."))
            try:
                with SessionLocal() as session:
                    execution = await asyncio.wait_for(
                        execute_task(session, run, reporter),
                        timeout=get_float("ATLAS_TASK_RUN_TIMEOUT_SECONDS"),
                    )
                    task = session.get(Task, run.task_id)
                    if task is not None:
                        task.last_run_at = datetime.now(UTC)
                    session.commit()
                warnings = {"codes": sorted({str(value.get("code")) for value in execution.warnings if value.get("code")}), "count": len(execution.warnings), "path": None}
                finished = datetime.now(UTC)
                def succeed(state: TaskRunState) -> TaskRunState:
                    if state.execution_token != token:
                        return state
                    return state.model_copy(update={"status": "succeeded", "finished_at": finished, "output_json": execution.output_json, "warnings_json": warnings, "error": None, "execution_token": None, "updated_at": finished})
                final = await update_run(runs, run.id, succeed)
            except TaskRunCancelled as exc:
                finished = datetime.now(UTC)
                def cancel(state: TaskRunState) -> TaskRunState:
                    if state.execution_token != token:
                        return state
                    return state.model_copy(update={"status": "cancelled", "finished_at": finished, "cancelled_at": finished, "error": str(exc), "execution_token": None, "updated_at": finished})
                final = await update_run(runs, run.id, cancel)
            except Exception as exc:
                finished = datetime.now(UTC)
                def fail(state: TaskRunState) -> TaskRunState:
                    if state.execution_token != token:
                        return state
                    failures = state.failed_attempts + 1
                    terminal = failures >= state.max_attempts
                    return state.model_copy(update={"status": "failed" if terminal else "queued", "failed_attempts": failures, "finished_at": finished if terminal else None, "error": str(exc), "execution_token": None, "worker_id": None, "updated_at": finished})
                final = await update_run(runs, run.id, fail)
            await publisher.publish(final.status, {"status": final.status, "error": final.error})
            if final.execution_token is not None and final.execution_token != token:
                return
            if final.status == "queued":
                await message.nak(delay=1)
            else:
                await release_task(runs, final.task_id, final.id)
                await message.ack()
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    worker_id = get_optional("ATLAS_RUNTIME_WORKER_ID") or f"{os.uname().nodename}:{os.getpid()}"
    capacity = get_int("ATLAS_RUNTIME_WORKER_CONCURRENCY")
    metrics_server = None
    if get_bool("ATLAS_METRICS_ENABLED"):
        metrics_server, _metrics_thread = start_http_server(
            get_int("ATLAS_RUNTIME_WORKER_METRICS_PORT"),
            addr=get_str("ATLAS_METRICS_HOST"),
        )
    client = await connect_nats()
    jetstream = client.jetstream()
    runs, workers = await ensure_task_storage(jetstream)
    subscription = await jetstream.pull_subscribe(TASK_SUBJECT, durable=TASK_CONSUMER, stream=TASK_STREAM)
    active: set[asyncio.Task] = set()
    started = datetime.now(UTC)

    async def presence() -> None:
        while not stop.is_set():
            state = WorkerState(worker_id=worker_id, started_at=started, last_seen_at=datetime.now(UTC), capacity=capacity, active_run_count=len(active), stopping=False)
            await workers.put(worker_id.replace(":", "-"), state.model_dump_json().encode())
            await asyncio.sleep(5)

    async def schedule() -> None:
        while not stop.is_set():
            try:
                await run_scheduler_once(SessionLocal, limit=get_int("ATLAS_SCHEDULER_BATCH_SIZE"))
            except Exception:
                pass
            await asyncio.sleep(max(0.5, get_float("ATLAS_RUNTIME_WORKER_POLL_SECONDS")))

    presence_task = asyncio.create_task(presence())
    scheduler_task = asyncio.create_task(schedule())
    try:
        while not stop.is_set():
            active = {task for task in active if not task.done()}
            available = capacity - len(active)
            if available <= 0:
                await asyncio.sleep(0.1)
                continue
            try:
                messages = await subscription.fetch(batch=available, timeout=1)
            except (NatsTimeoutError, asyncio.TimeoutError):
                continue
            active.update(asyncio.create_task(_process(message, runs, worker_id)) for message in messages)
    finally:
        stop.set()
        presence_task.cancel()
        scheduler_task.cancel()
        await asyncio.gather(presence_task, scheduler_task, *active, return_exceptions=True)
        await client.drain()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
            metrics_server.server_close()


def main() -> None:
    argparse.ArgumentParser(description="Run the Atlas runtime worker.").parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
