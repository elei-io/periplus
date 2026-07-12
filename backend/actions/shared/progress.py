from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

ProgressStatus = Literal["waiting", "started", "succeeded", "failed"]
ProgressPhase = Literal[
    "cache",
    "calibrate",
    "calibration_candidate",
    "collect_query_evidence",
    "crawl",
    "crawl_batch",
    "crawl_capacity",
    "extract",
    "extract_query_params",
    "generate_schema",
    "index_depth",
    "persist_result",
    "schema",
    "search_provider",
    "task",
    "queue",
]
_ProgressSink = Callable[["ProgressEvent"], None | Awaitable[None]]
_CancellationCheck = Callable[[], None | Awaitable[None]]


@dataclass(frozen=True)
class ProgressEvent:
    phase: ProgressPhase
    status: ProgressStatus
    resource: str | None = None
    current: int | None = None
    total: int | None = None
    message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    duration: float | None = None
    error: str | None = None
    operation_id: str | None = None


class ProgressReporter:
    def __init__(
        self,
        sink: _ProgressSink | None = None,
        cancellation_check: _CancellationCheck | None = None,
    ) -> None:
        self._sink = sink
        self._cancellation_check = cancellation_check
        self._active_operations: dict[tuple[str, str | None], list[str]] = {}

    async def check_cancelled(self) -> None:
        if self._cancellation_check is None:
            return
        result = self._cancellation_check()
        if inspect.isawaitable(result):
            await result

    async def emit(self, event: ProgressEvent) -> None:
        key = (event.phase, event.resource)
        operation_id = event.operation_id
        if operation_id is None:
            if event.status in {"waiting", "started"}:
                operation_id = str(uuid4())
                self._active_operations.setdefault(key, []).append(operation_id)
            else:
                active = self._active_operations.get(key, [])
                operation_id = active.pop() if active else str(uuid4())
                if not active:
                    self._active_operations.pop(key, None)
            event = replace(event, operation_id=operation_id)
        if self._sink is None:
            return
        result = self._sink(event)
        if inspect.isawaitable(result):
            await result

    @asynccontextmanager
    async def phase(
        self,
        phase: ProgressPhase,
        *,
        resource: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[None]:
        fields = {
            "phase": phase,
            "resource": resource,
            "current": current,
            "total": total,
            "message": message,
            "metadata": metadata or {},
        }
        started_at = time.perf_counter()
        operation_id = str(uuid4())
        await self.check_cancelled()
        await self.emit(ProgressEvent(status="started", operation_id=operation_id, **fields))
        try:
            yield
        except BaseException as exc:
            await self.emit(
                ProgressEvent(
                    status="failed",
                    duration=time.perf_counter() - started_at,
                    error=str(exc),
                    operation_id=operation_id,
                    **fields,
                )
            )
            raise
        await self.check_cancelled()
        await self.emit(
            ProgressEvent(
                status="succeeded",
                duration=time.perf_counter() - started_at,
                operation_id=operation_id,
                **fields,
            )
        )


async def emit_progress(progress_reporter: ProgressReporter | None, event: ProgressEvent) -> None:
    if progress_reporter is not None:
        await progress_reporter.emit(event)
