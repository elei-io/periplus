from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from periplus.crawl.runtime.graph_navigation import _process_edge
from periplus.crawl.runtime.graph_queue import EdgeWork
from periplus.crawl.runtime.graph_runs import EdgeEvaluationRetryable


class GraphNavigationRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retryable_edge_failure_is_naked_without_settlement(self) -> None:
        work = EdgeWork(
            graph_run_id=uuid4(),
            crawl_request_id=uuid4(),
            crawl_id=uuid4(),
            edge_id=uuid4(),
            generation=1,
            navigation={
                "object_name": "runtime/navigation/document.arrow",
                "sha256": "a" * 64,
                "schema_version": 1,
                "recipe": "page-links-v1",
                "row_count": 1,
                "byte_size": 1,
            },
        )
        message = SimpleNamespace(
            data=work.model_dump_json().encode(),
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
            in_progress=AsyncMock(),
        )
        runs = SimpleNamespace(
            get_run=AsyncMock(
                return_value=SimpleNamespace(status="running")
            )
        )
        requests = SimpleNamespace(
            get_request=AsyncMock(
                return_value=SimpleNamespace(generation=1)
            ),
            get_edge_evaluation=AsyncMock(return_value=None),
        )
        executor = Mock()

        with (
            patch(
                "periplus.crawl.runtime.graph_navigation.EdgeUrlExecutor",
                return_value=executor,
            ),
            patch(
                "periplus.crawl.runtime.graph_navigation.evaluate_edge",
                new=AsyncMock(
                    side_effect=EdgeEvaluationRetryable("pool exhausted")
                ),
            ),
        ):
            await _process_edge(
                message,
                runs,
                requests,
                None,
                None,
                object(),
                Mock(),
            )

        message.nak.assert_awaited_once_with(delay=1)
        message.ack.assert_not_awaited()
        message.term.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
