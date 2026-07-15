from __future__ import annotations

from datetime import UTC, datetime, timedelta
import asyncio
from threading import Event
from types import SimpleNamespace
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from actions.crawl.schemas import CrawlPage
from actions.crawl.service import RetryableAcquisitionError
from control.crawl_graphs.schemas import EdgeDedupeMode, FrozenGraphEdge, FrozenGraphNode, FrozenGraphSnapshot
from control.crawl_policies.schemas import DEFAULT_HTTP_USER_AGENT
from runtime.graph_queue import EdgeWork, ReadinessWork, edge_evaluation_identity, get_crawl_request, get_graph_run, new_graph_run, normalize_request_url, request_identity, update_crawl_request
from runtime.graph_runs import EdgeEvaluationFailed, admit_request, deterministic_request_id, evaluate_edge, expire_graph_run, handle_readiness, reconcile_pending_admissions, request_cancellation, settle_request
from runtime.graph_progress import EdgeProgress, edge_progress_key, initialize_run_progress
from runtime.navigation_contract import NavigationPackage
from workers.acquisition import _process_crawl


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


def navigation_package() -> NavigationPackage:
    return NavigationPackage(
        object_name="runtime/navigation/test.arrow",
        sha256="0" * 64,
        schema_version=1,
        recipe="recipe",
        row_count=1,
        byte_size=10,
    )


def policy_snapshot(profile: str = "http") -> dict:
    config = {"user_agent": DEFAULT_HTTP_USER_AGENT} if profile == "http" else {}
    return {
        "id": str(uuid4()),
        "slug": f"{profile}-test",
        "scheme": "*",
        "host": "*",
        "path_prefix": "/",
        "path_mode": "prefix",
        "max_concurrency": 4,
        "profile": {
            "id": str(uuid4()),
            "slug": f"{profile}-profile",
            "name": f"{profile.title()} test",
            "transport": profile,
            "config": config,
            "cost_rank": 10,
        },
        "trial_candidate": {
            "id": str(uuid4()),
            "slug": "rendered",
            "name": "Rendered",
            "transport": "browser",
            "config": {"mode": "static", "wait": "none"},
            "cost_rank": 20,
        },
    }


class FakeJetStream:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes]] = []
        self.buckets: dict[str, FakeKV] = {}

    async def publish(self, subject: str, payload: bytes, **_kwargs) -> None:
        self.messages.append((subject, payload))

    async def key_value(self, bucket: str) -> FakeKV:
        return self.buckets.setdefault(bucket, FakeKV())


def snapshot(*, entry: bool = True, self_edge: bool = False, dedupe_mode: EdgeDedupeMode = EdgeDedupeMode.graph) -> FrozenGraphSnapshot:
    graph_id = uuid4()
    source = FrozenGraphNode(id=uuid4(), name="source")
    target = source if self_edge else FrozenGraphNode(id=uuid4(), name="target")
    edge = FrozenGraphEdge(id=uuid4(), name="links", source_node_id=source.id, target_node_id=target.id, sql="SELECT url FROM page.links WHERE crawl_id = $crawl_id LIMIT 10", dedupe_mode=dedupe_mode)
    return FrozenGraphSnapshot(graph_id=graph_id, root_node_id=source.id if entry else uuid4(), nodes=[source, target] if source != target else [source], edges=[edge])


class GraphRuntimeTests(unittest.TestCase):
 def setUp(self) -> None:
    trial_patch = patch("runtime.graph_runs._trial_for_request", return_value=None)
    trial_patch.start()
    self.addCleanup(trial_patch.stop)

 def test_invalid_acquisition_work_is_terminated(self) -> None:
    async def scenario() -> None:
        message = SimpleNamespace(
            data=b"not-json",
            term=AsyncMock(),
            ack=AsyncMock(),
            nak=AsyncMock(),
        )

        await _process_crawl(
            message,
            object(),
            object(),
            object(),
            object(),
            object(),
            "http",
        )

        message.term.assert_awaited_once()
        message.ack.assert_not_awaited()
        message.nak.assert_not_awaited()

    asyncio.run(scenario())

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
            transport="http",
            effective_policy_snapshot_json=policy_snapshot(),
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())

        class Message:
            data = CrawlWork(crawl_request_id=request.id, transport="http").model_dump_json().encode()
            ack = AsyncMock()

        failure = SimpleNamespace(
            crawl_id=request.id,
            success=False,
            error="network failed",
            document_id=None,
        )
        with (
            patch("workers.acquisition.crawl_graph_request", AsyncMock(return_value=failure)),
            patch("workers.acquisition.transition_node_progress", AsyncMock()),
            patch("workers.acquisition.settle_request", AsyncMock()) as settle,
        ):
            await _process_crawl(
                Message(), runs, requests, object(), object(), object(), "http"
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
            transport="http",
            effective_policy_snapshot_json=policy_snapshot(),
            status="crawling",
            claim_token=uuid4(),
            claim_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())

        class Message:
            data = CrawlWork(crawl_request_id=request.id, transport="http").model_dump_json().encode()
            ack = AsyncMock()
            nak = AsyncMock()

        page = SimpleNamespace(
            crawl_id=request.id,
            success=False,
            error="still failed",
            document_id=None,
        )
        acquire = AsyncMock(return_value=page)
        with (
            patch("workers.acquisition.crawl_graph_request", acquire),
            patch("workers.acquisition.transition_node_progress", AsyncMock()),
            patch("workers.acquisition.settle_request", AsyncMock()),
        ):
            await _process_crawl(
                Message(), runs, requests, object(), object(), object(), "http"
            )

        acquire.assert_awaited_once()
        Message.ack.assert_awaited_once()
        Message.nak.assert_not_awaited()

    asyncio.run(scenario())

 def test_acquisition_infrastructure_failure_releases_claim_for_redelivery(self) -> None:
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
            transport="http",
            effective_policy_snapshot_json=policy_snapshot(),
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())

        class Message:
            data = CrawlWork(crawl_request_id=request.id, transport="http").model_dump_json().encode()
            metadata = SimpleNamespace(num_delivered=50)
            ack = AsyncMock()
            nak = AsyncMock()

        with (
            patch("workers.acquisition.crawl_graph_request", AsyncMock(side_effect=OSError("NATS unavailable"))),
            patch("workers.acquisition.transition_node_progress", AsyncMock()),
            patch("workers.acquisition.settle_request", AsyncMock()) as settle,
        ):
            await _process_crawl(
                Message(), runs, requests, object(), object(), object(), "http"
            )

        current = await get_crawl_request(requests, request.id)
        assert current is not None
        self.assertEqual(current.status, "queued")
        self.assertIsNone(current.claim_token)
        self.assertEqual(current.processing_failure_count, 0)
        Message.nak.assert_awaited_once_with(delay=1)
        Message.ack.assert_not_awaited()
        settle.assert_not_awaited()

    asyncio.run(scenario())

 def test_retryable_acquisition_uses_retry_after_without_settling(self) -> None:
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
            transport="http",
            effective_policy_snapshot_json=policy_snapshot(),
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())

        class Message:
            data = CrawlWork(crawl_request_id=request.id, transport="http").model_dump_json().encode()
            metadata = SimpleNamespace(num_delivered=50)
            ack = AsyncMock()
            nak = AsyncMock()

        retryable = RetryableAcquisitionError(
            CrawlPage(
                url=request.url,
                success=False,
                status_code=429,
                duration_seconds=0.1,
                error="HTTP 429: slow down",
                failure_code="http_status",
                failure_stage="request",
                failure_retryable=True,
                retry_after_seconds=12,
            )
        )
        acquire = AsyncMock(side_effect=retryable)
        with (
            patch("workers.acquisition.crawl_graph_request", acquire),
            patch("workers.acquisition.transition_node_progress", AsyncMock()),
            patch("workers.acquisition.settle_request", AsyncMock()) as settle,
        ):
            await _process_crawl(
                Message(), runs, requests, object(), object(), object(), "http"
            )

        current = await get_crawl_request(requests, request.id)
        assert current is not None
        self.assertEqual(current.status, "queued")
        self.assertEqual(current.processing_failure_count, 1)
        Message.nak.assert_awaited_once_with(delay=12)
        Message.ack.assert_not_awaited()
        settle.assert_not_awaited()
        self.assertFalse(acquire.await_args.kwargs["persist_retryable_failure"])

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
    package = NavigationPackage(object_name="runtime/navigation/test.arrow", sha256="0" * 64, schema_version=1, recipe="recipe", row_count=1, byte_size=10)
    payload = ReadinessWork(event_id=uuid4(), crawl_id=uuid4(), graph_run_id=uuid4(), crawl_request_id=uuid4(), navigation=package, occurred_at=datetime.now(UTC)); self.assertEqual(payload.navigation, package)

 def test_admission_is_idempotent_and_freezes_policy(self) -> None:
    async def scenario() -> None:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        run = new_graph_run(snapshot(), ["https://example.com"])
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        node = run.snapshot.nodes[0]
        resolver = lambda _url: policy_snapshot("browser")
        first, first_admitted = await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=node.id, url="https://example.com/a#one", policy_resolver=resolver)
        second, second_admitted = await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=node.id, url="https://EXAMPLE.com/a#two", policy_resolver=resolver)
        assert first is not None and second is not None and first.id == second.id
        assert first_admitted and not second_admitted
        assert first.effective_policy_snapshot_json is not None
        assert first.transport == "browser"
        assert jetstream.messages[0][0] == "atlas.graph.crawl.browser"
        assert len(jetstream.messages) == 1
        current = await get_graph_run(runs, run.id)
        assert current is not None and current.last_progress_at is not None

    asyncio.run(scenario())

 def test_settlement_records_last_progress(self) -> None:
    async def scenario() -> None:
        runs, requests, progress = FakeKV(), FakeKV(), FakeKV()
        graph = snapshot()
        started = datetime(2026, 1, 1, tzinfo=UTC)
        settled_at = started + timedelta(minutes=2)
        run = new_graph_run(graph, ["https://example.com"], now=started).model_copy(
            update={"status": "running", "request_count": 1, "pending_request_count": 1}
        )
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        from runtime.graph_queue import CrawlRequest

        request = CrawlRequest(
            id=uuid4(),
            graph_run_id=run.id,
            node_id=graph.root_node_id,
            url="https://example.com/",
            transport="http",
            effective_policy_snapshot_json=policy_snapshot(),
            status="awaiting_navigation",
            created_at=started,
            updated_at=started,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())

        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="completed",
            now=settled_at,
        )

        current = await get_graph_run(runs, run.id)
        assert current is not None
        self.assertEqual(current.status, "completed")
        self.assertEqual(current.last_progress_at, settled_at)

    asyncio.run(scenario())

 def test_navigation_readiness_activates_edges_without_materialization_state(self) -> None:
    async def scenario() -> None:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com"]).model_copy(
            update={"status": "running", "request_count": 1, "pending_request_count": 1}
        )
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        from runtime.graph_queue import CrawlRequest

        request = CrawlRequest(
            id=uuid4(),
            graph_run_id=run.id,
            node_id=graph.root_node_id,
            url="https://example.com/",
            transport="http",
            effective_policy_snapshot_json=policy_snapshot(),
            status="awaiting_navigation",
            created_at=run.created_at,
            updated_at=run.created_at,
        )
        await requests.create(request.id.hex, request.model_dump_json().encode())
        package = navigation_package()

        await handle_readiness(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            event=ReadinessWork(
                event_id=uuid4(),
                crawl_id=uuid4(),
                graph_run_id=run.id,
                crawl_request_id=request.id,
                navigation=package,
                occurred_at=datetime.now(UTC),
            ),
        )

        self.assertEqual(len(jetstream.messages), 1)
        work = EdgeWork.model_validate_json(jetstream.messages[0][1])
        self.assertEqual(work.navigation, package)
        current = await get_crawl_request(requests, request.id)
        assert current is not None
        self.assertEqual(current.status, "evaluating_edges")

    asyncio.run(scenario())

 def test_artifact_readiness_completes_without_activating_html_edges(self) -> None:
    async def scenario() -> None:
        runs, requests, progress, jetstream = FakeKV(), FakeKV(), FakeKV(), FakeJetStream()
        graph = snapshot()
        run = new_graph_run(graph, ["https://example.com/report.pdf"]).model_copy(
            update={"status": "running", "request_count": 1, "pending_request_count": 1}
        )
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        from runtime.graph_queue import CrawlRequest

        request = CrawlRequest(
            id=uuid4(),
            graph_run_id=run.id,
            node_id=graph.root_node_id,
            url="https://example.com/report.pdf",
            transport="http",
            artifact_id="sha256:" + "a" * 64,
            effective_policy_snapshot_json=policy_snapshot(),
            status="awaiting_navigation",
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
                crawl_id=uuid4(),
                graph_run_id=run.id,
                crawl_request_id=request.id,
                navigation=None,
                occurred_at=datetime.now(UTC),
            ),
        )

        self.assertEqual(jetstream.messages, [])
        current = await get_crawl_request(requests, request.id)
        assert current is not None
        self.assertEqual(current.status, "completed")

    asyncio.run(scenario())

 def test_settlement_separates_external_warnings_from_atlas_errors(self) -> None:
    async def scenario() -> None:
        runs, requests, progress = FakeKV(), FakeKV(), FakeKV()
        graph = snapshot()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        run = new_graph_run(graph, ["https://example.com"], now=now).model_copy(
            update={"status": "running", "request_count": 2, "pending_request_count": 2}
        )
        await runs.create(run.id.hex, run.model_dump_json().encode())
        await initialize_run_progress(progress, run)
        from runtime.graph_queue import CrawlRequest

        crawl_requests = []
        for path in ("external", "pipeline"):
            crawl_request = CrawlRequest(
                id=uuid4(),
                graph_run_id=run.id,
                node_id=graph.root_node_id,
                url=f"https://example.com/{path}",
                transport="http",
                effective_policy_snapshot_json=policy_snapshot(),
                status="crawling",
                created_at=now,
                updated_at=now,
            )
            await requests.create(
                crawl_request.id.hex, crawl_request.model_dump_json().encode()
            )
            crawl_requests.append(crawl_request)

        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=crawl_requests[0].id,
            status="failed",
            failure_stage="acquisition",
            now=now,
        )
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=crawl_requests[1].id,
            status="failed",
            failure_stage="edge",
            now=now,
        )

        current = await get_graph_run(runs, run.id)
        assert current is not None
        self.assertEqual(current.status, "completed_with_errors")
        self.assertEqual(current.failed_request_count, 2)
        self.assertEqual(current.warning_count, 1)
        self.assertEqual(current.error_count, 1)

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
            ("https://example.com/b", "awaiting_navigation"),
        ):
            request = CrawlRequest(
                id=uuid4(),
                graph_run_id=run.id,
                node_id=graph.root_node_id,
                url=url,
                transport="http",
                effective_policy_snapshot_json=policy_snapshot(),
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
        self.assertEqual(expired.error_count, 1)
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
                policy_resolver=lambda _url: policy_snapshot(),
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
        source, _admitted = await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=graph.nodes[0].id, url="https://example.com", policy_resolver=lambda _url: policy_snapshot())
        assert source is not None
        work = EdgeWork(graph_run_id=run.id, crawl_request_id=source.id, crawl_id=uuid4(), edge_id=graph.edges[0].id, navigation=navigation_package())
        calls = []
        def execute(sql, parameters):
            calls.append((sql, parameters))
            return ["https://target.example/a"]
        first = await evaluate_edge(runs=runs, requests=requests, progress=progress, jetstream=jetstream, work=work, execute_urls=execute, policy_resolver=lambda _url: policy_snapshot())
        second = await evaluate_edge(runs=runs, requests=requests, progress=progress, jetstream=jetstream, work=work, execute_urls=execute, policy_resolver=lambda _url: policy_snapshot())
        assert first == second == 1
        assert calls == [
            (
                graph.edges[0].sql,
                {
                    "crawl_id": work.crawl_id,
                    "_page_url": source.url,
                    "_document_id": source.document_id,
                },
            )
        ]
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
            policy_resolver=lambda _url: policy_snapshot(),
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
                        navigation=navigation_package(),
                    ),
                    execute_urls=query,
                    policy_resolver=lambda _url: policy_snapshot(),
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
                url=f"https://source.example/{path}", policy_resolver=lambda _url: policy_snapshot(),
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
                    navigation=navigation_package(),
                ),
                execute_urls=lambda _sql, _parameters: ["https://target.example/same"],
                policy_resolver=lambda _url: policy_snapshot(),
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
            policy_resolver=lambda _url: policy_snapshot(), dedupe_mode=EdgeDedupeMode.crawl,
            source_edge_id=edge_id, source_crawl_id=crawl_id,
        )
        second, second_admitted = await admit_request(
            runs=runs, requests=requests, progress=progress, jetstream=jetstream,
            run_id=run.id, node_id=graph.nodes[-1].id, url="https://target.example/same",
            policy_resolver=lambda _url: policy_snapshot(),
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
