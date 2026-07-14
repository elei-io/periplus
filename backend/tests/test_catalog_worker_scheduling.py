from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from materialization.writer import (
    _commit_fanout_plan,
    _commit_materialization_entries,
    _materialization_batch_due,
    _settle_crawl_fanouts,
)
from repository.catalogue.fanout import MissingCrawlMaterializationFanout
from repository.ingestion.worker import (
    _dead_letter_or_retry,
    _settle_graph_ingestion_failure,
)


class MaterializationWriterSchedulingTests(unittest.TestCase):
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


class WorkerSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_fanout_plan_does_not_poison_committed_scope(self) -> None:
        crawl_id = uuid4()
        scope = SimpleNamespace(
            materialization_id=uuid4(),
            definition_revision_id=uuid4(),
            scope_kind="crawl",
            scope_id=str(crawl_id),
        )

        @asynccontextmanager
        async def lease(*_args, **_kwargs):
            yield

        with (
            patch(
                "materialization.writer.CrawlMaterializationFanoutStore.crawls_for_scope",
                return_value=[crawl_id],
            ),
            patch("materialization.writer.operation_leases", new=lease),
            patch(
                "materialization.writer._refresh_crawl_fanout_fenced",
                side_effect=MissingCrawlMaterializationFanout("not planned"),
            ),
        ):
            await _settle_crawl_fanouts(
                SimpleNamespace(), scope, asyncio.Lock(), SimpleNamespace()
            )

    async def test_fanout_plan_reconciles_coverage_before_publishing_work(self) -> None:
        crawl_id = uuid4()
        scope = SimpleNamespace(
            materialization_id=uuid4(),
            definition_revision_id=uuid4(),
            scope_kind="crawl",
            scope_id=str(crawl_id),
            operation_id="operation",
            model_dump_json=lambda: "{}",
        )
        plan = SimpleNamespace(crawl_id=crawl_id, scopes=[scope])
        jetstream = SimpleNamespace(publish=AsyncMock())

        with (
            patch("materialization.writer.operation_lock", return_value=nullcontext()),
            patch(
                "materialization.writer.run_with_catalogue_retry",
                side_effect=lambda operation, **_kwargs: operation(),
            ),
            patch(
                "materialization.writer.CrawlMaterializationFanoutStore"
            ) as store_type,
        ):
            await _commit_fanout_plan(jetstream, SimpleNamespace(), plan)

        store_type.return_value.plan.assert_called_once()
        store_type.return_value.refresh.assert_called_once_with(crawl_id)
        jetstream.publish.assert_awaited_once()

    async def test_terminal_ingestion_failure_settles_runtime_request(self) -> None:
        jetstream = object()
        client = SimpleNamespace(jetstream=lambda: jetstream)
        request_id = uuid4()
        job = SimpleNamespace(
            crawl=SimpleNamespace(crawl_request_id=request_id, purpose="use")
        )
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
        message = SimpleNamespace(
            ack=AsyncMock(),
            nak=AsyncMock(),
            metadata=SimpleNamespace(num_delivered=1),
        )
        job = SimpleNamespace(
            scope=SimpleNamespace(operation_id="operation", scope_id="scope"),
            staging_key="staging.arrow",
        )
        heartbeat = asyncio.create_task(asyncio.sleep(60))

        @asynccontextmanager
        async def lease(*_args, **_kwargs):
            yield

        with (
            patch(
                "materialization.writer.commit_scope_batch",
                return_value={"operation": "committed"},
            ),
            patch(
                "materialization.writer.catalogue_operation_locks",
                return_value=nullcontext(),
            ),
            patch("materialization.writer.operation_leases", new=lease),
            patch(
                "materialization.writer.run_with_catalogue_retry",
                side_effect=lambda operation, **_kwargs: operation(),
            ),
            patch(
                "materialization.writer._settle_crawl_fanouts",
                new=AsyncMock(side_effect=OSError("MinIO unavailable")),
            ),
        ):
            await _commit_materialization_entries(
                SimpleNamespace(),
                SimpleNamespace(),
                [(message, job, heartbeat)],
                asyncio.Lock(),
                SimpleNamespace(),
            )

        message.ack.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=5)
        self.assertTrue(heartbeat.cancelled())

    async def test_exhausted_settlement_is_dead_lettered_and_terminated(self) -> None:
        message = SimpleNamespace(
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
            metadata=SimpleNamespace(num_delivered=5),
        )
        scope = SimpleNamespace(
            operation_id="operation",
            scope_id="scope",
            model_dump=lambda: {},
        )
        job = SimpleNamespace(scope=scope, staging_key="staging.arrow")
        heartbeat = asyncio.create_task(asyncio.sleep(60))
        jetstream = SimpleNamespace(publish=AsyncMock())

        @asynccontextmanager
        async def lease(*_args, **_kwargs):
            yield

        with (
            patch(
                "materialization.writer.commit_scope_batch",
                return_value={"operation": "committed"},
            ),
            patch(
                "materialization.writer.catalogue_operation_locks",
                return_value=nullcontext(),
            ),
            patch("materialization.writer.operation_leases", new=lease),
            patch(
                "materialization.writer.run_with_catalogue_retry",
                side_effect=lambda operation, **_kwargs: operation(),
            ),
            patch(
                "materialization.writer._settle_crawl_fanouts",
                new=AsyncMock(side_effect=OSError("MinIO unavailable")),
            ),
            patch("materialization.writer.get_int", return_value=5),
            patch("materialization.writer.MaterializationDeadLetter") as dead_letter,
        ):
            dead_letter.return_value.model_dump_json.return_value = "{}"
            await _commit_materialization_entries(
                jetstream,
                SimpleNamespace(),
                [(message, job, heartbeat)],
                asyncio.Lock(),
                SimpleNamespace(),
            )

        jetstream.publish.assert_awaited_once()
        message.term.assert_awaited_once()
        message.nak.assert_not_awaited()
        message.ack.assert_not_awaited()
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

        @asynccontextmanager
        async def lease(*_args, **_kwargs):
            yield

        async def ingestion_read() -> None:
            await first_started.wait()
            async with lock:
                order.append("ingestion")

        with (
            patch(
                "materialization.writer.commit_scope_batch",
                return_value={"operations": "committed"},
            ),
            patch(
                "materialization.writer.catalogue_operation_locks",
                return_value=nullcontext(),
            ),
            patch("materialization.writer.operation_leases", new=lease),
            patch(
                "materialization.writer.run_with_catalogue_retry",
                side_effect=lambda operation, **_kwargs: operation(),
            ),
            patch(
                "materialization.writer._settle_crawl_fanouts",
                new=settle,
            ),
        ):
            commit = asyncio.create_task(
                _commit_materialization_entries(
                    SimpleNamespace(),
                    SimpleNamespace(),
                    entries,
                    lock,
                    SimpleNamespace(),
                )
            )
            contender = asyncio.create_task(ingestion_read())
            await first_started.wait()
            await asyncio.sleep(0)
            release_first.set()
            await asyncio.gather(commit, contender)

        self.assertLess(order.index("ingestion"), order.index("settlement-2-start"))
