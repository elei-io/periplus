from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from runtime.graph_queue import (
    CRAWL_SUBJECTS,
    CrawlRequest,
    crawl_transport_from_policy,
    publish_crawl,
)


def policy_snapshot(profile: str) -> dict:
    return {
        "id": str(uuid4()),
        "slug": f"{profile}-test",
        "scheme": "https",
        "host": "example.com",
        "path_prefix": "/",
        "path_mode": "prefix",
        "max_concurrency": 1,
        "profile": {
            "id": str(uuid4()),
            "slug": f"{profile}-profile",
            "name": f"{profile.title()} test",
            "transport": profile,
            "config": {},
            "cost_rank": 10,
        },
    }


class CrawlTransportRoutingTests(unittest.TestCase):
    def test_frozen_policy_selects_exact_transport(self) -> None:
        for profile in ("http", "browser", "firecrawl"):
            self.assertEqual(crawl_transport_from_policy(policy_snapshot(profile)), profile)

    def test_publication_uses_only_the_frozen_transport_subject(self) -> None:
        async def scenario() -> None:
            published: list[tuple[str, bytes, dict]] = []

            async def publish(subject: str, payload: bytes, **kwargs) -> None:
                published.append((subject, payload, kwargs))

            now = datetime.now(UTC)
            for transport in ("http", "browser", "firecrawl"):
                request = CrawlRequest(
                    id=uuid4(),
                    graph_run_id=uuid4(),
                    node_id=uuid4(),
                    url="https://example.com/",
                    transport=transport,
                    effective_policy_snapshot_json=policy_snapshot(transport),
                    created_at=now,
                    updated_at=now,
                )
                await publish_crawl(SimpleNamespace(publish=publish), request)

            self.assertEqual(
                [subject for subject, _payload, _kwargs in published],
                [CRAWL_SUBJECTS["http"], CRAWL_SUBJECTS["browser"], CRAWL_SUBJECTS["firecrawl"]],
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
