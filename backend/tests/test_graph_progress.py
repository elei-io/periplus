from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from uuid import uuid4

from control.crawl_graphs.schemas import FrozenGraphEdge, FrozenGraphNode, FrozenGraphSnapshot
from runtime.graph_progress import edge_progress, node_progress
from runtime.graph_queue import CrawlRequest, EdgeEvaluation, GraphRun, edge_evaluation_key
from tests.test_graph_runtime import policy_snapshot


class FakeKV:
    def __init__(self, values) -> None:
        self.values = values

    async def keys(self):
        return list(self.values)

    async def get(self, key):
        return SimpleNamespace(value=self.values[key])


class GraphProgressTests(unittest.TestCase):
    def test_component_progress_is_derived_from_current_request_state(self) -> None:
        async def scenario() -> None:
            now = datetime.now(UTC)
            graph_id, run_id, source_id, target_id, edge_id = (uuid4() for _ in range(5))
            snapshot = FrozenGraphSnapshot(
                graph_id=graph_id,
                root_node_id=source_id,
                nodes=[FrozenGraphNode(id=source_id, name="source"), FrozenGraphNode(id=target_id, name="target")],
                edges=[FrozenGraphEdge(id=edge_id, name="links", source_node_id=source_id, target_node_id=target_id, sql="SELECT url WHERE crawl_id = $crawl_id LIMIT 10")],
            )
            run = GraphRun(id=run_id, graph_id=graph_id, trigger_kind="manual", status="running", snapshot=snapshot, trigger_urls=("https://example.com",), created_at=now)
            source = CrawlRequest(id=uuid4(), graph_run_id=run_id, node_id=source_id, url="https://example.com/", effective_policy_snapshot_json=policy_snapshot(), status="evaluating_edges", created_at=now, updated_at=now)
            target = CrawlRequest(id=uuid4(), graph_run_id=run_id, node_id=target_id, url="https://target.example/", effective_policy_snapshot_json=policy_snapshot(), source_edge_id=edge_id, status="crawling", created_at=now, updated_at=now)
            evaluation = EdgeEvaluation(identity="evaluation", graph_run_id=run_id, crawl_request_id=source.id, crawl_id=source.id, edge_id=edge_id, status="running", output_count=3, created_at=now, updated_at=now)
            bucket = FakeKV({source.id.hex: source.model_dump_json().encode(), target.id.hex: target.model_dump_json().encode(), edge_evaluation_key(evaluation.identity): evaluation.model_dump_json().encode()})

            node = await node_progress(bucket, run, target_id)
            self.assertEqual((node.admitted, node.crawling), (1, 1))
            edge = await edge_progress(bucket, run, edge_id)
            self.assertEqual((edge.urls_selected, edge.urls_admitted, edge.urls_deduplicated), (3, 1, 2))
            self.assertEqual(edge.evaluations_running, 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
