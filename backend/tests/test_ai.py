from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agents.catalogue_ai import (
    AiRequest,
    _AI_AGENT,
)
from agents.catalogue_tools import CatalogueTools
from repository.catalogue.interactive import BufferedCatalogueResult


class AiContractTests(unittest.IsolatedAsyncioTestCase):
    def test_request_context_is_small_and_strict(self) -> None:
        request = AiRequest.model_validate(
            {
                "prompt": "What data do we have?",
                "context": [
                    {"role": "user", "content": "Books?"},
                    {"role": "assistant", "content": "There is a books view."},
                ],
            }
        )
        self.assertEqual(len(request.context), 2)
        with self.assertRaises(ValueError):
            AiRequest.model_validate(
                {
                    "prompt": "Too much context",
                    "context": [
                        {"role": "user", "content": str(index)}
                        for index in range(21)
                    ],
                }
            )

    async def test_sql_tool_uses_the_interactive_query_boundary(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas", catalogue_schema="main"
            )
        )
        service = CatalogueTools(runtime)
        result = BufferedCatalogueResult(
            query_id=uuid4(),
            columns=["price"],
            column_types=["DOUBLE"],
            rows=[[12.5]],
        )
        with patch(
            "agents.catalogue_tools.execute_interactive_query",
            new=AsyncMock(return_value=result),
        ) as execute:
            response = await service.query(
                "SELECT price FROM views.books LIMIT 900"
            )

        self.assertIn("LIMIT 201", execute.await_args.args[1])
        self.assertEqual(response.rows, [[12.5]])
        self.assertTrue(response.compilation.valid)
        self.assertEqual(
            response.compilation.authored_sql,
            execute.await_args.args[1],
        )

    async def test_describe_relation_returns_ducklake_comments(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas", catalogue_schema="main"
            )
        )

        async def run_internal(operation):
            return operation(object())

        runtime.run_internal = run_internal
        service = CatalogueTools(runtime)
        rows = [
            (
                "main",
                "documents",
                "BASE TABLE",
                "One retained document per content identity.",
                "document_id",
                "UUID",
                "NO",
                "Stable document identity.",
            )
        ]
        with patch(
            "agents.catalogue_tools.trusted_remote_rows",
            return_value=rows,
        ) as query:
            relation = await service.describe_relation("main.documents")

        sql = query.call_args.args[1]
        self.assertIn("duckdb_tables()", sql)
        self.assertIn("duckdb_views()", sql)
        self.assertIn("duckdb_columns()", sql)
        self.assertEqual(
            relation.description,
            "One retained document per content identity.",
        )
        self.assertEqual(
            relation.columns[0].description,
            "Stable document identity.",
        )

    async def test_list_macros_uses_managed_descriptions(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas", catalogue_schema="main"
            )
        )

        async def run_internal(operation):
            return operation(object())

        runtime.run_internal = run_internal
        service = CatalogueTools(
            runtime,
            macro_descriptions={
                "macros.suggest_records": "Discovers repeated records."
            },
        )
        with patch(
            "agents.catalogue_tools.trusted_remote_rows",
            return_value=[
                (
                    "macros",
                    "suggest_records",
                    "table_macro",
                    None,
                    ["p_url"],
                    None,
                )
            ],
        ):
            macros = await service.list_macros()

        self.assertEqual(macros[0].description, "Discovers repeated records.")

    def test_agent_has_a_non_executing_lint_tool(self) -> None:
        tools = {
            tool.name
            for toolset in _AI_AGENT.toolsets
            for tool in toolset.tools.values()
        }
        self.assertIn("lint_catalogue_sql", tools)
        self.assertIn("suggest_sql_query", tools)

    def test_sql_suggestion_must_be_clean_and_read_only(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas", catalogue_schema="main"
            )
        )
        service = CatalogueTools(runtime)

        with self.assertRaisesRegex(ValueError, "errors or warnings"):
            service.prepare_suggestion(
                title="All documents",
                description="Inspect retained documents.",
                sql="SELECT * FROM main.documents",
            )
        with self.assertRaisesRegex(ValueError, "only SELECT queries are allowed"):
            service.prepare_suggestion(
                title="Delete documents",
                description="This must never be accepted.",
                sql="DELETE FROM main.documents",
            )

        suggestion = service.prepare_suggestion(
            title="Sample documents",
            description="Inspect a bounded sample.",
            sql="SELECT * FROM main.documents LIMIT 10",
        )
        self.assertEqual(suggestion.title, "Sample documents")
        self.assertIn("LIMIT 10", suggestion.authored_sql)

    async def test_mutating_sql_is_rejected(self) -> None:
        runtime = SimpleNamespace(
            config=SimpleNamespace(
                catalogue_alias="atlas", catalogue_schema="main"
            )
        )
        service = CatalogueTools(runtime)
        with self.assertRaisesRegex(ValueError, "only SELECT queries are allowed"):
            await service.query("DELETE FROM main.documents")


if __name__ == "__main__":
    unittest.main()
