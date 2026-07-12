from __future__ import annotations

import unittest
from uuid import uuid4

from actions.crawl.service import _durable_crawl_id, _run_envelope
from runtime.context import (
    GraphExecutionContext,
    current_graph_execution,
    graph_execution_scope,
)


class GraphCrawlContextTests(unittest.TestCase):
    def test_scope_exposes_and_resets_frozen_request_provenance(self) -> None:
        context = GraphExecutionContext(
            graph_id=uuid4(),
            graph_run_id=uuid4(),
            graph_node_id=uuid4(),
            crawl_request_id=uuid4(),
            source_crawl_id=uuid4(),
            source_edge_id=uuid4(),
            effective_policy_snapshot_json={"config": {"mode": "static"}},
        )

        self.assertIsNone(current_graph_execution())
        with graph_execution_scope(context):
            self.assertIs(current_graph_execution(), context)
            envelope = _run_envelope(crawl_request_id=context.crawl_request_id)
            self.assertEqual(envelope.graph_id, context.graph_id)
            self.assertEqual(envelope.graph_run_id, context.graph_run_id)
            self.assertEqual(envelope.graph_node_id, context.graph_node_id)
            self.assertEqual(envelope.source_crawl_id, context.source_crawl_id)
            self.assertEqual(envelope.source_edge_id, context.source_edge_id)
        self.assertIsNone(current_graph_execution())

    def test_crawl_identity_is_the_retry_stable_request_identity(self) -> None:
        request_id = uuid4()
        self.assertEqual(
            _durable_crawl_id(crawl_request_id=request_id),
            request_id,
        )


if __name__ == "__main__":
    unittest.main()
