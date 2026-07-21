from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from agents.acquisition_tools import (
    AcquisitionTools,
    BRAVE_WEB_SEARCH_URL,
    BraveSearchError,
    BraveSearchParameterError,
    CrawlGraphSummary,
)


class AcquisitionToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_page_waits_for_exact_crawl_ingestion_success(self) -> None:
        graph_id = uuid4()
        run_id = uuid4()
        crawl_id = uuid4()
        submitter = AsyncMock(return_value=run_id)
        tools = AcquisitionTools(
            SimpleNamespace(),  # type: ignore[arg-type]
            brave_api_key=None,
            page_crawl_submitter=submitter,
        )
        tools.list_graphs = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                CrawlGraphSummary(
                    id=graph_id,
                    slug="single-page",
                    description="One page",
                    system_owned=True,
                    node_count=1,
                    edge_count=0,
                )
            ]
        )
        request = SimpleNamespace(
            id=crawl_id,
            document_id="sha256:document",
            status="completed",
            error=None,
        )
        ingestion = SimpleNamespace(
            status="succeeded",
            result=SimpleNamespace(
                document_id="sha256:document",
                repository_snapshot=42,
            ),
        )
        client = SimpleNamespace(
            jetstream=lambda: SimpleNamespace(),
            drain=AsyncMock(),
        )
        with (
            patch("agents.acquisition_tools.connect_nats", AsyncMock(return_value=client)),
            patch(
                "agents.acquisition_tools.ensure_graph_storage",
                AsyncMock(return_value=(object(), object(), object())),
            ),
            patch(
                "agents.acquisition_tools.ensure_ingestion_results",
                AsyncMock(return_value=object()),
            ),
            patch(
                "agents.acquisition_tools.get_crawl_request",
                AsyncMock(return_value=request),
            ),
            patch(
                "agents.acquisition_tools.get_ingestion_state",
                AsyncMock(return_value=ingestion),
            ) as get_state,
        ):
            result = await tools.inspect_live_page("https://example.com/page#fragment")

        submitter.assert_awaited_once_with(graph_id, "https://example.com/page")
        self.assertEqual(result.status, "catalogue_ready")
        self.assertEqual(result.crawl_id, crawl_id)
        self.assertEqual(result.repository_snapshot, 42)
        self.assertIn(crawl_id.hex, get_state.await_args.args[1])
        client.drain.assert_awaited_once()

    async def test_web_search_returns_only_bounded_agent_context(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url).split("?")[0], BRAVE_WEB_SEARCH_URL)
            self.assertEqual(request.headers["X-Subscription-Token"], "secret")
            self.assertEqual(request.url.params["country"], "DE")
            self.assertEqual(request.url.params["search_lang"], "de")
            self.assertEqual(request.url.params["ui_lang"], "de-DE")
            self.assertEqual(request.url.params["freshness"], "pw")
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Example documentation",
                                "url": "https://docs.example.com/",
                                "description": "Official product documentation.",
                                "extra_snippets": ["raw provider-only field"],
                                "provider_metadata": {"must_not_escape": True},
                            },
                            {
                                "title": "Unsafe URL",
                                "url": "javascript:alert(1)",
                            },
                        ]
                    }
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            tools = AcquisitionTools(
                SimpleNamespace(),  # type: ignore[arg-type]
                brave_api_key="secret",
                http_client=client,
            )
            result = await tools.search_web(
                "example documentation",
                count=5,
                country="de",
                search_lang="de",
                ui_lang="de-DE",
                freshness="week",
            )

        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].url, "https://docs.example.com/")
        serialized = result.model_dump_json()
        self.assertNotIn("raw provider-only field", serialized)
        self.assertNotIn("provider_metadata", serialized)

    async def test_web_search_reports_unavailable_without_making_a_request(
        self,
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("Brave Search should not be called without a key")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            tools = AcquisitionTools(
                SimpleNamespace(),  # type: ignore[arg-type]
                brave_api_key=None,
                http_client=client,
            )
            result = await tools.search_web("example documentation")

        self.assertEqual(result.results, [])
        self.assertIsNotNone(result.unavailable_reason)

    async def test_web_search_normalizes_japanese_language_alias(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params["country"], "JP")
            self.assertEqual(request.url.params["search_lang"], "jp")
            self.assertEqual(request.url.params["ui_lang"], "ja-JP")
            return httpx.Response(200, json={"web": {"results": []}})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            tools = AcquisitionTools(
                SimpleNamespace(),  # type: ignore[arg-type]
                brave_api_key="secret",
                http_client=client,
            )
            await tools.search_web(
                "中古家電",
                country="JP",  # type: ignore[arg-type]
                search_lang="ja",  # type: ignore[arg-type]
                ui_lang="ja_jp",  # type: ignore[arg-type]
            )

    async def test_web_search_rejects_unsupported_locale_before_request(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("Unsupported locales must not reach Brave Search")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            tools = AcquisitionTools(
                SimpleNamespace(),  # type: ignore[arg-type]
                brave_api_key="secret",
                http_client=client,
            )
            with self.assertRaisesRegex(
                BraveSearchParameterError, "does not support country"
            ):
                await tools.search_web(
                    "example",
                    country="ZZ",  # type: ignore[arg-type]
                )

    async def test_web_search_turns_provider_errors_into_safe_retryable_errors(
        self,
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422,
                json={
                    "error": {
                        "id": "provider-id-must-not-escape",
                        "meta": {
                            "errors": [
                                {
                                    "loc": ["query", "search_lang"],
                                    "msg": "provider detail must not escape",
                                }
                            ]
                        },
                    }
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            tools = AcquisitionTools(
                SimpleNamespace(),  # type: ignore[arg-type]
                brave_api_key="secret",
                http_client=client,
            )
            with self.assertRaises(BraveSearchError) as raised:
                await tools.search_web("example")

        message = str(raised.exception)
        self.assertIn("search_lang", message)
        self.assertNotIn("provider-id", message)
        self.assertNotIn("provider detail", message)


if __name__ == "__main__":
    unittest.main()
