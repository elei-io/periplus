from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg

from repository.ingestion.worker import (
    _commit_batch_isolated,
    _dependency_probe,
    _prepare_ingestion_job,
)
from repository.ingestion.health import HealthMonitor
from runtime.resource_governor import DURABLE_RESOURCE_WAIT


@asynccontextmanager
async def _admitted(*_args, **_kwargs):
    yield


class IngestionFenceFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_probe_does_not_request_work_capacity(self) -> None:
        client = SimpleNamespace(flush=AsyncMock())
        ingestor = SimpleNamespace(probe=MagicMock())
        monitor = HealthMonitor()

        async def stop_after_probe(_interval: float) -> None:
            raise asyncio.CancelledError

        with (
            patch(
                "repository.ingestion.worker.get_float",
                side_effect=lambda name: {
                    "ATLAS_INGESTION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS": 1.0,
                    "ATLAS_INGESTION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS": 1.0,
                }[name],
            ),
            patch(
                "repository.ingestion.worker.asyncio.sleep",
                new=stop_after_probe,
            ),
            patch(
                "repository.ingestion.worker.resource_permits",
                side_effect=AssertionError("health must not request a work permit"),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await _dependency_probe(client, ingestor, monitor)

        client.flush.assert_awaited_once()
        ingestor.probe.assert_called_once_with()
        self.assertEqual(monitor.status(), (True, "ready"))

    async def test_preparation_heartbeats_while_waiting_for_capacity(self) -> None:
        permit_waiting = asyncio.Event()
        release_permit = asyncio.Event()
        acquire_timeouts: list[float | None] = []

        @asynccontextmanager
        async def delayed_permit(*_args, acquire_timeout=None, **_kwargs):
            acquire_timeouts.append(acquire_timeout)
            permit_waiting.set()
            await release_permit.wait()
            yield

        message = SimpleNamespace(in_progress=AsyncMock())
        crawl = SimpleNamespace()
        job = SimpleNamespace(
            request_id="request",
            crawl=crawl,
            urls=(),
            crawl_attempts=(),
            crawl_steps=(),
        )
        ingestor = SimpleNamespace(prepare_from_raw=MagicMock(return_value="prepared"))
        with (
            patch(
                "repository.ingestion.worker.resource_permits",
                new=delayed_permit,
            ),
            patch(
                "repository.ingestion.worker.object_request",
                return_value=object(),
            ),
        ):
            task = asyncio.create_task(
                _prepare_ingestion_job(
                    ingestor,
                    message,
                    job,
                    {},
                    asyncio.Lock(),
                    MagicMock(),
                )
            )
            await asyncio.wait_for(permit_waiting.wait(), timeout=1)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            message.in_progress.assert_awaited()
            release_permit.set()
            self.assertEqual(await asyncio.wait_for(task, timeout=1), "prepared")

        self.assertEqual(acquire_timeouts, [DURABLE_RESOURCE_WAIT])
        ingestor.prepare_from_raw.assert_called_once_with(
            crawl=crawl,
            urls=(),
            crawl_attempts=(),
            crawl_steps=(),
            known_documents={},
        )

    async def test_fence_unavailability_retries_the_batch_without_terminal_failures(
        self,
    ) -> None:
        ingestor = MagicMock()
        ingestor.commit_prepared_batch.side_effect = psycopg.OperationalError(
            "catalogue unavailable"
        )
        jobs = [
            SimpleNamespace(request_id="first", enqueued_at=datetime.now(UTC)),
            SimpleNamespace(request_id="second", enqueued_at=datetime.now(UTC)),
        ]
        messages = [
            SimpleNamespace(
                metadata=SimpleNamespace(num_delivered=3),
                nak=AsyncMock(),
            )
            for _value in jobs
        ]
        prepared = [
            SimpleNamespace(element_count=10, staged_bytes=100),
            SimpleNamespace(element_count=20, staged_bytes=200),
        ]

        with (
            patch(
                "repository.ingestion.worker.resource_permits",
                _admitted,
            ),
            patch(
                "repository.ingestion.worker.operation_leases",
                _admitted,
            ),
            patch(
                "repository.ingestion.worker.catalogue_request",
                return_value=object(),
            ),
        ):
            await _commit_batch_isolated(
                MagicMock(),
                MagicMock(),
                ingestor,
                jobs,
                messages,
                prepared,
                asyncio.Lock(),
                MagicMock(),
                MagicMock(),
            )

        ingestor.discard_prepared.assert_called_once_with(prepared)
        for message in messages:
            message.nak.assert_awaited_once_with(delay=4)

    async def test_success_is_recorded_after_commit_and_before_ack(self) -> None:
        events: list[str] = []
        ingestor = MagicMock()

        def commit(_prepared, **_kwargs):
            events.append("ducklake_commit")
            return [SimpleNamespace()]

        ingestor.commit_prepared_batch.side_effect = commit
        job = SimpleNamespace(
            request_id="request",
            enqueued_at=datetime.now(UTC),
        )
        message = SimpleNamespace(
            metadata=SimpleNamespace(num_delivered=1),
            ack=AsyncMock(side_effect=lambda: events.append("ack")),
        )
        prepared = [SimpleNamespace(element_count=10, staged_bytes=100)]

        async def store_result(*_args, **_kwargs):
            events.append("terminal_state")

        with (
            patch(
                "repository.ingestion.worker.resource_permits",
                _admitted,
            ),
            patch(
                "repository.ingestion.worker.operation_leases",
                _admitted,
            ),
            patch(
                "repository.ingestion.worker.catalogue_request",
                return_value=object(),
            ),
            patch(
                "repository.ingestion.worker.store_ingestion_response",
                new=store_result,
            ),
        ):
            await _commit_batch_isolated(
                MagicMock(),
                MagicMock(),
                ingestor,
                [job],
                [message],
                prepared,
                asyncio.Lock(),
                MagicMock(),
                MagicMock(),
            )

        self.assertEqual(events, ["ducklake_commit", "terminal_state", "ack"])


if __name__ == "__main__":
    unittest.main()
