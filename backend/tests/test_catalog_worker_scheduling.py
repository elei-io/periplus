from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from repository.ingestion.worker import (
    _dead_letter_or_retry,
    _settle_graph_ingestion_failure,
)


class WorkerSettlementTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
