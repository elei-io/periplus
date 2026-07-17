"""Shared process-lifecycle mechanics for Atlas worker entrypoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
import signal
from typing import Literal

from prometheus_client import start_http_server

from config import get_bool, get_int, get_str
from repository.ingestion.health import HealthMonitor, start_health_server


WorkerRole = Literal[
    "acquisition",
    "ingestion",
    "materialization",
    "maintenance",
]


@dataclass(frozen=True, slots=True)
class WorkerEndpointConfig:
    """Validated health and metrics listener configuration for one worker role."""

    health_address: str
    health_port: int
    metrics_enabled: bool
    metrics_address: str
    metrics_port: int | None

    @classmethod
    def from_env(cls, role: WorkerRole) -> WorkerEndpointConfig:
        prefix = f"ATLAS_{role.upper()}_WORKER"
        metrics_enabled = get_bool("ATLAS_METRICS_ENABLED")
        return cls(
            health_address=get_str(f"{prefix}_HEALTH_HOST"),
            health_port=get_int(f"{prefix}_HEALTH_PORT"),
            metrics_enabled=metrics_enabled,
            metrics_address=get_str("ATLAS_METRICS_HOST"),
            metrics_port=(
                get_int(f"{prefix}_METRICS_PORT")
                if metrics_enabled
                else None
            ),
        )


class WorkerEndpoints:
    """Own one worker process's health and optional metrics servers."""

    def __init__(self, config: WorkerEndpointConfig) -> None:
        self._config = config
        self._health_server = None
        self._metrics_server = None

    def start_health(self, monitor: HealthMonitor) -> None:
        if self._health_server is not None:
            raise RuntimeError("worker health server is already running")
        self._health_server, _thread = start_health_server(
            address=self._config.health_address,
            port=self._config.health_port,
            monitor=monitor,
        )

    def start_metrics(self) -> None:
        if not self._config.metrics_enabled:
            return
        if self._metrics_server is not None:
            raise RuntimeError("worker metrics server is already running")
        if self._config.metrics_port is None:
            raise RuntimeError("worker metrics port is not configured")
        self._metrics_server, _thread = start_http_server(
            self._config.metrics_port,
            addr=self._config.metrics_address,
        )

    async def close(self) -> None:
        """Stop listeners in the order used by the existing worker implementations."""

        if self._health_server is not None:
            server, self._health_server = self._health_server, None
            await asyncio.to_thread(server.shutdown)
            server.server_close()
        if self._metrics_server is not None:
            server, self._metrics_server = self._metrics_server, None
            await asyncio.to_thread(server.shutdown)
            server.server_close()


def install_signal_handlers(stop: asyncio.Event) -> None:
    """Request cooperative worker shutdown for either process termination signal."""

    loop = asyncio.get_running_loop()
    for received_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received_signal, stop.set)


async def supervise_until_stopped(
    tasks: Mapping[str, Awaitable[None]],
    stop: asyncio.Event,
) -> None:
    """Run core tasks until shutdown or the first unexpected task exit."""

    running = [
        asyncio.create_task(operation, name=name)
        for name, operation in tasks.items()
    ]
    stop_task = asyncio.create_task(stop.wait(), name="worker-stop")
    done, _pending = await asyncio.wait(
        [*running, stop_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    error = next(
        (
            task.exception()
            for task in done
            if task is not stop_task and not task.cancelled()
        ),
        None,
    )
    for task in (*running, stop_task):
        task.cancel()
    await asyncio.gather(*running, stop_task, return_exceptions=True)
    if error is not None:
        raise error


async def monitor_heartbeat(
    monitor: HealthMonitor,
    stop: asyncio.Event | None = None,
    *,
    interval_seconds: float = 1,
) -> None:
    """Keep event-loop readiness fresh until stopped or cancelled."""

    while stop is None or not stop.is_set():
        monitor.heartbeat()
        if stop is None:
            await asyncio.sleep(interval_seconds)
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def cancel_task(task: asyncio.Task | None) -> None:
    """Cancel and reap an optional background task."""

    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
