from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from repository.ducklake import CatalogueConflictError
from repository.pipeline import IngestionWorkerConfig
from repository.worker import (
    _batch_reached_limit,
    _commit_batch_isolated,
    _dead_letter_or_retry,
    _retry_or_fail,
    _would_exceed_batch,
)


class RepositoryWorkerBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = IngestionWorkerConfig(
            max_items=3,
            max_element_rows=10,
            max_staged_bytes=100,
            max_wait_seconds=0.1,
        )

    def test_flushes_before_a_prepared_page_would_cross_batch_limits(self) -> None:
        prepared = [SimpleNamespace(element_count=6, staged_bytes=40)]
        incoming = SimpleNamespace(element_count=5, staged_bytes=20)

        self.assertTrue(
            _would_exceed_batch(prepared, incoming, config=self.config)
        )

    def test_single_oversized_page_is_bounded_by_document_limits(self) -> None:
        incoming = SimpleNamespace(element_count=11, staged_bytes=101)

        self.assertFalse(_would_exceed_batch([], incoming, config=self.config))
        self.assertTrue(_batch_reached_limit([incoming], config=self.config))

    def test_item_count_flushes_without_waiting_for_the_next_fetch(self) -> None:
        prepared = [
            SimpleNamespace(element_count=1, staged_bytes=1)
            for _ in range(3)
        ]

        self.assertTrue(_batch_reached_limit(prepared, config=self.config))


class RepositoryWorkerIsolationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _items(count: int):
        jobs = [
            SimpleNamespace(enqueued_at=datetime.now(UTC)) for _ in range(count)
        ]
        messages = [
            SimpleNamespace(metadata=SimpleNamespace(num_delivered=1))
            for _ in range(count)
        ]
        prepared = [
            SimpleNamespace(element_count=1, staged_bytes=1) for _ in range(count)
        ]
        return jobs, messages, prepared

    async def test_infrastructure_failure_retries_batch_without_bisection(self) -> None:
        jobs, messages, prepared = self._items(8)
        ingestor = SimpleNamespace(
            commit_prepared_batch=Mock(side_effect=ConnectionError("catalogue down")),
            discard_prepared=Mock(),
        )

        with patch(
            "repository.worker._retry_or_fail", new_callable=AsyncMock
        ) as retry:
            await _commit_batch_isolated(
                Mock(), Mock(), ingestor, jobs, messages, prepared
            )

        self.assertEqual(ingestor.commit_prepared_batch.call_count, 1)
        ingestor.discard_prepared.assert_called_once_with(prepared)
        self.assertEqual(retry.await_count, 8)

    async def test_deterministic_catalogue_failure_is_bisected(self) -> None:
        jobs, messages, prepared = self._items(4)
        ingestor = SimpleNamespace(
            commit_prepared_batch=Mock(
                side_effect=CatalogueConflictError("conflicting entry")
            ),
            discard_prepared=Mock(),
        )

        with patch(
            "repository.worker._retry_or_fail", new_callable=AsyncMock
        ) as retry:
            await _commit_batch_isolated(
                Mock(), Mock(), ingestor, jobs, messages, prepared
            )

        self.assertEqual(ingestor.commit_prepared_batch.call_count, 7)
        self.assertEqual(ingestor.discard_prepared.call_count, 4)
        self.assertEqual(retry.await_count, 4)


class RepositoryWorkerTerminalStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_work_is_retained_when_dead_letter_publish_fails(self) -> None:
        message = SimpleNamespace(
            metadata=SimpleNamespace(num_delivered=5),
            nak=AsyncMock(),
            term=AsyncMock(),
        )
        client = SimpleNamespace(jetstream=Mock(return_value=Mock()))

        with patch(
            "repository.worker.publish_dead_letter",
            new=AsyncMock(side_effect=ConnectionError("NATS unavailable")),
        ):
            await _dead_letter_or_retry(
                client,
                message,
                SimpleNamespace(request_id="crawl-1"),
                "failed",
            )

        message.nak.assert_awaited_once_with(delay=30)
        message.term.assert_not_awaited()

    async def test_terminal_work_is_removed_after_dead_letter_puback(self) -> None:
        message = SimpleNamespace(
            metadata=SimpleNamespace(num_delivered=5),
            nak=AsyncMock(),
            term=AsyncMock(),
        )
        client = SimpleNamespace(jetstream=Mock(return_value=Mock()))

        with patch(
            "repository.worker.publish_dead_letter", new_callable=AsyncMock
        ) as publish:
            await _dead_letter_or_retry(
                client,
                message,
                SimpleNamespace(request_id="crawl-1"),
                "failed",
            )

        publish.assert_awaited_once()
        message.term.assert_awaited_once()
        message.nak.assert_not_awaited()

    async def test_terminal_failure_reconciles_ducklake_to_success(self) -> None:
        result = object()
        crawl = object()
        job = SimpleNamespace(
            kind="crawl",
            crawl=crawl,
            run_usage=None,
        )
        ingestor = SimpleNamespace(
            reconcile_crawl_commit=Mock(return_value=result),
        )
        message = SimpleNamespace(
            metadata=SimpleNamespace(num_delivered=5),
            ack=AsyncMock(),
            term=AsyncMock(),
            nak=AsyncMock(),
        )
        durable = SimpleNamespace(status="succeeded")

        with (
            patch("repository.worker.max_delivery_attempts", return_value=5),
            patch(
                "repository.worker.store_ingestion_response",
                new_callable=AsyncMock,
                return_value=durable,
            ) as store,
            patch("repository.worker._notify", new_callable=AsyncMock),
        ):
            await _retry_or_fail(
                Mock(), Mock(), ingestor, message, job, RuntimeError("late failure")
            )

        ingestor.reconcile_crawl_commit.assert_called_once_with(
            crawl=crawl,
            run_usage=None,
        )
        self.assertIs(store.await_args.kwargs["result"], result)
        self.assertIsNone(store.await_args.kwargs["error"])
        message.ack.assert_awaited_once()
        message.term.assert_not_awaited()

    async def test_reconciliation_outage_keeps_delivery_live(self) -> None:
        job = SimpleNamespace(kind="crawl", crawl=object(), run_usage=None)
        ingestor = SimpleNamespace(
            reconcile_crawl_commit=Mock(side_effect=ConnectionError("catalogue down")),
        )
        message = SimpleNamespace(
            metadata=SimpleNamespace(num_delivered=5),
            ack=AsyncMock(),
            term=AsyncMock(),
            nak=AsyncMock(),
        )

        with (
            patch("repository.worker.max_delivery_attempts", return_value=5),
            patch(
                "repository.worker.store_ingestion_response",
                new_callable=AsyncMock,
            ) as store,
        ):
            await _retry_or_fail(
                Mock(), Mock(), ingestor, message, job, RuntimeError("write failed")
            )

        store.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=30)
        message.ack.assert_not_awaited()
        message.term.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
