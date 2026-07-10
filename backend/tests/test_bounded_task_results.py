from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

from actions.shared.data_schema.schemas import SchemaOutput
from tasks.executor import (
    _bounded_result,
    _execute_crawl_primitive,
    _execute_primitive,
)


class BoundedTaskResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_no_store_does_not_open_catalogue(self) -> None:
        run = SimpleNamespace(
            id=uuid4(),
            input_json={"cache": {"mode": "no_store"}},
            primitive="index",
        )

        with patch("tasks.executor._has_durable_run_usage") as lookup:
            result = await _bounded_result(run, counts={"pages": 1})

        lookup.assert_not_called()
        self.assertIsNone(result["catalogue"])

    async def test_summary_size_does_not_scale_with_result_count(self) -> None:
        run_id = uuid4()
        run = SimpleNamespace(
            id=run_id,
            primitive="index",
        )

        with patch("tasks.executor._has_durable_run_usage", return_value=True):
            result = await _bounded_result(run, counts={"links": 100_000_000})

        self.assertEqual(result["counts"], {"links": 100_000_000})
        self.assertEqual(result["catalogue"], {"run_id": str(run_id)})
        self.assertNotIn("links", result)
        self.assertLess(len(json.dumps(result)), 256)

    async def test_summary_omits_catalogue_when_run_has_no_durable_usage(self) -> None:
        run = SimpleNamespace(
            id=uuid4(),
            # This can be policy-derived no_store; the request itself need not say so.
            input_json={"urls": ["https://example.com"]},
            primitive="crawl",
        )

        with patch("tasks.executor._has_durable_run_usage", return_value=False):
            result = await _bounded_result(run, counts={"requested_urls": 1})

        self.assertIsNone(result["catalogue"])


class BoundedPrimitiveExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_execution_uses_frozen_run_input_and_returns_summary(self) -> None:
        task = SimpleNamespace(
            primitive="index",
            input_json={"url": "https://edited.example", "prompt": "edited"},
        )
        run = SimpleNamespace(
            id=uuid4(),
            primitive="schema",
            input_json={"url": "https://queued.example", "prompt": "queued"},
            task=task,
        )
        generated = SchemaOutput(
            schema_id="schema-1",
            schema_type="css",
            extraction_schema={"baseSelector": "body", "fields": []},
        )
        schema_mock = AsyncMock(return_value=generated)
        with (
            patch("tasks.executor.schema", new=schema_mock),
            patch("tasks.executor._has_durable_run_usage", return_value=True),
        ):
            execution = await _execute_primitive(object(), run)  # type: ignore[arg-type]

        schema_mock.assert_awaited_once_with(
            url="https://queued.example",
            prompt="queued",
            progress_reporter=None,
            session=ANY,
            task_run_id=run.id,
        )
        self.assertEqual(execution.response_json["counts"], {"schemas": 1})
        self.assertNotIn("schema", execution.response_json)

    async def test_crawl_execution_uses_frozen_run_input(self) -> None:
        run = SimpleNamespace(
            id=uuid4(),
            primitive="crawl",
            input_json={"urls": ["https://queued.example"]},
            task=SimpleNamespace(
                primitive="index",
                input_json={"urls": ["https://edited.example"]},
            ),
        )
        output = SimpleNamespace(
            pages=[],
            stats=SimpleNamespace(requested_urls=1, succeeded=1, failed=0),
        )
        crawl_mock = AsyncMock(return_value=output)

        with (
            patch("tasks.executor.crawl", new=crawl_mock),
            patch("tasks.executor._has_durable_run_usage", return_value=True),
        ):
            await _execute_crawl_primitive(object(), run)  # type: ignore[arg-type]

        crawl_mock.assert_awaited_once_with(
            urls=["https://queued.example"],
            progress_reporter=None,
            session=ANY,
            task_run_id=run.id,
            retain_pages=False,
            include_links=False,
        )


if __name__ == "__main__":
    unittest.main()
