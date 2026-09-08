"""Live readiness state and HTTP endpoint for Periplus workers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(slots=True)
class HealthMonitor:
    """Thread-safe readiness shared by the async worker and HTTP server."""

    heartbeat_timeout_seconds: float = 5.0
    _last_heartbeat: float = field(default_factory=time.monotonic)
    _dependencies_ready: bool = False
    _dependency_error: str | None = "dependencies have not been checked"
    _subsystems: dict[str, tuple[bool, str | None]] = field(default_factory=dict)
    _queues: dict[str, tuple[int, object, float, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat = time.monotonic()

    def dependencies_ready(self) -> None:
        with self._lock:
            self._dependencies_ready = True
            self._dependency_error = None

    def dependencies_unavailable(self, error: str) -> None:
        with self._lock:
            self._dependencies_ready = False
            self._dependency_error = error

    def subsystem_ready(self, name: str) -> None:
        with self._lock:
            self._subsystems[name] = (True, None)

    def subsystem_unavailable(self, name: str, error: str) -> None:
        with self._lock:
            self._subsystems[name] = (False, error)

    def queue_observed(
        self,
        name: str,
        *,
        pending: int,
        progress_marker: object,
        stalled_after_seconds: float,
    ) -> float:
        """Record owned-queue progress and return its current stalled age."""

        now = time.monotonic()
        with self._lock:
            previous = self._queues.get(name)
            last_progress = now
            if (
                pending > 0
                and previous is not None
                and previous[0] > 0
                and previous[1] == progress_marker
            ):
                last_progress = previous[2]
            self._queues[name] = (
                max(0, pending),
                progress_marker,
                last_progress,
                stalled_after_seconds,
            )
        return 0.0 if pending <= 0 else max(0.0, now - last_progress)

    def telemetry(self) -> dict[str, int]:
        with self._lock:
            return {"dependencies": int(self._dependencies_ready),
                    **{name: int(ready) for name, (ready, _) in self._subsystems.items()}}

    def alive(self) -> bool:
        with self._lock:
            return time.monotonic() - self._last_heartbeat <= self.heartbeat_timeout_seconds

    def status(
        self,
        *,
        include_liveness: bool = True,
        include_queues: bool = True,
        exclude_subsystems: frozenset[str] = frozenset(),
    ) -> tuple[bool, str]:
        with self._lock:
            heartbeat_age = time.monotonic() - self._last_heartbeat
            dependencies_ready = self._dependencies_ready
            dependency_error = self._dependency_error
            unavailable = {
                name: detail
                for name, (ready, detail) in self._subsystems.items()
                if not ready and name not in exclude_subsystems
            }
            stalled = (
                {
                    name: (pending, time.monotonic() - last_progress)
                    for name, (
                        pending,
                        _marker,
                        last_progress,
                        threshold,
                    ) in self._queues.items()
                    if pending > 0
                    and time.monotonic() - last_progress > threshold
                }
                if include_queues
                else {}
            )
        if include_liveness and heartbeat_age > self.heartbeat_timeout_seconds:
            return False, "event loop heartbeat is stale"
        if not dependencies_ready:
            return False, dependency_error or "dependencies are unavailable"
        if unavailable:
            detail = "; ".join(
                f"{name}: {error or 'unavailable'}"
                for name, error in sorted(unavailable.items())
            )
            return False, detail
        if stalled:
            detail = "; ".join(
                f"{name}: {pending} work items without progress for {age:.0f}s"
                for name, (pending, age) in sorted(stalled.items())
            )
            return False, detail
        return True, "ready"


class _HealthServer(ThreadingHTTPServer):
    monitor: HealthMonitor


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path not in {"/healthz", "/livez"}:
            self.send_error(404)
            return
        monitor = self.server.monitor  # type: ignore[attr-defined]
        ready = monitor.alive() if self.path == "/livez" else monitor.status()[0]
        detail = "ready" if ready else "unavailable"
        body = f"{detail}\n".encode()
        self.send_response(HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


def start_health_server(
    *,
    address: str,
    port: int,
    monitor: HealthMonitor,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start a worker readiness endpoint backed by live state."""

    server = _HealthServer((address, port), _HealthHandler)
    server.monitor = monitor
    thread = threading.Thread(
        target=server.serve_forever,
        name="periplus-worker-health",
        daemon=True,
    )
    thread.start()
    return server, thread
