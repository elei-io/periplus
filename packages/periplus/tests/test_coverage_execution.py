import copy
from datetime import UTC, datetime
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import duckdb
import httpx

from periplus.crawl.control.coverage_requests.resolver import CoverageResolver, ResolutionFailed, ResolutionUnavailable, public_start_url
from periplus.crawl.runtime.coverage_requests import process_coverage_request, coverage_run_id, coverage_plan


def response(text):
    return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpointed_search_and_selected_url_provenance(self):
        calls = []
        def transport(request):
            calls.append(request)
            if len(calls) == 1:
                return response('{"queries":["Finnish robotics manufacturers"]}')
            if len(calls) == 2:
                return httpx.Response(200, json={"web": {"results": [{"url": "https://example.org/products", "title": "Robots"}, {"url": "file:///etc/passwd"}]}})
            return response('{"result_ids":[0]}')
        saved = []
        async def checkpoint(state): saved.append(copy.deepcopy(state))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "PERIPLUS_COVERAGE_MODEL": "test", "BRAVE_SEARCH_API_KEY": "test"}), patch('periplus.crawl.control.coverage_requests.resolver.public_start_url', AsyncMock(side_effect=lambda url: url)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                resolver = CoverageResolver(client)
                urls = await resolver.resolve("Robots", 5, {}, checkpoint)
                self.assertEqual(urls, ["https://example.org/products"])
                self.assertEqual(len(saved), 2)
                await resolver.resolve("Robots", 5, saved[-1], checkpoint)
                self.assertEqual(sum(r.method == 'GET' for r in calls), 1)

    async def test_cannot_invent_urls(self):
        state = {"queries": ["robots"], "searches": [[{"url": "https://example.org/", "title": "Robots"}]]}
        for result in ['{"result_ids":[99]}', '{"result_ids":[]}', '{"result_ids":[-1]}']:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "PERIPLUS_COVERAGE_MODEL": "test", "BRAVE_SEARCH_API_KEY": "test"}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response(result))) as client:
                    with self.assertRaises(ResolutionFailed):
                        await CoverageResolver(client).resolve("robots", 1, state, AsyncMock())

    async def test_missing_credentials_wait_without_provider_calls(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail('Unexpected provider call'))) as client:
                with self.assertRaises(ResolutionUnavailable):
                    await CoverageResolver(client).resolve("robots", 1, {}, AsyncMock())

    async def test_private_starting_hosts_rejected(self):
        for url in ['http://127.0.0.1/', 'http://[::1]/', 'http://169.254.169.254/']:
            with self.assertRaises(ResolutionFailed):
                await public_start_url(url)


class Store:
    def __init__(self, row): self.row = row
    def get(self, identity): return copy.deepcopy(self.row)
    def update(self, identity, **values):
        for key, value in values.items(): setattr(self.row, key, value)
    def policies(self, urls): return {url: {} for url in urls}


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row = SimpleNamespace(id=uuid4(), kind='description', input='Robots', status='pending', retry_at=None,
            run_id=None, started_at=None, completed_at=None, resolved_urls=[], resolution={}, attempts=0,
            max_pages=5, depth=1, link_scope='internal', allowed_sections=[], error=None)
        self.store = Store(self.row)
        self.runs = SimpleNamespace(get_run=AsyncMock(return_value=None))
        self.resolver = SimpleNamespace(resolve=AsyncMock(return_value=['https://example.org/products']))
        self.create = patch('periplus.crawl.runtime.coverage_requests.create_graph_run', AsyncMock())
        self.created = self.create.start()
        self.addCleanup(self.create.stop)

    async def process(self):
        await process_coverage_request(self.row.id, store=self.store, resolver=self.resolver, runs=self.runs,
                                       requests=None, progress=None, jetstream=None)

    async def test_retry_uses_same_run_and_frozen_urls(self):
        await self.process()
        self.assertEqual(self.row.status, 'ongoing')
        first = self.created.call_args.kwargs
        self.row.retry_at = None
        await self.process()
        self.assertEqual(self.resolver.resolve.await_count, 1)
        self.assertEqual(self.created.call_args.kwargs['run_id'], first['run_id'])
        self.assertEqual(self.created.call_args.kwargs['now'], first['now'])
        self.assertEqual(first['max_crawls'], 5)
        self.assertEqual(first['max_run_seconds'], 3600)

    async def test_crash_after_run_commit_recovers_completion(self):
        self.runs.get_run.return_value = SimpleNamespace(status='completed_with_errors', completed_at=datetime.now(UTC), request_count=2, failed_request_count=1)
        await self.process()
        self.assertEqual(self.row.status, 'completed')
        self.assertEqual(self.row.run_id, coverage_run_id(self.row.id))
        self.created.assert_not_called()
        self.resolver.resolve.assert_not_called()

    async def test_missing_configuration_pending_and_no_crawl(self):
        self.resolver.resolve.side_effect = ResolutionUnavailable('Search not configured')
        await self.process()
        self.assertEqual(self.row.status, 'pending')
        self.assertGreater(self.row.retry_at, datetime.now(UTC))
        self.assertEqual(self.row.attempts, 0)
        self.created.assert_not_called()

    async def test_transient_search_failure_retries_then_stops(self):
        self.resolver.resolve.side_effect = httpx.ReadTimeout('test')
        with self.assertLogs(level='ERROR'):
            for _ in range(3):
                self.row.retry_at = None
                await self.process()
        self.assertEqual(self.row.status, 'failed')
        self.created.assert_not_called()

    async def test_failed_run_does_not_become_success(self):
        self.runs.get_run.return_value = SimpleNamespace(status='failed', completed_at=datetime.now(UTC), request_count=1, failed_request_count=1)
        await self.process()
        self.assertEqual(self.row.status, 'failed')


    async def test_all_page_failures_need_attention(self):
        self.runs.get_run.return_value = SimpleNamespace(status='completed_with_errors', completed_at=datetime.now(UTC), request_count=1, failed_request_count=1)
        await self.process()
        self.assertEqual(self.row.status, 'failed')

    async def test_discovery_only_dispatches_starting_urls_inside_sections(self):
        self.row.allowed_sections = ['https://example.org/products']
        self.resolver.resolve.return_value = ['https://example.org/products/one', 'https://example.org/jobs']
        await self.process()
        self.assertEqual(self.created.call_args.kwargs['urls'], ['https://example.org/products/one'])
        self.assertIn('https://example.org/products', self.resolver.resolve.call_args.args[0])

    async def test_no_matching_starting_urls_fails_without_dispatch(self):
        self.row.allowed_sections = ['https://example.org/docs']
        await self.process()
        self.assertEqual(self.row.status, 'failed')
        self.assertIn('allowed sections', self.row.error)
        self.created.assert_not_called()

    def test_section_boundaries_are_enforced_by_actual_edge_sql(self):
        from periplus.crawl.control.coverage_requests.schemas import within_allowed_sections
        self.row.resolved_urls = ['https://example.org/docs/stable/index']
        self.row.allowed_sections = ['https://example.org/docs/stable']
        urls = ['https://example.org/docs/stable', 'https://example.org/docs/stable/',
                'https://example.org/docs/stable/page?q=1', 'https://example.org/docs/stable?q=1',
                'https://example.org/docs/stable-old/page', 'https://example.org/docs/old',
                'https://sub.example.org/docs/stable/page', 'https://example.org.evil.test/docs/stable/page',
                'http://example.org/docs/stable/page', 'https://example.org:444/docs/stable/page',
                'https://example.org/jobs?next=/docs/stable/']
        with duckdb.connect() as con:
            con.execute('CREATE SCHEMA nav')
            con.execute('CREATE TABLE nav.links(target_url VARCHAR, target_host VARCHAR)')
            from urllib.parse import urlsplit
            con.executemany('INSERT INTO nav.links VALUES (?, ?)', [(url, urlsplit(url).hostname) for url in urls])
            self.assertEqual({row[0] for row in con.execute(coverage_plan(self.row).edges[0].sql).fetchall()}, set(urls[:4]))
        self.assertEqual([url for url in urls if within_allowed_sections(url, self.row.allowed_sections)], urls[:4])

    def test_link_scope_is_relative_to_starting_site(self):
        self.row.resolved_urls = ['https://www.example.org/products']
        with duckdb.connect() as con:
            con.execute('CREATE SCHEMA nav')
            con.execute("CREATE TABLE nav.links AS SELECT * FROM (VALUES ('https://example.org/a','example.org'), ('https://sub.example.org/b','sub.example.org'), ('https://elsewhere.net/c','elsewhere.net'), ('https://badexample.org/d','badexample.org')) t(target_url,target_host)")
            for scope, expected in [('internal', 2), ('external', 2), ('both', 4)]:
                self.row.link_scope = scope
                plan = coverage_plan(self.row)
                self.assertEqual(len(con.execute(plan.edges[0].sql).fetchall()), expected)
            self.row.depth = 0
            self.assertEqual(coverage_plan(self.row).edges, [])
