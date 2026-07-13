from __future__ import annotations

import asyncio
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from repository.ingestion.worker import (
    _commit_materialization_entries,
    _dead_letter_or_retry,
    _materialization_batch_due,
    _settle_graph_ingestion_failure,
)


class CatalogWorkerSchedulingTests(unittest.TestCase):
    def test_partial_materialization_batch_flushes_at_deadline(self) -> None:
        entries = [(object(), SimpleNamespace(file_bytes=10), object())]
        self.assertFalse(
            _materialization_batch_due(
                entries,
                started_at=10.0,
                now=14.9,
                max_items=100,
                max_bytes=1000,
                max_wait=5.0,
            )
        )
        self.assertTrue(
            _materialization_batch_due(
                entries,
                started_at=10.0,
                now=15.0,
                max_items=100,
                max_bytes=1000,
                max_wait=5.0,
            )
        )


class CatalogWorkerSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_ingestion_failure_settles_runtime_request(self) -> None:
        jetstream = object()
        client = SimpleNamespace(jetstream=lambda: jetstream)
        request_id = uuid4()
        job = SimpleNamespace(crawl=SimpleNamespace(crawl_request_id=request_id))
        runs, requests, workers, progress = object(), object(), object(), object()

        with (
            patch(
                "repository.ingestion.worker.ensure_graph_storage",
                new=AsyncMock(return_value=(runs, requests, workers)),
            ),
            patch(
                "repository.ingestion.worker.ensure_graph_progress_storage",
                new=AsyncMock(return_value=progress),
            ),
            patch(
                "repository.ingestion.worker.settle_request",
                new=AsyncMock(),
            ) as settle,
        ):
            await _settle_graph_ingestion_failure(client, job, "DuckLake unavailable")

        settle.assert_awaited_once_with(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request_id,
            status="failed",
            error="Repository ingestion failed: DuckLake unavailable",
            failure_stage="enrichment",
        )

    async def test_dead_letter_settles_graph_request_before_terminating_work(
        self,
    ) -> None:
        message = SimpleNamespace(
            term=AsyncMock(),
            nak=AsyncMock(),
            metadata=SimpleNamespace(num_delivered=5),
        )
        job = SimpleNamespace()
        client = SimpleNamespace(jetstream=lambda: object())

        with (
            patch(
                "repository.ingestion.worker._settle_graph_ingestion_failure",
                new=AsyncMock(),
            ) as settle,
            patch(
                "repository.ingestion.worker.publish_dead_letter",
                new=AsyncMock(),
            ) as publish,
        ):
            await _dead_letter_or_retry(client, message, job, "catalogue unavailable")

        settle.assert_awaited_once_with(client, job, "catalogue unavailable")
        publish.assert_awaited_once()
        message.term.assert_awaited_once()
        message.nak.assert_not_awaited()

    async def test_dead_letter_retains_work_until_graph_failure_is_settled(
        self,
    ) -> None:
        message = SimpleNamespace(term=AsyncMock(), nak=AsyncMock())
        job = SimpleNamespace()
        client = SimpleNamespace(jetstream=lambda: object())

        with (
            patch(
                "repository.ingestion.worker._settle_graph_ingestion_failure",
                new=AsyncMock(side_effect=OSError("NATS unavailable")),
            ),
            patch(
                "repository.ingestion.worker.publish_dead_letter",
                new=AsyncMock(),
            ) as publish,
        ):
            await _dead_letter_or_retry(client, message, job, "catalogue unavailable")

        publish.assert_not_awaited()
        message.term.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=30)

    async def test_durable_commit_is_nacked_when_fanout_settlement_is_unavailable(
        self,
    ) -> None:
        message = SimpleNamespace(ack=AsyncMock(), nak=AsyncMock())
        job = SimpleNamespace(
            scope=SimpleNamespace(operation_id="operation", scope_id="scope")
        )
        heartbeat = asyncio.create_task(asyncio.sleep(60))

        with (
            patch(
                "repository.ingestion.worker.commit_scope_batch",
                return_value={"operation": "committed"},
            ),
            patch(
                "repository.ingestion.worker.operation_locks",
                return_value=nullcontext(),
            ),
            patch(
                "repository.ingestion.worker._settle_crawl_fanouts",
                new=AsyncMock(side_effect=OSError("MinIO unavailable")),
            ),
        ):
            await _commit_materialization_entries(
                SimpleNamespace(),
                SimpleNamespace(),
                [(message, job, heartbeat)],
                asyncio.Lock(),
            )

        message.ack.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=5)
        self.assertTrue(heartbeat.cancelled())

    async def test_materialization_settlement_yields_catalogue_between_entries(
        self,
    ) -> None:
        lock = asyncio.Lock()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        order: list[str] = []
        settlement_count = 0

        async def settle(*_args) -> None:
            nonlocal settlement_count
            settlement_count += 1
            order.append(f"settlement-{settlement_count}-start")
            if settlement_count == 1:
                first_started.set()
                await release_first.wait()
            order.append(f"settlement-{settlement_count}-end")

        entries = []
        for index in range(2):
            entries.append(
                (
                    SimpleNamespace(ack=AsyncMock(), nak=AsyncMock()),
                    SimpleNamespace(
                        scope=SimpleNamespace(
                            operation_id=f"operation-{index}",
                            scope_id=f"scope-{index}",
                        )
                    ),
                    asyncio.create_task(asyncio.sleep(60)),
                )
            )

        async def ingestion_read() -> None:
            await first_started.wait()
            async with lock:
                order.append("ingestion")

        with (
            patch(
                "repository.ingestion.worker.commit_scope_batch",
                return_value={"operations": "committed"},
            ),
            patch(
                "repository.ingestion.worker.operation_locks",
                return_value=nullcontext(),
            ),
            patch(
                "repository.ingestion.worker._settle_crawl_fanouts",
                new=settle,
            ),
        ):
            commit = asyncio.create_task(
                _commit_materialization_entries(
                    SimpleNamespace(), SimpleNamespace(), entries, lock
                )
            )
            contender = asyncio.create_task(ingestion_read())
            await first_started.wait()
            await asyncio.sleep(0)
            release_first.set()
            await asyncio.gather(commit, contender)

        self.assertLess(order.index("ingestion"), order.index("settlement-2-start"))
