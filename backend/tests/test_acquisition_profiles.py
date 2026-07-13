from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from actions.crawl.service import _acquire_firecrawl, _acquire_http
from control.crawl_policies.schemas import FirecrawlProfileConfig, HttpProfileConfig
from runtime.crawl_capacity import (
    PolicyCapacityHolder,
    PolicyCapacityState,
    _release_policy_slot,
    _try_acquire_policy_slot,
    policy_capacity_key,
)


class FakeKV:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.revisions: dict[str, int] = {}

    async def create(self, key: str, value: bytes) -> int:
        if key in self.values:
            from nats.js.errors import KeyWrongLastSequenceError

            raise KeyWrongLastSequenceError
        self.values[key] = value
        self.revisions[key] = 1
        return 1

    async def get(self, key: str):
        if key not in self.values:
            from nats.js.errors import KeyNotFoundError

            raise KeyNotFoundError
        return SimpleNamespace(value=self.values[key], revision=self.revisions[key])

    async def update(self, key: str, value: bytes, last: int) -> int:
        if self.revisions[key] != last:
            from nats.js.errors import KeyWrongLastSequenceError

            raise KeyWrongLastSequenceError
        self.values[key] = value
        self.revisions[key] += 1
        return self.revisions[key]


class AcquisitionProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_profile_returns_remote_html(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["x-atlas-test"], "yes")
            return httpx.Response(
                200,
                text="<html><body>ok</body></html>",
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await _acquire_http(
                client,
                "https://example.com/",
                HttpProfileConfig(headers={"x-atlas-test": "yes"}),
            )

        self.assertTrue(page.success)
        self.assertEqual(page.status_code, 200)
        self.assertIn("<body>ok</body>", page.html or "")

    async def test_firecrawl_profile_requests_raw_html(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v2/scrape")
            self.assertEqual(request.headers["authorization"], "Bearer fc-test")
            self.assertIn(b'"formats":["rawHtml"]', request.content)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "rawHtml": "<html><body>remote</body></html>",
                        "metadata": {
                            "url": "https://example.com/final",
                            "statusCode": 200,
                        },
                    },
                },
                request=request,
            )

        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                page = await _acquire_firecrawl(
                    client,
                    "https://example.com/",
                    FirecrawlProfileConfig(),
                )

        self.assertTrue(page.success)
        self.assertEqual(page.url, "https://example.com/final")
        self.assertIn("remote", page.html or "")

    async def test_policy_capacity_is_shared_by_all_workers(self) -> None:
        bucket = FakeKV()
        policy = SimpleNamespace(
            id=uuid4(),
            revision=3,
            config={"profile": "http", "concurrency": 1, "config": {}},
        )
        first_owner = uuid4()
        second_owner = uuid4()
        now = datetime.now(UTC)

        self.assertTrue(
            await _try_acquire_policy_slot(
                bucket, policy=policy, owner=first_owner, now=now
            )
        )
        self.assertFalse(
            await _try_acquire_policy_slot(
                bucket, policy=policy, owner=second_owner, now=now
            )
        )
        await _release_policy_slot(bucket, policy=policy, owner=first_owner)
        self.assertTrue(
            await _try_acquire_policy_slot(
                bucket, policy=policy, owner=second_owner, now=now
            )
        )

    async def test_expired_policy_holder_does_not_consume_capacity(self) -> None:
        bucket = FakeKV()
        policy_id = uuid4()
        policy = SimpleNamespace(
            id=policy_id,
            revision=1,
            config={"profile": "http", "concurrency": 1, "config": {}},
        )
        now = datetime.now(UTC)
        state = PolicyCapacityState(
            policy_id=policy_id,
            policy_revision=1,
            concurrency=1,
            holders=(
                PolicyCapacityHolder(
                    owner=uuid4(), expires_at=now - timedelta(seconds=1)
                ),
            ),
            updated_at=now - timedelta(seconds=2),
        )
        await bucket.create(
            policy_capacity_key(policy_id, 1), state.model_dump_json().encode()
        )

        self.assertTrue(
            await _try_acquire_policy_slot(
                bucket, policy=policy, owner=uuid4(), now=now
            )
        )


if __name__ == "__main__":
    unittest.main()
