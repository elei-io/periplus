from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai.models.test import TestModel

from agents.catalogue_analytics import (
    AnalysisDirection,
    AnalysisPlan,
    AnalyticsEvent,
    AnalyticsQuestion,
    SqlDependencies,
    _IDEA_AGENT,
    _SQL_AGENT,
    _SYNTHESIS_AGENT,
    query_catalogue,
    stream_catalogue_analysis,
)
from agents.catalogue_tools import (
    CatalogueQueryResult,
    CatalogueTools,
)
from repository.catalogue.interactive import BufferedCatalogueResult
from repository.catalogue.quack_runtime import CatalogueQueryExecutionError
from repository.catalogue.query import CatalogueQueryError


def analysis_plan() -> AnalysisPlan:
    return AnalysisPlan(
        category="Retained crawl volume",
        interpretation="Measure the retained corpus and add useful context.",
        directions=[
            AnalysisDirection(
                id="retained_volume",
                title="Total retained crawls",
                objective="Count all retained crawl records.",
                rationale="Directly answers the user's question.",
            ),
            AnalysisDirection(
                id="host_distribution",
                title="Distribution by host",
                objective="Group retained crawls by host.",
                rationale="Shows whether the total is concentrated.",
            ),
            AnalysisDirection(
                id="collection_freshness",
                title="Collection freshness",
                objective="Measure the latest retained crawl timestamps.",
                rationale="Adds a recency caveat to the count.",
            ),
            AnalysisDirection(
                id="outcome_quality",
                title="Outcome quality",
                objective="Compare successful and unsuccessful outcomes.",
                rationale="Checks whether the retained total is healthy.",
            ),
        ],
    )


class AnalyticsContractTests(unittest.IsolatedAsyncioTestCase):
    def test_question_plan_and_events_are_strict(self) -> None:
        self.assertEqual(
            AnalyticsQuestion(question="What changed?").question,
            "What changed?",
        )
        with self.assertRaisesRegex(ValueError, "at least 3"):
            AnalysisPlan(
                category="Invalid",
                interpretation="Too few directions.",
                directions=analysis_plan().directions[:2],
            )
        with self.assertRaisesRegex(ValueError, "Direction IDs must be unique"):
            AnalysisPlan(
                category="Invalid",
                interpretation="Duplicate direction IDs.",
                directions=[
                    *analysis_plan().directions[:3],
                    analysis_plan().directions[0],
                ],
            )
        with self.assertRaises(ValueError):
            AnalyticsEvent.model_validate(
                {
                    "type": "analysis.started",
                    "run_id": "run",
                    "provider_event": {},
                }
            )

    async def test_query_is_validated_and_bounded(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas", catalogue_schema="main"
            )
        )
        service = CatalogueTools(runtime)
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
            response = await service.query(
                "SELECT page_url FROM main.documents LIMIT 900"
            )

        executed_sql = execute.await_args.args[1]
        self.assertIn("LIMIT 201", executed_sql)
        self.assertEqual(response.sql, executed_sql)
        self.assertEqual(response.rows, [["https://example.com/"]])

    async def test_non_read_only_sql_is_rejected_before_execution(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas", catalogue_schema="main"
            )
        )
        service = CatalogueTools(runtime)
        execute = AsyncMock()
        with (
            patch(
                "agents.catalogue_tools.execute_interactive_query", new=execute
            ),
            self.assertRaisesRegex(
                ValueError, "only SELECT queries are allowed"
            ),
        ):
            await service.query("DELETE FROM main.documents")
        execute.assert_not_awaited()

    async def test_preliminary_sql_is_streamed_as_orientation_evidence(
        self,
    ) -> None:
        class Catalogue:
            async def query(self, sql: str) -> CatalogueQueryResult:
                return CatalogueQueryResult(
                    query_id="orientation-query",
                    sql=sql,
                    columns=["value"],
                    column_types=["BIGINT"],
                    rows=[[1]],
                    row_count=1,
                )

        events: list[AnalyticsEvent] = []

        async def emit(event: AnalyticsEvent) -> None:
            events.append(event)

        dependencies = SqlDependencies(
            run_id="run",
            direction_id=None,
            scope="orientation",
            catalogue_tools=Catalogue(),  # type: ignore[arg-type]
            emit=emit,
        )
        context = SimpleNamespace(
            deps=dependencies,
            tool_call_id="orientation-call",
        )

        result = await query_catalogue(  # type: ignore[arg-type]
            context,
            "SELECT 1",
        )

        self.assertEqual(result.rows, [[1]])
        self.assertEqual(
            [event.type for event in events],
            ["query.started", "query.completed"],
        )
        self.assertTrue(all(event.scope == "orientation" for event in events))
        self.assertTrue(all(event.direction_id is None for event in events))

    async def test_pipeline_runs_dynamic_directions_and_synthesizes(
        self,
    ) -> None:
        class Catalogue:
            calls = 0

            async def query(self, sql: str) -> CatalogueQueryResult:
                self.calls += 1
                return CatalogueQueryResult(
                    query_id=f"query-{self.calls}",
                    sql=sql,
                    columns=["value"],
                    column_types=["BIGINT"],
                    rows=[[self.calls]],
                    row_count=1,
                )

        catalogue = Catalogue()
        with (
            _IDEA_AGENT.override(
                model=TestModel(
                    call_tools=[],
                    custom_output_args=analysis_plan().model_dump(mode="json")
                )
            ),
            _SQL_AGENT.override(
                model=TestModel(
                    call_tools=["query_catalogue"],
                    custom_output_text="Direction finding.",
                )
            ),
            _SYNTHESIS_AGENT.override(
                model=TestModel(custom_output_text="Combined finding.")
            ),
        ):
            events = [
                event
                async for event in stream_catalogue_analysis(
                    "How many crawls are retained?",
                    catalogue,  # type: ignore[arg-type]
                )
            ]

        self.assertEqual(events[0].type, "analysis.started")
        plan_event = next(
            event for event in events if event.type == "plan.completed"
        )
        self.assertEqual(len(plan_event.plan.directions), 4)  # type: ignore[union-attr]
        started = [
            event.direction_id
            for event in events
            if event.type == "direction.started"
        ]
        self.assertCountEqual(
            started,
            [
                "retained_volume",
                "host_distribution",
                "collection_freshness",
                "outcome_quality",
            ],
        )
        completed_queries = [
            event
            for event in events
            if event.type == "query.completed" and event.scope == "analysis"
        ]
        self.assertEqual(len(completed_queries), 4)
        self.assertEqual(
            {event.direction_id for event in completed_queries},
            {
                "retained_volume",
                "host_distribution",
                "collection_freshness",
                "outcome_quality",
            },
        )
        self.assertEqual(events[-1].type, "analysis.completed")
        self.assertEqual(events[-1].summary, "Combined finding.")

    async def test_invalid_sql_is_returned_for_retry_per_direction(self) -> None:
        class Catalogue:
            calls = 0

            async def query(self, sql: str) -> CatalogueQueryResult:
                self.calls += 1
                if self.calls <= 4:
                    raise CatalogueQueryError(
                        "invalid SQL: unexpected token"
                    )
                return CatalogueQueryResult(
                    query_id=uuid4().hex,
                    sql=sql,
                    columns=["value"],
                    column_types=["BIGINT"],
                    rows=[[1]],
                    row_count=1,
                )

        catalogue = Catalogue()
        with (
            _IDEA_AGENT.override(
                model=TestModel(
                    call_tools=[],
                    custom_output_args=analysis_plan().model_dump(mode="json")
                )
            ),
            _SQL_AGENT.override(
                model=TestModel(
                    call_tools=["query_catalogue"],
                    custom_output_text="Recovered.",
                )
            ),
            _SYNTHESIS_AGENT.override(
                model=TestModel(custom_output_text="Recovered synthesis.")
            ),
        ):
            events = [
                event
                async for event in stream_catalogue_analysis(
                    "Analyze retained data.",
                    catalogue,  # type: ignore[arg-type]
                )
            ]

        self.assertEqual(
            len([event for event in events if event.type == "query.failed"]),
            4,
        )
        self.assertEqual(
            len(
                [event for event in events if event.type == "query.completed"]
            ),
            4,
        )
        self.assertEqual(events[-1].type, "analysis.completed")

    async def test_catalogue_outage_hides_internal_error(self) -> None:
        class Catalogue:
            async def query(self, _sql: str) -> CatalogueQueryResult:
                raise CatalogueQueryExecutionError("Invalid connection id")

        with (
            _IDEA_AGENT.override(
                model=TestModel(
                    call_tools=[],
                    custom_output_args=analysis_plan().model_dump(mode="json")
                )
            ),
            _SQL_AGENT.override(
                model=TestModel(
                    call_tools=["query_catalogue"],
                    custom_output_text="Catalogue unavailable.",
                )
            ),
            _SYNTHESIS_AGENT.override(
                model=TestModel(custom_output_text="Unavailable synthesis.")
            ),
        ):
            events = [
                event
                async for event in stream_catalogue_analysis(
                    "What was retained?",
                    Catalogue(),  # type: ignore[arg-type]
                )
            ]

        serialized = "\n".join(event.model_dump_json() for event in events)
        self.assertEqual(
            len([event for event in events if event.type == "query.failed"]),
            4,
        )
        self.assertNotIn("Invalid connection id", serialized)
        self.assertEqual(events[-1].type, "analysis.completed")


if __name__ == "__main__":
    unittest.main()
