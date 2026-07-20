from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from ducklake_cdc_client import LeaseContentionError

from materialization.executor import (
    _probe_dependencies_once,
    _supervise_cdc,
    _supervise_subsystem,
)
from materialization.live import (
    _close_consumer,
    _crawl_triggered_scopes,
    _open_crawl_planner_consumer,
    _open_url_planner_consumer,
    _required_consumer_starts,
    _run_blocking,
    _url_batch_scopes,
    run_crawl_planner,
)
from repository.ingestion.health import HealthMonitor


class MaterializationSupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_probe_treats_an_owned_catalogue_lane_as_busy(self) -> None:
        client = SimpleNamespace(flush=AsyncMock())
        catalogue = SimpleNamespace(connection=SimpleNamespace(execute=MagicMock()))
        lane = SimpleNamespace(locked=lambda: True)
        with (
            patch(
                "materialization.executor.resource_permits",
                side_effect=AssertionError("health must not request a work permit"),
            ),
            patch("materialization.executor.catalogue_operation_lane", return_value=lane),
            patch(
                "materialization.executor.run_catalogue_operation",
                new=AsyncMock(),
            ) as run_operation,
        ):
            await _probe_dependencies_once(
                client,
                catalogue,
                timeout=0.1,
            )

        client.flush.assert_awaited_once()
        run_operation.assert_not_awaited()

    async def test_cdc_failure_is_retried_without_escaping_supervisor(self) -> None:
        stop = asyncio.Event()
        attempts = 0
        retried = asyncio.Event()

        async def operation() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise LeaseContentionError("lease contention")
            retried.set()
            await stop.wait()

        monitor = HealthMonitor()
        monitor.dependencies_ready()
        task = asyncio.create_task(
            _supervise_cdc("cdc_test", operation, stop, monitor)
        )
        await asyncio.wait_for(retried.wait(), timeout=2)
        self.assertFalse(task.done())
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(attempts, 2)

    async def test_subsystem_failure_is_retried_without_escaping_supervisor(
        self,
    ) -> None:
        stop = asyncio.Event()
        attempts = 0
        retried = asyncio.Event()

        async def operation() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("DuckLake scan failed")
            retried.set()
            await stop.wait()

        monitor = HealthMonitor()
        monitor.dependencies_ready()
        task = asyncio.create_task(
            _supervise_subsystem("backfill", operation, stop, monitor)
        )
        await asyncio.wait_for(retried.wait(), timeout=2)
        self.assertFalse(task.done())
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    async def test_blocking_call_finishes_before_cancellation_returns(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocking() -> None:
            started.set()
            release.wait(timeout=2)

        task = asyncio.create_task(_run_blocking(blocking))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    def test_normal_close_releases_consumer(self) -> None:
        catalogue = MagicMock()
        consumer = MagicMock()
        consumer.name = "consumer"

        _close_consumer(catalogue, consumer, drop=False)

        consumer.close.assert_called_once_with(timeout=5.0, cancel=False, release=True)
        consumer.client.cdc_consumer_force_release.assert_not_called()

    def test_crawl_planner_consumer_owns_a_dedicated_connection(self) -> None:
        catalogue = SimpleNamespace(
            lake=MagicMock(),
            connection=MagicMock(),
            config=SimpleNamespace(schema="main"),
        )
        with patch("materialization.live.DMLConsumer") as consumer_type:
            consumer = consumer_type.return_value
            consumer.open.return_value = consumer

            opened = _open_crawl_planner_consumer(catalogue, 42, "use")

        self.assertIs(opened, consumer)
        consumer_type.assert_called_once_with(
            catalogue.lake,
            "atlas-crawl-materialization-planner",
            connection=catalogue.connection,
            table="main.crawls",
            mode="changes",
            start_at=42,
            on_exists="use",
            lease_policy="error",
        )
        consumer.open.assert_called_once_with()

    def test_crawl_scope_preserves_cdc_document_identity(self) -> None:
        definition = SimpleNamespace(
            id=uuid4(),
            definition_revision_id=uuid4(),
            name="page_links",
            scope_kind="crawl",
            scope_column="crawl_id",
        )

        scopes = _crawl_triggered_scopes(
            [definition],
            crawl_id="79a83bee-38a3-4dd8-8fe3-4f89a43c7f79",
            document_id="sha256:document",
        )

        self.assertEqual(len(scopes), 1)
        self.assertEqual(scopes[0].document_id, "sha256:document")

    def test_url_planner_consumes_typed_url_changes(self) -> None:
        catalogue = SimpleNamespace(
            lake=MagicMock(),
            connection=MagicMock(),
            config=SimpleNamespace(schema="main"),
        )
        with patch("materialization.live.DMLConsumer") as consumer_type:
            consumer = consumer_type.return_value
            consumer.open.return_value = consumer

            opened = _open_url_planner_consumer(catalogue, 42, "use")

        self.assertIs(opened, consumer)
        consumer_type.assert_called_once_with(
            catalogue.lake,
            "atlas-url-materialization-planner",
            connection=catalogue.connection,
            table="main.urls",
            mode="changes",
            start_at=42,
            on_exists="use",
            lease_policy="error",
        )

    def test_url_change_publishes_only_url_scopes(self) -> None:
        url_definition = SimpleNamespace(
            id=uuid4(),
            definition_revision_id=uuid4(),
            name="url_features",
            scope_kind="url",
            scope_column="url_id",
        )
        crawl_definition = SimpleNamespace(
            id=uuid4(),
            definition_revision_id=uuid4(),
            name="page_links",
            scope_kind="crawl",
            scope_column="crawl_id",
        )
        batch = SimpleNamespace(
            changes=[
                SimpleNamespace(
                    kind=SimpleNamespace(value="insert"),
                    values={"url_id": "a" * 64},
                )
            ]
        )

        scopes = _url_batch_scopes(
            [url_definition, crawl_definition],
            batch,
        )

        self.assertEqual(len(scopes), 1)
        self.assertEqual(scopes[0].scope_kind, "url")
        self.assertEqual(scopes[0].scope_id, "a" * 64)
        self.assertIsNone(scopes[0].document_id)

    def test_planner_opens_only_consumers_required_by_live_scopes(self) -> None:
        definitions = [
            SimpleNamespace(scope_kind="crawl", activation_snapshot=20),
            SimpleNamespace(scope_kind="document", activation_snapshot=10),
        ]

        self.assertEqual(
            _required_consumer_starts(definitions),
            {"crawl": 10},
        )
        definitions.append(
            SimpleNamespace(scope_kind="url", activation_snapshot=30)
        )
        self.assertEqual(
            _required_consumer_starts(definitions),
            {"crawl": 10, "url": 30},
        )

    async def test_crawl_planner_stays_idle_without_live_definitions(self) -> None:
        stop = asyncio.Event()

        async def stop_after_idle(*_args) -> None:
            stop.set()

        monitor = HealthMonitor()
        monitor.dependencies_ready()
        with (
            patch("materialization.live.active_definitions", return_value=[]),
            patch("materialization.live._wait", new=stop_after_idle),
            patch(
                "materialization.live._run_active_crawl_planner",
                new=AsyncMock(),
            ) as run_active,
        ):
            await run_crawl_planner(MagicMock(), stop, monitor)

        run_active.assert_not_awaited()
        self.assertEqual(monitor.status(), (True, "ready"))


if __name__ == "__main__":
    unittest.main()
