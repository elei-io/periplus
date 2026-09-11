"""Bounded, backpressured delivery of one query execution on the existing HTTP lane."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import json
import threading

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from periplus.operations.query_history.schemas import PreparationEvidence
from periplus.query.errors import query_error
from periplus.query.history import result_fields, track

MEDIA_TYPE = "application/x-ndjson"


class QueryStreamResponse(StreamingResponse):
    """Own admission until the producer has stopped, including disconnect cleanup."""

    def __init__(self, request, payload, limits):
        self.request, self.payload, self.limits = request, payload, limits
        self.cancelled = threading.Event()
        self.worker = None
        self.executing = threading.Event()
        super().__init__(self.frames(), media_type=MEDIA_TYPE,
                         headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def __call__(self, scope, receive, send):
        try:
            with anyio.move_on_after(self.limits.max_duration_seconds + 1):
                await super().__call__(scope, receive, send)
        finally:
            self.cancelled.set()
            # The admission slot remains ours until this exact producer is finished.
            with anyio.CancelScope(shield=True):
                if self.worker is not None:
                    if self.executing.is_set():
                        connection = self.request.app.state.query_service.connection
                        if connection is not None:
                            connection.interrupt()
                    await self.worker
                self.request.app.state.query_slot.release()

    async def frames(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=2)
        deadline = loop.time() + self.limits.max_duration_seconds

        def emit(frame):
            if self.cancelled.is_set():
                raise TimeoutError("Query stream was cancelled.")
            pending = asyncio.run_coroutine_threadsafe(queue.put(frame), loop)
            try:
                while True:
                    try:
                        pending.result(timeout=0.1)
                        return
                    except FutureTimeout:
                        if self.cancelled.is_set() or loop.time() >= deadline:
                            raise TimeoutError("Query stream delivery time limit exceeded.") from None
            finally:
                if not pending.done():
                    pending.cancel()

        async def produce():
            async with track(self.request, self.payload, "execute") as record:
                service = self.request.app.state.query_service
                evidence = PreparationEvidence(compiler_version=service.compiler_version)
                try:
                    self.executing.set()
                    try:
                        result = await run_in_threadpool(service.execute, self.payload, limits=self.limits,
                                                         evidence=evidence, emit=emit, cancelled=self.cancelled)
                    finally:
                        self.executing.clear()
                    record.update(result_fields(result))
                    terminal = dict(type="complete", row_count=result.row_count, result_bytes=result.result_bytes,
                                    truncated=result.truncated, truncation_reason=result.truncation_reason,
                                    elapsed_ms=result.elapsed_ms)
                except Exception as exc:
                    status, error = query_error(exc)
                    record.update(outcome="timeout" if status == 408 else "rejected" if status < 500 else "failed", error_code=error.code)
                    terminal = dict(type="error", status=status, **error.model_dump())
                finally:
                    record.update(evidence.model_dump())
                from periplus.query.server_http import _query_outcomes
                _query_outcomes.labels("execute", record.get("error_code") or "success").inc()
                if self.cancelled.is_set():
                    record.update(outcome="cancelled", error_code="request_cancelled")
                else:
                    # Terminal delivery is bounded too, including when the data deadline fired.
                    try:
                        await asyncio.wait_for(queue.put(terminal), timeout=1)
                    except TimeoutError:
                        pass

        self.worker = asyncio.create_task(produce())
        while True:
            # Heartbeat whitespace keeps the HTTP transport alive during preparation;
            # the SDK still requires an explicit complete frame.
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                if self.worker.done():
                    return
                yield b"\n"
                continue
            yield (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            if frame["type"] in {"complete", "error"}:
                return
