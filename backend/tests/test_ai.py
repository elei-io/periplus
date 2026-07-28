from __future__ import annotations

import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from atlas.query.ai import (
    AiAnswer,
    AiRequest,
    CatalogueAssistantTools,
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

    async def test_ai_query_is_read_only_and_bounded(self) -> None:
        catalogue = _Catalogue([[index] for index in range(202)])
        tools = CatalogueAssistantTools(_Control(catalogue))

        result = await tools.query("SELECT page_id FROM web.pages")

        self.assertIn("LIMIT 201", catalogue.sql[0])
        self.assertEqual(len(result["rows"]), 200)
        self.assertEqual(result["row_count"], 200)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["sql"], "SELECT page_id FROM web.pages;")
        self.assertEqual(
            result["display_sql"],
            "SELECT\n  page_id\nFROM web.pages;",
        )
        with self.assertRaisesRegex(ValueError, "read-only"):
            await tools.query("DELETE FROM web.pages")

    async def test_suggestion_is_bound_and_normalized_before_handoff(self) -> None:
        catalogue = _Catalogue()
        tools = CatalogueAssistantTools(_Control(catalogue))

        suggestion = await tools.prepare_suggestion(
            title="Recent pages",
            description="Inspect recently observed pages.",
            sql="select * from web.pages limit 10;",
        )

        self.assertEqual(suggestion.sql, "SELECT * FROM web.pages LIMIT 10;")
        self.assertEqual(
            suggestion.display_sql,
            "SELECT\n  *\nFROM web.pages\nLIMIT 10;",
        )
        self.assertEqual(
            catalogue.sql,
            ["EXPLAIN SELECT * FROM web.pages LIMIT 10"],
        )
        with self.assertRaisesRegex(ValueError, "web"):
            await tools.prepare_suggestion(
                title="Internal",
                description="Must be rejected.",
                sql="SELECT * FROM material.pages",
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
            "select count(*) from web.pages",
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
        self.assertEqual(events[-1].sql, "SELECT COUNT(*) FROM web.pages;")
        self.assertEqual(
            events[-1].display_sql,
            "SELECT\n  COUNT(*)\nFROM web.pages;",
        )
        self.assertEqual(events[-1].row_count, 1)

    def test_answer_contract_is_compact_and_structured(self) -> None:
        answer = AiAnswer.model_validate(
            {
                "conclusion": "There are 4,413 domains.",
                "evidence": ["Counted distinct non-null domains."],
                "recommendation": None,
            }
        )

        self.assertEqual(answer.conclusion, "There are 4,413 domains.")
        with self.assertRaises(ValidationError):
            AiAnswer.model_validate(
                {
                    "conclusion": "Too much evidence.",
                    "evidence": [str(index) for index in range(4)],
                }
            )


if __name__ == "__main__":
    unittest.main()
