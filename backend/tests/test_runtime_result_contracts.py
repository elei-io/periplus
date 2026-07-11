from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from actions.index.schemas import IndexLink, IndexOutput
from actions.search.schemas import SearchOutput, SearchResult
from runtime.executor import execute_task


def _run(primitive: str, input_json: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        task_id=uuid4(),
        task_revision=1,
        primitive=primitive,
        attempt=1,
        data_schema_id=None,
        crawl_policy_snapshots_json=[],
        input_json=input_json,
    )


class RuntimeResultContractTests(IsolatedAsyncioTestCase):
    async def test_search_stores_the_typed_action_output(self) -> None:
        output = SearchOutput(
            results=[
                SearchResult(
                    url="https://example.com/result",
                    title="Example",
                    description="A result.",
                )
            ]
        )
        with patch("runtime.executor.search", AsyncMock(return_value=output)):
            execution = await execute_task(
                SimpleNamespace(),
                _run("search", {"query": "example"}),
            )

        self.assertEqual(execution.output_json, output.model_dump(mode="json"))
        self.assertEqual(execution.warnings, [])

    async def test_index_stores_the_complete_typed_action_output(self) -> None:
        output = IndexOutput(
            pages=1,
            failed_pages=0,
            discovered_links=1,
            result_links=1,
            links=[
                IndexLink(
                    source_url="https://example.com",
                    url="https://example.com/about",
                    text="About",
                    title="",
                    depth=0,
                    link_index=0,
                    internal=True,
                )
            ],
        )
        with patch("runtime.executor.index", AsyncMock(return_value=output)):
            execution = await execute_task(
                SimpleNamespace(),
                _run("index", {"url": "https://example.com"}),
            )

        self.assertEqual(execution.output_json, output.model_dump(mode="json"))
        self.assertEqual(execution.warnings, [])

