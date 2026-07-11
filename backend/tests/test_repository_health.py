from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from repository.ingestion.health import HealthMonitor, start_health_server


class RepositoryHealthTests(unittest.TestCase):
    def test_health_server_exposes_only_readiness_endpoint(self) -> None:
        monitor = HealthMonitor()
        monitor.dependencies_ready()
        server, _thread = start_health_server(
            address="127.0.0.1", port=0, monitor=monitor
        )
        port = server.server_address[1]
        try:
            with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"ready\n")
            with self.assertRaises(HTTPError) as raised:
                urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2)
            try:
                self.assertEqual(raised.exception.code, 404)
            finally:
                raised.exception.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_health_fails_when_dependencies_are_unavailable(self) -> None:
        monitor = HealthMonitor()
        monitor.dependencies_unavailable("catalogue unavailable")
        server, _thread = start_health_server(
            address="127.0.0.1", port=0, monitor=monitor
        )
        try:
            with self.assertRaises(HTTPError) as raised:
                urlopen(f"http://127.0.0.1:{server.server_address[1]}/healthz", timeout=2)
            self.assertEqual(raised.exception.code, 503)
            self.assertEqual(raised.exception.read(), b"catalogue unavailable\n")
            raised.exception.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_health_fails_when_event_loop_heartbeat_is_stale(self) -> None:
        monitor = HealthMonitor(heartbeat_timeout_seconds=1)
        monitor.dependencies_ready()
        with patch("repository.ingestion.health.time.monotonic", side_effect=[10.0, 12.0]):
            monitor.heartbeat()
            self.assertEqual(monitor.status(), (False, "event loop heartbeat is stale"))


if __name__ == "__main__":
    unittest.main()
