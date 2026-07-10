"""Live readiness state and HTTP endpoint for the repository ingestor."""

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

    def status(self) -> tuple[bool, str]:
        with self._lock:
            heartbeat_age = time.monotonic() - self._last_heartbeat
            dependencies_ready = self._dependencies_ready
            dependency_error = self._dependency_error
        if heartbeat_age > self.heartbeat_timeout_seconds:
            return False, "event loop heartbeat is stale"
        if not dependencies_ready:
            return False, dependency_error or "dependencies are unavailable"
        return True, "ready"


class _HealthServer(ThreadingHTTPServer):
    monitor: HealthMonitor


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/healthz":
            self.send_error(404)
            return
        ready, detail = self.server.monitor.status()  # type: ignore[attr-defined]
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
    """Start the ingestor readiness endpoint backed by live worker state."""

    server = _HealthServer((address, port), _HealthHandler)
    server.monitor = monitor
    thread = threading.Thread(
        target=server.serve_forever,
        name="atlas-ingestor-health",
        daemon=True,
    )
    thread.start()
    return server, thread
