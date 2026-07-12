from __future__ import annotations

from datetime import UTC, datetime, timedelta
import asyncio
from threading import Event
from types import SimpleNamespace
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from control.crawl_graphs.schemas import EdgeDedupeMode, FrozenGraphEdge, FrozenGraphNode, FrozenGraphSnapshot
from runtime.graph_queue import EdgeWork, ReadinessWork, edge_evaluation_identity, get_crawl_request, get_graph_run, new_graph_run, normalize_request_url, request_identity, update_crawl_request
from runtime.graph_runs import EdgeEvaluationFailed, admit_request, deterministic_request_id, evaluate_edge, expire_graph_run, handle_readiness, reconcile_pending_admissions, request_cancellation
from runtime.graph_progress import EdgeProgress, edge_progress_key, initialize_run_progress
from workers.crawl import _process_crawl


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
        assert self.revisions[key] == last
        self.values[key] = value
        self.revisions[key] += 1
        return self.revisions[key]

    async def put(self, key: str, value: bytes) -> int:
        self.values[key] = value
        self.revisions[key] = self.revisions.get(key, 0) + 1
        return self.revisions[key]

    async def keys(self) -> list[str]:
        return list(self.values)


class FakeJetStream:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, payload: bytes, **_kwargs) -> None:
        self.messages.append((subject, payload))


def snapshot(*, entry: bool = True, self_edge: bool = False, dedupe_mode: EdgeDedupeMode = EdgeDedupeMode.graph) -> FrozenGraphSnapshot:
    graph_id = uuid4()
    source = FrozenGraphNode(id=uuid4(), name="source")
    target = source if self_edge else FrozenGraphNode(id=uuid4(), name="target")
    edge = FrozenGraphEdge(id=uuid4(), name="links", source_node_id=source.id, target_node_id=target.id, sql="SELECT url FROM materialized.page_links WHERE crawl_id = $crawl_id LIMIT 10", dedupe_mode=dedupe_mode)
    return FrozenGraphSnapshot(graph_id=graph_id, root_node_id=source.id if entry else uuid4(), nodes=[source, target] if source != target else [source], edges=[edge])


class GraphRuntimeTests(unittest.TestCase):
 def test_failed_acquisition_settles_after_durable_publication(self) -> None:
    async def scenario() -> None:
        runs, requests = FakeKV(), FakeKV()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        identity = request_identity(run.id, "https://example.com")
        request_id = deterministic_request_id(identity)
        from runtime.graph_queue import CrawlRequest, CrawlWork

        request = CrawlRequest(
            id=request_id,
            graph_run_id=run.id,
            node_id=graph.root_node_id,
            url="https://example.com/",
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())

        class Message:
            data = CrawlWork(crawl_request_id=request.id).model_dump_json().encode()
            ack = AsyncMock()

        @contextmanager
        def session():
            yield object()

        failure = SimpleNamespace(
            crawl_id=request.id,
            success=False,
            error="network failed",
            document_id=None,
        )
        with (
            patch("workers.crawl.session_scope", session),
            patch("workers.crawl.crawl_graph_request", AsyncMock(return_value=failure)),
            patch("workers.crawl.transition_node_progress", AsyncMock()),
            patch("workers.crawl.settle_request", AsyncMock()) as settle,
        ):
            await _process_crawl(
                Message(), runs, requests, object(), object(), object()
            )

        settle.assert_awaited_once()
        self.assertEqual(settle.await_args.kwargs["status"], "failed")
        self.assertEqual(settle.await_args.kwargs["error"], "network failed")
        Message.ack.assert_awaited_once()

    asyncio.run(scenario())

 def test_expired_crawl_claim_is_reclaimed(self) -> None:
    async def scenario() -> None:
        runs, requests = FakeKV(), FakeKV()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        from runtime.graph_queue import CrawlRequest, CrawlWork

        request = CrawlRequest(
            id=uuid4(),
            graph_run_id=run.id,
            node_id=graph.root_node_id,
            url="https://example.com/",
            status="crawling",
            claim_token=uuid4(),
            claim_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())

        class Message:
            data = CrawlWork(crawl_request_id=request.id).model_dump_json().encode()
            ack = AsyncMock()
            nak = AsyncMock()

        @contextmanager
        def session():
            yield object()

        page = SimpleNamespace(
            crawl_id=request.id,
            success=False,
            error="still failed",
            document_id=None,
        )
        acquire = AsyncMock(return_value=page)
        with (
            patch("workers.crawl.session_scope", session),
            patch("workers.crawl.crawl_graph_request", acquire),
            patch("workers.crawl.transition_node_progress", AsyncMock()),
            patch("workers.crawl.settle_request", AsyncMock()),
        ):
            await _process_crawl(
                Message(), runs, requests, object(), object(), object()
            )

        acquire.assert_awaited_once()
        Message.ack.assert_awaited_once()
        Message.nak.assert_not_awaited()

    asyncio.run(scenario())

 def test_contracts_and_identities(self) -> None:
    value = snapshot(); run = new_graph_run(value, ["https://EXAMPLE.com", "https://example.org/a#fragment"])
    self.assertEqual(run.trigger_urls, ("https://example.com/", "https://example.org/a"))
    with self.assertRaisesRegex(ValueError, "root node"): new_graph_run(snapshot(entry=False), ["https://example.com"])
    for url in ("relative", "ftp://example.com/a", "https:///missing-host"):
        with self.assertRaisesRegex(ValueError, "absolute HTTP"): normalize_request_url(url)
    self.assertEqual(
        normalize_request_url("HTTPS://Example.COM:443/a?z=2&a=1#fragment"),
        "https://example.com/a?a=1&z=2",
    )
    run_id, edge_id, first_crawl, second_crawl = uuid4(), uuid4(), uuid4(), uuid4()
    self.assertEqual(request_identity(run_id, "https://example.com/a#one"), request_identity(run_id, "https://EXAMPLE.com/a#two"))
    self.assertNotEqual(
        request_identity(run_id, "https://example.com/a", dedupe_mode=EdgeDedupeMode.crawl, source_edge_id=edge_id, source_crawl_id=first_crawl),
        request_identity(run_id, "https://example.com/a", dedupe_mode=EdgeDedupeMode.crawl, source_edge_id=edge_id, source_crawl_id=second_crawl),
    )
    self.assertEqual(
        request_identity(run_id, "https://example.com/a", dedupe_mode=EdgeDedupeMode.document, source_edge_id=edge_id, source_document_id="sha256:same"),
        request_identity(run_id, "https://example.com/a", dedupe_mode=EdgeDedupeMode.document, source_edge_id=edge_id, source_document_id="sha256:same"),
    )
    identity = request_identity(uuid4(), "https://example.com"); self.assertEqual(deterministic_request_id(identity), deterministic_request_id(identity))
    payload = ReadinessWork(event_id=uuid4(), crawl_id=uuid4(), graph_run_id=uuid4(), crawl_request_id=uuid4(), status="ready", occurred_at=datetime.now(UTC)); self.assertEqual(payload.failed_materialization_ids, ())

 def test_admission_is_idempotent_and_freezes_policy(self) -> None:
    async def scenario() -> None:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        run = new_graph_run(snapshot(), ["https://example.com"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        node = run.snapshot.nodes[0]
        resolver = lambda _url: {"id": str(uuid4()), "config": {"mode": "static"}}
        first, first_admitted = await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=node.id, url="https://example.com/a#one", policy_resolver=resolver)
        second, second_admitted = await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=node.id, url="https://EXAMPLE.com/a#two", policy_resolver=resolver)
        assert first is not None and second is not None and first.id == second.id
        assert first_admitted and not second_admitted
        assert first.effective_policy_snapshot_json is not None
        assert len(jetstream.messages) == 1

    asyncio.run(scenario())

 def test_cancellation_settles_every_nonterminal_request(self) -> None:
    async def scenario() -> None:
        runs, requests, progress = FakeKV(), FakeKV(), FakeKV()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"]).model_copy(update={
            "status": "running",
            "request_count": 2,
            "pending_request_count": 2,
        })
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        from runtime.graph_queue import CrawlRequest

        for url, status in (
            ("https://example.com/a", "crawling"),
            ("https://example.com/b", "awaiting_materializations"),
        ):
            request = CrawlRequest(
                id=uuid4(),
                graph_run_id=run.id,
                node_id=graph.root_node_id,
                url=url,
                status=status,
                created_at=run.created_at,
                updated_at=run.created_at,
            )
            await requests.create(request.id.hex, request.model_dump_json().encode())

        cancelled = await request_cancellation(
            runs, requests, run.id, progress=progress
        )
        self.assertEqual(cancelled.status, "cancelled")
        values = await requests.keys()
        for key in values:
            request = await get_crawl_request(requests, UUID(key))
            assert request is not None
            self.assertEqual(request.status, "cancelled")

    asyncio.run(scenario())

 def test_run_expiry_does_not_need_a_later_admission(self) -> None:
    async def scenario() -> None:
        runs, requests, progress = FakeKV(), FakeKV(), FakeKV()
        graph = snapshot()
        now = datetime(2026, 1, 1, 1, tzinfo=UTC)
        run = new_graph_run(
            graph,
            ["https://example.com"],
            now=now - timedelta(hours=1),
        ).model_copy(update={"status": "running"})
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)

        with patch("runtime.graph_runs.get_int", return_value=60):
            expired = await expire_graph_run(
                runs=runs,
                requests=requests,
                progress=progress,
                run=run,
                now=now,
            )

        self.assertEqual(expired.status, "failed")
        self.assertIn("ATLAS_GRAPH_MAX_RUN_SECONDS=60", expired.error or "")

    asyncio.run(scenario())

 def test_admission_reconciles_a_publish_failure(self) -> None:
    async def scenario() -> None:
        runs, requests, progress = FakeKV(), FakeKV(), FakeKV()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)

        class FailingJetStream(FakeJetStream):
            def __init__(self) -> None:
                super().__init__()
                self.fail = True

            async def publish(self, subject: str, payload: bytes, **kwargs) -> None:
                if self.fail:
                    self.fail = False
                    raise RuntimeError("ambiguous publish")
                await super().publish(subject, payload, **kwargs)

        jetstream = FailingJetStream()
        with self.assertRaisesRegex(RuntimeError, "ambiguous publish"):
            await admit_request(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                run_id=run.id,
                node_id=graph.root_node_id,
                url="https://example.com/a",
                policy_resolver=lambda _url: None,
            )

        reserved = await get_graph_run(runs, run.id)
        assert reserved is not None
        self.assertEqual(len(reserved.pending_admissions), 1)
        self.assertEqual(reserved.request_count, 1)

        await reconcile_pending_admissions(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            run=reserved,
        )

        reconciled = await get_graph_run(runs, run.id)
        assert reconciled is not None
        self.assertEqual(reconciled.pending_admissions, ())
        self.assertEqual(len(jetstream.messages), 1)

    asyncio.run(scenario())


 def test_edge_redelivery_does_not_duplicate_target_request(self) -> None:
    async def scenario() -> None:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        source, _admitted = await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=graph.nodes[0].id, url="https://example.com", policy_resolver=lambda _url: None)
        assert source is not None
        work = EdgeWork(graph_run_id=run.id, crawl_request_id=source.id, crawl_id=uuid4(), edge_id=graph.edges[0].id)
        calls = []
        def execute(sql, parameters):
            calls.append((sql, parameters))
            return ["https://target.example/a"]
        first = await evaluate_edge(runs=runs, requests=requests, progress=progress, jetstream=jetstream, work=work, execute_urls=execute, policy_resolver=lambda _url: None)
        second = await evaluate_edge(runs=runs, requests=requests, progress=progress, jetstream=jetstream, work=work, execute_urls=execute, policy_resolver=lambda _url: None)
        assert first == second == 1
        assert calls == [(graph.edges[0].sql, {"crawl_id": work.crawl_id})]
        # One source publication and one target publication; redelivery adds neither.
        assert len(jetstream.messages) == 2
        edge_state = EdgeProgress.model_validate_json(
            (await progress.get(edge_progress_key(run.id, graph.edges[0].id))).value
        )
        assert edge_state.urls_selected == edge_state.urls_admitted == 1
        assert edge_state.urls_deduplicated == 0
        assert edge_state.evaluations_completed == 1

    asyncio.run(scenario())

 def test_edge_timeout_interrupts_query_and_fails_request(self) -> None:
    async def scenario() -> None:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        source, admitted = await admit_request(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            run_id=run.id,
            node_id=graph.nodes[0].id,
            url="https://example.com",
            policy_resolver=lambda _url: None,
        )
        assert source is not None and admitted

        class InterruptibleQuery:
            def __init__(self) -> None:
                self.release = Event()
                self.interrupted = False

            def __call__(self, _sql, _parameters):
                self.release.wait(timeout=1)
                return []

            def interrupt(self) -> None:
                self.interrupted = True
                self.release.set()

        query = InterruptibleQuery()
        with patch(
            "runtime.graph_runs.get_float",
            side_effect=lambda name: 0.01 if name == "ATLAS_EDGE_QUERY_TIMEOUT_SECONDS" else 60,
        ):
            with self.assertRaisesRegex(EdgeEvaluationFailed, "execution-time limit"):
                await evaluate_edge(
                    runs=runs,
                    requests=requests,
                    progress=progress,
                    jetstream=jetstream,
                    work=EdgeWork(
                        graph_run_id=run.id,
                        crawl_request_id=source.id,
                        crawl_id=uuid4(),
                        edge_id=graph.edges[0].id,
                    ),
                    execute_urls=query,
                    policy_resolver=lambda _url: None,
                )

        failed = await get_crawl_request(requests, source.id)
        assert failed is not None
        self.assertTrue(query.interrupted)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.failure_stage, "edge")

    asyncio.run(scenario())

 def test_edge_dedupe_modes_apply_after_sql_selection(self) -> None:
    async def run_mode(mode: EdgeDedupeMode) -> tuple[int, int]:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        graph = snapshot(dedupe_mode=mode)
        run = new_graph_run(graph, ["https://source.example/one"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        sources = []
        for path in ("one", "two"):
            source, admitted = await admit_request(
                runs=runs, requests=requests, progress=progress, jetstream=jetstream,
                run_id=run.id, node_id=graph.nodes[0].id,
                url=f"https://source.example/{path}", policy_resolver=lambda _url: None,
            )
            assert source is not None and admitted
            source = await update_crawl_request(
                requests,
                source.id,
                lambda value: value.model_copy(update={"document_id": "sha256:same-document"}),
            )
            sources.append(source)
        for source in sources:
            await evaluate_edge(
                runs=runs, requests=requests, progress=progress, jetstream=jetstream,
                work=EdgeWork(
                    graph_run_id=run.id, crawl_request_id=source.id,
                    crawl_id=source.id, edge_id=graph.edges[0].id,
                ),
                execute_urls=lambda _sql, _parameters: ["https://target.example/same"],
                policy_resolver=lambda _url: None,
            )
        state = EdgeProgress.model_validate_json(
            (await progress.get(edge_progress_key(run.id, graph.edges[0].id))).value
        )
        return state.urls_admitted, state.urls_deduplicated

    async def scenario() -> None:
        self.assertEqual(await run_mode(EdgeDedupeMode.graph), (1, 1))
        self.assertEqual(await run_mode(EdgeDedupeMode.crawl), (2, 0))
        self.assertEqual(await run_mode(EdgeDedupeMode.document), (1, 1))

    asyncio.run(scenario())

 def test_graph_mode_sees_urls_admitted_by_narrower_modes(self) -> None:
    async def scenario() -> None:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        graph = snapshot()
        run = new_graph_run(graph, ["https://source.example/"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        edge_id, crawl_id = uuid4(), uuid4()
        first, first_admitted = await admit_request(
            runs=runs, requests=requests, progress=progress, jetstream=jetstream,
            run_id=run.id, node_id=graph.nodes[0].id, url="https://target.example/same",
            policy_resolver=lambda _url: None, dedupe_mode=EdgeDedupeMode.crawl,
            source_edge_id=edge_id, source_crawl_id=crawl_id,
        )
        second, second_admitted = await admit_request(
            runs=runs, requests=requests, progress=progress, jetstream=jetstream,
            run_id=run.id, node_id=graph.nodes[-1].id, url="https://target.example/same",
            policy_resolver=lambda _url: None,
        )
        self.assertIsNotNone(first)
        self.assertTrue(first_admitted)
        self.assertIsNone(second)
        self.assertFalse(second_admitted)

    asyncio.run(scenario())

 def test_platform_ceiling_names_are_exact(self) -> None:
    from runtime.graph_runs import _ceiling_error
    run = new_graph_run(snapshot(), ["https://example.com"], now=datetime(2026, 1, 1, tzinfo=UTC)).model_copy(update={"request_count": 10})
    with patch("runtime.graph_runs.get_int", side_effect=lambda name: {"ATLAS_GRAPH_MAX_REQUESTS_PER_RUN": 10, "ATLAS_GRAPH_MAX_RUN_SECONDS": 3600}[name]):
       self.assertIn("ATLAS_GRAPH_MAX_REQUESTS_PER_RUN=10", _ceiling_error(run, datetime(2026, 1, 1, tzinfo=UTC)) or "")

 def test_successful_materialization_requeue_reopens_failed_request(self) -> None:
    async def scenario() -> None:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"]).model_copy(update={
            "status": "completed_with_errors",
            "request_count": 1,
            "pending_request_count": 0,
            "failed_request_count": 1,
            "completed_at": datetime.now(UTC),
        })
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        request_id = uuid4()
        from runtime.graph_queue import CrawlRequest

        request = CrawlRequest(
            id=request_id,
            graph_run_id=run.id,
            node_id=graph.root_node_id,
            url="https://example.com/",
            document_id="sha256:document",
            status="failed",
            error="materialization failed",
            failure_stage="enrichment",
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())
        await handle_readiness(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            event=ReadinessWork(
                event_id=uuid4(),
                crawl_id=request.id,
                graph_run_id=run.id,
                crawl_request_id=request.id,
                status="ready",
                occurred_at=datetime.now(UTC),
            ),
        )

        reopened_run = await get_graph_run(runs, run.id)
        reopened_request = await get_crawl_request(requests, request.id)
        assert reopened_run is not None and reopened_request is not None
        self.assertEqual(reopened_run.status, "running")
        self.assertEqual(reopened_run.pending_request_count, 1)
        self.assertEqual(reopened_run.failed_request_count, 0)
        self.assertEqual(reopened_request.status, "evaluating_edges")
        self.assertIsNone(reopened_request.failure_stage)
        self.assertEqual(jetstream.messages[-1][0], "atlas.graph.edge")

    asyncio.run(scenario())
