from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from repository.ingestion.worker import _consume_materialization_commits


class CatalogWorkerSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_commit_consumer_drains_without_repository_poll_delay(self) -> None:
        stop = asyncio.Event()
        handled = 0

        async def handle(*_args) -> None:
            nonlocal handled
            handled += 1
            if handled == 3:
                stop.set()

        with (
            patch(
                "repository.ingestion.worker._maintenance_active",
                AsyncMock(return_value=False),
            ),
            patch(
                "repository.ingestion.worker._commit_materialization_if_ready",
                side_effect=handle,
            ),
        ):
            await asyncio.wait_for(
                _consume_materialization_commits(
                    AsyncMock(),
                    AsyncMock(),
                    object(),
                    asyncio.Lock(),
                    None,
                    stop,
                ),
                timeout=0.25,
            )

        self.assertEqual(handled, 3)
