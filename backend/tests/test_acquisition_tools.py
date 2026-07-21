from __future__ import annotations

import unittest
from types import SimpleNamespace

import httpx

from agents.acquisition_tools import AcquisitionTools, BRAVE_WEB_SEARCH_URL


class AcquisitionToolsTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
