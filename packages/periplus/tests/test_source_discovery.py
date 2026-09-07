"""One bounded discovery phase per pass; results are selected only from search evidence."""
import os
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from periplus.crawl.control.collections.discovery import (
    Candidate, DiscoveryState, DiscoveryFailed, DiscoveryUnavailable, SourceDiscovery, public_start_url,
)


def model_response(text):
    return httpx.Response(200, json={"model": "test-model", "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})


class SourceDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"PERIPLUS_DISCOVERY_MODEL": "requested-alias", "OPENAI_API_KEY": "test", "BRAVE_SEARCH_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)

    async def test_checkpointed_phases_do_not_repeat_search_and_preserve_model(self):
        calls = []
        def transport(request):
            calls.append(request)
            if len(calls) == 1:
                return model_response('{"queries":["Finnish robotics"]}')
            if len(calls) == 2:
                return httpx.Response(200, json={"web": {"results": [{"url": "https://example.org/products", "title": "Robots"}, {"url": "file:///secret"}]}})
            return model_response('{"result_ids":[0]}')
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            resolver = SourceDiscovery(client)
            state = DiscoveryState()
            with patch('periplus.crawl.control.collections.discovery.public_start_url', AsyncMock(side_effect=lambda url: url)):
                for revision in range(1, 5):
                    state = await resolver.step("Robots", 5, state)
                    state = DiscoveryState.model_validate_json(state.model_dump_json())
                    self.assertEqual(state.revision, revision)
                self.assertTrue(state.complete)
                self.assertEqual(state.urls, ("https://example.org/products",))
                self.assertEqual(state.model, "test-model")
                self.assertEqual(await resolver.step("Robots", 5, state), state)
        self.assertEqual(json.loads(calls[0].content)["model"], "requested-alias")
        self.assertEqual(json.loads(calls[2].content)["model"], "test-model")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum(request.method == "GET" for request in calls), 1)

    async def test_selection_cannot_invent_candidate_ids(self):
        state = DiscoveryState(model="test-model", queries=("robots",), searches=((Candidate(url="https://example.org/"),),))
        for ids in ('[99]', '[-1]'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: model_response('{"result_ids":'+ids+'}'))) as client:
                with self.subTest(ids=ids), self.assertRaises(DiscoveryFailed):
                    await SourceDiscovery(client).step("Robots", 1, state)

    async def test_provider_outage_and_response_size_do_not_change_checkpoint(self):
        state = DiscoveryState()
        for status, body, error in ((503, b"private detail", DiscoveryUnavailable), (200, b"x" * (512*1024+1), DiscoveryFailed)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body))) as client:
                with self.assertRaises(error):
                    await SourceDiscovery(client).step("Robots", 5, state)
        self.assertEqual(state.revision, 0)

    async def test_dns_rejects_private_addresses_and_defers_transient_failures(self):
        import asyncio
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 0))])):
            with self.assertRaises(DiscoveryFailed):
                await public_start_url("https://example.org/")
        with patch.object(loop, "getaddrinfo", AsyncMock(side_effect=OSError("dns unavailable"))):
            with self.assertRaises(DiscoveryUnavailable):
                await public_start_url("https://example.org/")
