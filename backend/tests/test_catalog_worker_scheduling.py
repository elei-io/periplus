from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from repository.ingestion.worker import _dead_letter_or_retry


class WorkerSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_dead_letter_records_catalogue_failure_without_graph_settlement(
        self,
    ) -> None:
        message = SimpleNamespace(
            term=AsyncMock(),
            nak=AsyncMock(),
            metadata=SimpleNamespace(num_delivered=5),
        )
        job = SimpleNamespace()
        client = SimpleNamespace(jetstream=lambda: object())

        with patch(
            "repository.ingestion.worker.publish_dead_letter",
            new=AsyncMock(),
        ) as publish:
            await _dead_letter_or_retry(
                client, message, job, "catalogue unavailable", 5
            )

        publish.assert_awaited_once()
        message.term.assert_awaited_once()
        message.nak.assert_not_awaited()

    async def test_dead_letter_retains_work_until_failure_is_durable(
        self,
    ) -> None:
        message = SimpleNamespace(term=AsyncMock(), nak=AsyncMock())
        job = SimpleNamespace()
        client = SimpleNamespace(jetstream=lambda: object())

        with patch(
            "repository.ingestion.worker.publish_dead_letter",
            new=AsyncMock(side_effect=OSError("NATS unavailable")),
        ) as publish:
            await _dead_letter_or_retry(
                client, message, job, "catalogue unavailable", 5
            )

        publish.assert_awaited_once()
        message.term.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=30)


if __name__ == "__main__":
    unittest.main()
