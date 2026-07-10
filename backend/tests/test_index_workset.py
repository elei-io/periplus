from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from pydantic import ValidationError

from actions.crawl.schemas import CrawlOutput, CrawlPage, CrawlStats
from actions.index.schemas import Input
from actions.index.service import index
from actions.index.workset import IndexBudgetExceeded, IndexWorkset


class IndexWorksetTests(unittest.TestCase):
    def test_tracks_repeated_links_and_deduplicated_results_exactly(self) -> None:
        with IndexWorkset(
            max_pages=10,
            max_links=10,
            max_temp_bytes=100 * 1024 * 1024,
        ) as workset:
            workset.seed("https://example.com")
            root = workset.claim(1)[0]
            workset.complete_page(
                root,
                edges=[
                    ("https://example.com/a", "one", "", 0, 0, True, True, True),
                    ("https://example.com/a", "two", "", 0, 1, True, True, True),
                ],
                succeeded=True,
            )

            self.assertEqual(workset.edge_count(), 2)
            self.assertEqual(workset.result_count(dedupe=False), 2)
            self.assertEqual(workset.result_count(dedupe=True), 1)
            self.assertEqual(workset.page_count(), 2)

    def test_streams_edges_and_only_enqueues_crawlable_targets(self) -> None:
        with IndexWorkset(
            max_pages=10,
            max_links=10,
            max_temp_bytes=100 * 1024 * 1024,
        ) as workset:
            workset.seed("https://example.com")
            root = workset.claim(1)[0]

            def edges():
                yield ("https://example.com/a", "a", "", 0, 0, True, True, True)
                yield ("https://outside.example/b", "b", "", 0, 1, False, True, False)

            workset.complete_page(root, edges=edges(), succeeded=True)

            self.assertEqual(workset.edge_count(), 2)
            self.assertEqual(workset.page_count(), 2)

    def test_budget_failure_rolls_back_page_writes_and_removes_temp_file(self) -> None:
        workset = IndexWorkset(
            max_pages=2,
            max_links=1,
            max_temp_bytes=100 * 1024 * 1024,
        )
        path = workset.path
        try:
            workset.seed("https://example.com")
            root = workset.claim(1)[0]
            with self.assertRaises(IndexBudgetExceeded):
                workset.complete_page(
                    root,
                    edges=[
                        ("https://example.com/a", "", "", 0, 0, True, True, True),
                        ("https://example.com/b", "", "", 0, 1, True, True, True),
                    ],
                    succeeded=True,
                )
            self.assertEqual(workset.edge_count(), 0)
            self.assertEqual(workset.page_count(), 1)
        finally:
            workset.close(check_budget=False)
        self.assertFalse(path.exists())


class IndexServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_traversal_keeps_only_disk_backed_counts(self) -> None:
        async def fake_crawl(*, urls, page_consumer, **_kwargs):
            pages = []
            for position, url in enumerate(urls):
                depth = int(url.rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else 0
                links = [] if depth >= 2 else [{"href": f"https://example.com/{depth + 1}"}]
                page = CrawlPage(
                    url=url,
                    success=True,
                    status_code=200,
                    duration_seconds=0,
                    crawl={"links": {"internal": links, "external": []}},
                )
                await page_consumer(position, url, page)
                pages.append(page)
            return CrawlOutput(
                stats=CrawlStats(
                    requested_urls=len(urls),
                    succeeded=len(urls),
                    failed=0,
                    duration_seconds=0,
                ),
                pages=pages,
            )

        with patch("actions.index.service.crawl_service", new=fake_crawl):
            output = await index("https://example.com/0", max_depth=2)

        self.assertEqual(output.pages, 3)
        self.assertEqual(output.failed_pages, 0)
        self.assertEqual(output.discovered_links, 2)
        self.assertEqual(output.result_links, 2)

    async def test_reuses_one_repository_pipeline_across_frontier_batches(self) -> None:
        seen_pipelines = []
        crawl_calls = 0

        async def fake_crawl(*, urls, page_consumer, repository_pipeline, **_kwargs):
            nonlocal crawl_calls
            crawl_calls += 1
            seen_pipelines.append(repository_pipeline)
            url = urls[0]
            links = (
                [{"href": "https://example.com/next"}]
                if crawl_calls == 1
                else []
            )
            page = CrawlPage(
                url=url,
                success=True,
                status_code=200,
                duration_seconds=0,
                crawl={"links": {"internal": links, "external": []}},
            )
            await page_consumer(0, url, page)
            return CrawlOutput(
                stats=CrawlStats(
                    requested_urls=1,
                    succeeded=1,
                    failed=0,
                    duration_seconds=0,
                ),
                pages=[page],
            )

        pipeline = MagicMock()
        pipeline.__aenter__ = AsyncMock(return_value=pipeline)
        pipeline.close = AsyncMock()
        with (
            patch("actions.index.service.crawl_service", new=fake_crawl),
            patch("actions.index.service.repository_ingestor_from_env", return_value=object()),
            patch("actions.index.service.RepositoryPipeline", return_value=pipeline),
            patch("actions.index.service.repository_required_for_urls", return_value=True),
            patch.dict(os.environ, {"ATLAS_INDEX_PAGE_BATCH_SIZE": "1"}),
        ):
            output = await index(
                "https://example.com",
                max_depth=1,
                session=object(),  # type: ignore[arg-type]
                task_run_id=uuid4(),
            )

        self.assertEqual(output.pages, 2)
        self.assertEqual(crawl_calls, 2)
        self.assertEqual(seen_pipelines, [pipeline, pipeline])
        pipeline.__aenter__.assert_awaited_once()
        pipeline.close.assert_awaited_once()

    async def test_rejects_index_budget_above_deployment_ceiling(self) -> None:
        with patch.dict(os.environ, {"ATLAS_INDEX_MAX_PAGES": "2"}):
            with self.assertRaisesRegex(ValueError, "ATLAS_INDEX_MAX_PAGES"):
                await index("https://example.com", max_pages=3)


class IndexInputTests(unittest.TestCase):
    def test_omitted_budget_uses_a_lower_deployment_ceiling(self) -> None:
        with patch.dict(os.environ, {"ATLAS_INDEX_MAX_PAGES": "20"}):
            value = Input(url="https://example.com")

        self.assertEqual(value.max_pages, 20)

    def test_explicit_budget_above_deployment_ceiling_is_rejected(self) -> None:
        with patch.dict(os.environ, {"ATLAS_INDEX_MAX_PAGES": "20"}):
            with self.assertRaises(ValidationError):
                Input(url="https://example.com", max_pages=21)


if __name__ == "__main__":
    unittest.main()
