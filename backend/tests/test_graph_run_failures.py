from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from api.routers.graph_runs import failure_summary
from runtime.graph_queue import GraphRunFailureGroup
from runtime.graph_runs import _updated_failure_groups


class GraphRunFailureApiTests(unittest.TestCase):
    def test_failure_groups_remain_bounded_with_an_overflow_group(self) -> None:
        captured_at = datetime(2026, 7, 21, tzinfo=UTC)
        run = SimpleNamespace(
            failure_groups=tuple(
                GraphRunFailureGroup(
                    failure_stage="navigation",
                    failure_code=f"failure_{index}",
                    count=1,
                    example_url=f"https://example.com/{index}",
                    last_occurred_at=captured_at,
                )
                for index in range(32)
            )
        )

        groups = _updated_failure_groups(
            run,
            request=SimpleNamespace(url="https://example.com/new"),
            failure_stage="connection",
            failure_code="new_failure",
            status_code=None,
            detail="new detail",
            occurred_at=captured_at + timedelta(seconds=1),
        )

        self.assertEqual(len(groups), 32)
        self.assertEqual(groups[-1].failure_stage, "other")
        self.assertEqual(groups[-1].failure_code, "other_failures")
        self.assertEqual(groups[-1].count, 2)

    def test_returns_bounded_runtime_groups_without_crawl_history_lookups(
        self,
    ) -> None:
        async def scenario() -> None:
            captured_at = datetime(2026, 7, 21, tzinfo=UTC)
            run = SimpleNamespace(
                failed_request_count=4,
                failure_groups=(
                    GraphRunFailureGroup(
                        failure_stage="edge",
                        failure_code="edge_evaluation_failed",
                        count=1,
                        example_url="https://example.com/edge",
                        example_detail="query failed",
                        last_occurred_at=captured_at,
                    ),
                    GraphRunFailureGroup(
                        failure_stage="navigation",
                        failure_code="http_status",
                        status_code=503,
                        count=3,
                        example_url="https://example.com/unavailable",
                        example_detail="Page returned HTTP 503",
                        last_occurred_at=captured_at + timedelta(seconds=1),
                    ),
                ),
            )
            client = SimpleNamespace(drain=AsyncMock())
            with (
                patch(
                    "api.routers.graph_runs._storage",
                    AsyncMock(
                        return_value=(client, object(), object(), object())
                    ),
                ),
                patch(
                    "api.routers.graph_runs.get_graph_run",
                    AsyncMock(return_value=run),
                ),
            ):
                result = await failure_summary(uuid4())

            self.assertEqual(result.total, 4)
            self.assertEqual(len(result.items), 2)
            self.assertEqual(result.items[0].failure_code, "http_status")
            self.assertEqual(result.items[0].status_code, 503)
            self.assertEqual(result.items[0].count, 3)
            self.assertEqual(
                result.items[0].example_url,
                "https://example.com/unavailable",
            )
            client.drain.assert_awaited_once_with()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
