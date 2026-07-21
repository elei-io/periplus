from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from agents.acquisition_tools import (
    AcquisitionPlan,
    BraveSearchError,
    SeedSearchResponse,
    SeedSearchResult,
)
from agents.catalogue_search import SearchEvent, _AGENT, stream_atlas_search
from agents.catalogue_tools import (
    CatalogueQueryResult,
    CatalogueRelation,
    CatalogueTools,
)
from control.chats.schemas import ChatTurnRequest
from repository.catalogue.interactive import BufferedCatalogueResult
from repository.catalogue.quack_runtime import CatalogueQueryExecutionError
from repository.catalogue.query import CatalogueQueryError


class SearchContractTests(unittest.IsolatedAsyncioTestCase):
    def test_question_is_trimmed_and_bounded(self) -> None:
        self.assertEqual(
            ChatTurnRequest(message="What changed?").message,
            "What changed?",
        )

    def test_events_reject_transport_specific_extensions(self) -> None:
        with self.assertRaises(ValueError):
            SearchEvent.model_validate(
                {"type": "run.started", "run_id": "run", "provider_event": {}}
            )

    async def test_query_tool_executes_a_validated_two_hundred_row_query(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(catalogue_alias="atlas", catalogue_schema="main")
        )
        service = CatalogueTools(runtime, SimpleNamespace())
        result = BufferedCatalogueResult(
            query_id=uuid4(),
            columns=["page_url"],
            column_types=["string"],
            rows=[["https://example.com/"]],
        )

        with patch(
            "agents.catalogue_tools.execute_interactive_query",
            new=AsyncMock(return_value=result),
        ) as execute:
            response = await service.query("SELECT page_url FROM main.documents LIMIT 900")

        executed_sql = execute.await_args.args[1]
        self.assertIn("LIMIT 201", executed_sql)
        self.assertEqual(response.sql, executed_sql)
        self.assertEqual(response.rows, [["https://example.com/"]])

    async def test_query_tool_rejects_non_read_only_sql_before_execution(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(catalogue_alias="atlas", catalogue_schema="main")
        )
        service = CatalogueTools(runtime, SimpleNamespace())
        execute = AsyncMock()

        with (
            patch("agents.catalogue_tools.execute_interactive_query", new=execute),
            self.assertRaisesRegex(ValueError, "only SELECT queries are allowed"),
        ):
            await service.query("DELETE FROM main.documents")

        execute.assert_not_awaited()

    async def test_agent_owns_tool_loop_and_emits_stable_progress(self) -> None:
        class Tools:
            async def list_relations(self, kind: str) -> list[CatalogueRelation]:
                return [CatalogueRelation(qualified_name="main.documents", kind=kind)]

        model = TestModel(
            call_tools=["list_catalogue_relations"],
            custom_output_text="Found the documents table.",
        )
        with _AGENT.override(model=model):
            events = [
                event
                async for event in stream_atlas_search(
                    "What tables are available?",
                    Tools(),  # type: ignore[arg-type]
                    SimpleNamespace(),  # type: ignore[arg-type]
                )
            ]

        self.assertEqual(events[0].type, "run.started")
        self.assertIn("tool.started", [event.type for event in events])
        self.assertEqual(events[-1].type, "run.completed", events[-1].message)
        self.assertEqual(events[-1].summary, "Found the documents table.")

    async def test_acquisition_results_are_ephemeral_sse_evidence(self) -> None:
        class Acquisition:
            async def list_graphs(self) -> list[object]:
                return []

            async def list_schedules(self) -> list[object]:
                return []

            async def search_web(
                self, query: str, *, count: int = 10, **_kwargs: object
            ) -> SeedSearchResponse:
                return SeedSearchResponse(
                    query=query,
                    results=[
                        SeedSearchResult(
                            title="Private tool result",
                            url="https://seed.example.com/",
                            description="Ephemeral SSE evidence.",
                        )
                    ][:count],
                )

        model = TestModel(
            call_tools=["list_crawl_graphs", "list_crawl_schedules", "search_public_web"],
            custom_output_text="Use the proposed seed URL with a one-off graph run.",
        )
        with _AGENT.override(model=model):
            events = [
                event
                async for event in stream_atlas_search(
                    "Plan a crawl of the example documentation.",
                    SimpleNamespace(),  # type: ignore[arg-type]
                    Acquisition(),  # type: ignore[arg-type]
                )
            ]

        started_tools = {
            event.tool for event in events if event.type == "tool.started"
        }
        self.assertEqual(
            started_tools,
            {"list_crawl_graphs", "list_crawl_schedules", "search_public_web"},
        )
        serialized_events = "\n".join(event.model_dump_json() for event in events)
        self.assertIn("https://seed.example.com/", serialized_events)
        self.assertIn("Ephemeral SSE evidence.", serialized_events)
        search_event = next(
            event for event in events if event.type == "search.completed"
        )
        self.assertEqual(search_event.arguments["country"], "ALL")  # type: ignore[index]
        self.assertEqual(search_event.arguments["language"], "en")  # type: ignore[index]

    async def test_submitted_acquisition_plan_is_returned_for_ui_actions(self) -> None:
        graph_id = uuid4()

        class Acquisition:
            async def prepare_plans(self, _plans: object) -> list[AcquisitionPlan]:
                return [AcquisitionPlan(
                    graph_id=graph_id,
                    graph_slug="marketplace",
                    start_urls=["https://market.example.com/electronics"],
                    max_crawls=500,
                    recommended_run_type="one_off",
                )]

        async def respond(messages, _info):
            if not any(isinstance(message, ModelResponse) for message in messages):
                yield {
                    0: DeltaToolCall(
                        name="propose_acquisition",
                        json_args=json.dumps(
                            {"plans": [{
                                "name": "Marketplace acquisition",
                                "purpose": "Retain marketplace evidence.",
                                "mode": "corpus",
                                "graph_id": str(graph_id),
                                "start_urls": [
                                    "https://market.example.com/electronics"
                                ],
                                "max_crawls": 500,
                                "recommended_run_type": "one_off",
                            }]}
                        ),
                        tool_call_id="plan",
                    )
                }
                return
            yield "The acquisition plan is ready."

        model = FunctionModel(stream_function=respond)
        with _AGENT.override(model=model):
            events = [
                event
                async for event in stream_atlas_search(
                    "Plan a marketplace crawl.",
                    SimpleNamespace(),  # type: ignore[arg-type]
                    Acquisition(),  # type: ignore[arg-type]
                )
            ]

        self.assertEqual(events[-1].type, "run.completed", events[-1].message)
        self.assertEqual(events[-1].acquisition_plans[0].graph_id, graph_id)  # type: ignore[index]
        self.assertEqual(events[-1].acquisition_plans[0].max_crawls, 500)  # type: ignore[index]

    async def test_invalid_sql_is_returned_to_the_agent_for_retry(self) -> None:
        class Catalogue:
            calls = 0

            async def query(self, sql: str) -> CatalogueQueryResult:
                self.calls += 1
                if self.calls == 1:
                    raise CatalogueQueryError("invalid SQL: unexpected token")
                return CatalogueQueryResult(
                    query_id=uuid4().hex,
                    sql=sql,
                    columns=["book_count"],
                    column_types=["BIGINT"],
                    rows=[[517]],
                )

        catalogue = Catalogue()
        model = TestModel(
            call_tools=["query_catalogue"],
            custom_output_text="Atlas retained 517 books.",
        )
        with _AGENT.override(model=model):
            events = [
                event
                async for event in stream_atlas_search(
                    "How many books are retained?",
                    catalogue,  # type: ignore[arg-type]
                    SimpleNamespace(),  # type: ignore[arg-type]
                )
            ]

        event_types = [event.type for event in events]
        self.assertEqual(catalogue.calls, 2)
        self.assertIn("query.failed", event_types)
        self.assertIn("query.completed", event_types)
        self.assertEqual(events[-1].type, "run.completed")

    async def test_catalogue_outage_is_not_misreported_as_bad_sql(self) -> None:
        class Catalogue:
            calls = 0

            async def query(self, _sql: str) -> CatalogueQueryResult:
                self.calls += 1
                raise CatalogueQueryExecutionError("Invalid connection id")

        catalogue = Catalogue()
        model = TestModel(
            call_tools=["query_catalogue"],
            custom_output_text="The catalogue is temporarily unavailable.",
        )
        with _AGENT.override(model=model):
            events = [
                event
                async for event in stream_atlas_search(
                    "What was retained?",
                    catalogue,  # type: ignore[arg-type]
                    SimpleNamespace(),  # type: ignore[arg-type]
                )
            ]

        self.assertEqual(catalogue.calls, 1)
        self.assertIn("query.failed", [event.type for event in events])
        self.assertEqual(events[-1].type, "run.completed")
        self.assertNotIn("Invalid connection id", events[-1].summary or "")

    async def test_brave_failure_is_returned_to_the_agent_for_retry(self) -> None:
        class Acquisition:
            calls = 0

            async def search_web(
                self, query: str, *, count: int = 10, **_kwargs: object
            ) -> SeedSearchResponse:
                self.calls += 1
                if self.calls == 1:
                    raise BraveSearchError("Brave Search is temporarily rate limited.")
                return SeedSearchResponse(query=query, results=[])

        acquisition = Acquisition()
        model = TestModel(
            call_tools=["search_public_web"],
            custom_output_text="No web seeds were returned.",
        )
        with _AGENT.override(model=model):
            events = [
                event
                async for event in stream_atlas_search(
                    "Find public crawl seeds.",
                    SimpleNamespace(),  # type: ignore[arg-type]
                    acquisition,  # type: ignore[arg-type]
                )
            ]

        self.assertEqual(acquisition.calls, 2)
        self.assertEqual(events[-1].type, "run.completed", events[-1].message)


if __name__ == "__main__":
    unittest.main()
