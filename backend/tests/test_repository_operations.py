from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api.app import app
from repository.admin import DeadLetterList, DeadLetterRecord
from repository.queue import DeadLetterEntry, IngestionJob


def _record() -> DeadLetterRecord:
    return DeadLetterRecord(
        sequence=7,
        entry=DeadLetterEntry(
            job=IngestionJob(
                request_id="manifest-1",
                reply_subject="_INBOX.test",
                enqueued_at=datetime.now(UTC),
                kind="manifest",
                run_manifest_zstd="value",
            ),
            error="invalid manifest",
            failed_at=datetime.now(UTC),
            delivery_count=5,
        ),
    )


class RepositoryOperationsTests(unittest.TestCase):
    def test_lists_dead_letters_through_http(self) -> None:
        record = _record()
        with patch(
            "api.routers.repository_operations.list_dead_letters",
            new=AsyncMock(return_value=DeadLetterList(items=[record])),
        ):
            response = TestClient(app).get(
                "/operations/repository/dead-letters", params={"limit": 10}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["sequence"], 7)

    def test_requeues_dead_letter_through_http(self) -> None:
        record = _record()
        operation = AsyncMock(return_value=record)
        with patch(
            "api.routers.repository_operations.requeue_repository_dead_letter",
            new=operation,
        ):
            response = TestClient(app).post(
                "/operations/repository/dead-letters/7/requeue"
            )

        self.assertEqual(response.status_code, 200)
        operation.assert_awaited_once_with(7)


if __name__ == "__main__":
    unittest.main()
