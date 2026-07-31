from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from atlas.query.ai import (
    AiAnswer,
    AiRequest,
    CatalogueAssistantTools,
    _INSTRUCTIONS,
    query_catalogue,
)


class _Catalogue:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.sql: list[str] = []

    def trusted_remote_result(self, sql: str):
        self.sql.append(sql)
        return ["value"], ["INTEGER"], self.rows


class _Control:
    def __init__(self, catalogue: _Catalogue):
        self.catalogue = catalogue

    async def run(self, operation):
        return operation(object(), self.catalogue)


class AiContractTests(unittest.IsolatedAsyncioTestCase):
    def test_instructions_prioritize_notebook_ready_research_sql(self) -> None:
        instructions = " ".join(_INSTRUCTIONS.split())
        self.assertIn("turn a research goal into SQL", instructions)
        self.assertIn("take your suggested SQL into a notebook", instructions)
        self.assertIn("register at least one and at most three", instructions)
        self.assertIn("exploratory probes", instructions)

    def test_request_context_is_small_and_strict(self) -> None:
        request = AiRequest.model_validate(
            {
                "prompt": "What data do we have?",
                "context": [
                    {"role": "user", "content": "Pages?"},
                    {"role": "assistant", "content": "There is a pages view."},
                ],
            }
        )
        self.assertEqual(len(request.context), 2)
        with self.assertRaises(ValidationError):
            AiRequest.model_validate(
                {
                    "prompt": "Too much context",
                    "context": [
                        {"role": "user", "content": str(index)}
                        for index in range(21)
                    ],
                }
            )

    async def test_catalogue_metadata_uses_the_current_five_value_contract(
        self,
    ) -> None:
        tools = CatalogueAssistantTools(_Control(_Catalogue()))
        with patch(
            "atlas.query.ai._public_metadata",
            return_value=(
                "catalogue-v1",
                "duckdb-v1",
                128,
                [
                    (
                        "web",
                        "page",
                        "Captured pages.",
                        "url",
                        "VARCHAR",
                        False,
                        "Normalized URL.",
                    )
                ],
                {},
            ),
        ):
            result = await tools.list_objects()

        self.assertEqual(result["catalogue_version"], "catalogue-v1")
        self.assertEqual(result["relations"][0]["name"], "web.page")
        self.assertEqual(
            result["relations"][0]["columns"][0]["name"],
            "url",
        )

    async def test_ai_query_is_read_only_and_bounded(self) -> None:
        catalogue = _Catalogue([[index] for index in range(202)])
        tools = CatalogueAssistantTools(_Control(catalogue))

        result = await tools.query("SELECT url FROM web.page")

        self.assertIn("LIMIT 201", catalogue.sql[0])
        self.assertEqual(len(result["rows"]), 200)
        self.assertEqual(result["row_count"], 200)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["sql"], "SELECT url FROM web.page;")
        self.assertEqual(
            result["display_sql"],
            "SELECT\n  url\nFROM web.page;",
        )
        with self.assertRaisesRegex(ValueError, "read-only"):
            await tools.query("DELETE FROM web.page")

    async def test_suggestion_is_bound_and_normalized_before_handoff(self) -> None:
        catalogue = _Catalogue()
        tools = CatalogueAssistantTools(_Control(catalogue))

        suggestion = await tools.prepare_suggestion(
            title="Recent pages",
            description="Inspect recently observed pages.",
            sql="select * from web.page limit 10;",
        )

        self.assertEqual(suggestion.sql, "SELECT * FROM web.page LIMIT 10;")
        self.assertEqual(
            suggestion.display_sql,
            "SELECT\n  *\nFROM web.page\nLIMIT 10;",
        )
        self.assertEqual(
            catalogue.sql,
            ["EXPLAIN SELECT * FROM web.page LIMIT 10"],
        )
        with self.assertRaisesRegex(ValueError, "web"):
            await tools.prepare_suggestion(
                title="Internal",
                description="Must be rejected.",
                sql="SELECT * FROM material.html_elements",
            )

    async def test_query_events_explain_and_preserve_agent_work(self) -> None:
        catalogue = _Catalogue([[4413]])
        tools = CatalogueAssistantTools(_Control(catalogue))
        events = []

        async def emit(event):
            events.append(event)

        context = SimpleNamespace(
            deps=SimpleNamespace(
                run_id="run",
                tools=tools,
                emit=emit,
            ),
            tool_call_id="query-1",
        )
        result = await query_catalogue(
            context,
            "Count retained domains",
            "select count(*) from web.page",
        )

        self.assertEqual(result["row_count"], 1)
        self.assertEqual([event.type for event in events], [
            "tool.started",
            "tool.completed",
        ])
        self.assertTrue(all(event.activity == "query" for event in events))
        self.assertTrue(
            all(event.purpose == "Count retained domains" for event in events)
        )
        self.assertEqual(events[-1].sql, "SELECT COUNT(*) FROM web.page;")
        self.assertEqual(
            events[-1].display_sql,
            "SELECT\n  COUNT(*)\nFROM web.page;",
        )
        self.assertEqual(events[-1].row_count, 1)
        self.assertEqual(events[-1].columns, ["value"])
        self.assertEqual(events[-1].types, ["INTEGER"])
        self.assertEqual(events[-1].rows, [[4413]])

    def test_answer_contract_is_compact_markdown(self) -> None:
        answer = AiAnswer.model_validate(
            {
                "markdown": (
                    "There are **4,413 domains**.\n\n"
                    "- Counted distinct non-null domains."
                ),
            }
        )

        self.assertIn("**4,413 domains**", answer.markdown)
        with self.assertRaises(ValidationError):
            AiAnswer.model_validate(
                {
                    "markdown": "x" * 4_001,
                }
            )


if __name__ == "__main__":
    unittest.main()
