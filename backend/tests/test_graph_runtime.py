from __future__ import annotations

from datetime import UTC, datetime
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from control.crawl_graphs.schemas import FrozenGraphEdge, FrozenGraphNode, FrozenGraphSnapshot
from runtime.graph_queue import EdgeWork, ReadinessWork, edge_evaluation_identity, new_graph_run, normalize_request_url, request_identity
from runtime.graph_runs import admit_request, deterministic_request_id, evaluate_edge
from runtime.graph_progress import EdgeProgress, edge_progress_key, initialize_run_progress


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


class FakeJetStream:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, payload: bytes, **_kwargs) -> None:
        self.messages.append((subject, payload))


def snapshot(*, entry: bool = True, self_edge: bool = False) -> FrozenGraphSnapshot:
    graph_id = uuid4()
    source = FrozenGraphNode(id=uuid4(), name="source")
    target = source if self_edge else FrozenGraphNode(id=uuid4(), name="target")
    edge = FrozenGraphEdge(id=uuid4(), name="links", source_node_id=source.id, target_node_id=target.id, sql="SELECT url FROM materialized.page_links WHERE crawl_id = $crawl_id LIMIT 10")
    return FrozenGraphSnapshot(graph_id=graph_id, root_node_id=source.id if entry else uuid4(), nodes=[source, target] if source != target else [source], edges=[edge])


class GraphRuntimeTests(unittest.TestCase):
 def test_contracts_and_identities(self) -> None:
    value = snapshot(); run = new_graph_run(value, ["https://EXAMPLE.com", "https://example.org/a#fragment"])
    self.assertEqual(run.trigger_urls, ("https://example.com/", "https://example.org/a"))
    with self.assertRaisesRegex(ValueError, "root node"): new_graph_run(snapshot(entry=False), ["https://example.com"])
    for url in ("relative", "ftp://example.com/a", "https:///missing-host"):
        with self.assertRaisesRegex(ValueError, "absolute HTTP"): normalize_request_url(url)
    run_id, first, second = uuid4(), uuid4(), uuid4()
    self.assertEqual(request_identity(run_id, first, "https://example.com/a#one"), request_identity(run_id, first, "https://EXAMPLE.com/a#two"))
    self.assertNotEqual(request_identity(run_id, first, "https://example.com/a"), request_identity(run_id, second, "https://example.com/a"))
    identity = request_identity(uuid4(), uuid4(), "https://example.com"); self.assertEqual(deterministic_request_id(identity), deterministic_request_id(identity))
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

 def test_platform_ceiling_names_are_exact(self) -> None:
    from runtime.graph_runs import _ceiling_error
    run = new_graph_run(snapshot(), ["https://example.com"], now=datetime(2026, 1, 1, tzinfo=UTC)).model_copy(update={"request_count": 10})
    with patch("runtime.graph_runs.get_int", side_effect=lambda name: {"ATLAS_GRAPH_MAX_REQUESTS_PER_RUN": 10, "ATLAS_GRAPH_MAX_RUN_SECONDS": 3600}[name]):
        self.assertIn("ATLAS_GRAPH_MAX_REQUESTS_PER_RUN=10", _ceiling_error(run, datetime(2026, 1, 1, tzinfo=UTC)) or "")
