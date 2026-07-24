from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from api.app import lifespan


class ApiLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_receives_postgres_graph_runtime(self) -> None:
        runs = object()
        requests = runs
        workers = object()
        nats_client = MagicMock()
        nats_client.jetstream.return_value = object()
        nats_client.drain = AsyncMock()
        catalogue_control = MagicMock()
        catalogue_control.start = AsyncMock()
        catalogue_control.close = AsyncMock()
        catalogue_control.latest_snapshot = AsyncMock(return_value=1)
        quack_runtime = MagicMock()
        quack_runtime.start = AsyncMock()
        quack_runtime.close = AsyncMock()
        scheduler = AsyncMock()

        with (
            patch("api.app.connect_nats", AsyncMock(return_value=nats_client)),
            patch(
                "api.app.ensure_graph_storage",
                AsyncMock(return_value=(runs, requests, workers)),
            ),
            patch(
                "api.app.ensure_catalogue_worker_storage",
                AsyncMock(return_value=object()),
            ),
            patch(
                "api.app.ensure_catalogue_query_storage",
                AsyncMock(return_value=object()),
            ),
            patch("api.app.CatalogueControl", return_value=catalogue_control),
            patch("api.app.QuackQueryRuntime", return_value=quack_runtime),
            patch("api.app.run_outbox_relay", AsyncMock()),
            patch("api.app.run_scheduler", scheduler),
        ):
            app = SimpleNamespace(state=SimpleNamespace())
            async with lifespan(app):
                self.assertIs(app.state.graph_runtime.runs, runs)

        self.assertIs(scheduler.call_args.kwargs["progress"], runs)


if __name__ == "__main__":
    unittest.main()
