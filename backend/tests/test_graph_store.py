from __future__ import annotations

from datetime import UTC, datetime
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from control.crawl_graphs.schemas import EdgeDedupeMode
from db import Base
from runtime.graph_models import (
    CrawlRequestRecord,
    EdgeEvaluationRecord,
    GraphAdmissionRecord,
    GraphOutboxRecord,
    GraphRunRecord,
)
from runtime.graph_queue import new_graph_run
from runtime.graph_queue import (
    EdgeEvaluation,
    EdgeWork,
    NavigationReadinessWork,
    edge_evaluation_identity,
)
from runtime.graph_runs import create_graph_run
from runtime.graph_store import AsyncGraphRuntimeStore, GraphRuntimeStore
from tests.graph_fixtures import policy_snapshot, snapshot


class GraphRuntimeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            self.engine,
            tables=[
                GraphRunRecord.__table__,
                CrawlRequestRecord.__table__,
                GraphAdmissionRecord.__table__,
                EdgeEvaluationRecord.__table__,
                GraphOutboxRecord.__table__,
            ],
        )
        self.sessions = sessionmaker(
            self.engine, expire_on_commit=False
        )
        self.store = GraphRuntimeStore(self.sessions)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _run(self, *, max_crawls: int = 10):
        run = new_graph_run(
            snapshot(),
            ["https://example.com/"],
            max_crawls=max_crawls,
        )
        return self.store.create_run(run)

    def test_admission_atomically_creates_state_budget_and_outbox(self) -> None:
        run = self._run()
        admitted = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/",
            effective_policy_snapshot=policy_snapshot(),
            now=datetime.now(UTC),
        )

        self.assertTrue(admitted.created)
        assert admitted.request is not None
        current = self.store.get_run(run.id)
        assert current is not None
        self.assertEqual(current.status, "running")
        self.assertEqual(current.request_count, 1)
        self.assertEqual(current.pending_request_count, 1)
        self.assertEqual(current.acquisition_pending_count, 1)
        with self.sessions() as session:
            outbox = session.scalar(select(GraphOutboxRecord))
            assert outbox is not None
            self.assertEqual(outbox.subject, "atlas.graph.crawl")
            self.assertEqual(
                outbox.payload["crawl_request_id"],
                str(admitted.request.id),
            )
            self.assertEqual(outbox.payload["generation"], 1)

    def test_admission_deduplicates_without_spending_budget_twice(self) -> None:
        run = self._run()
        arguments = {
            "run_id": run.id,
            "node_id": run.snapshot.root_node_id,
            "url": "https://example.com/",
            "effective_policy_snapshot": policy_snapshot(),
        }
        first = self.store.admit_request(**arguments)
        repeated = self.store.admit_request(**arguments)

        self.assertTrue(first.created)
        self.assertFalse(repeated.created)
        assert first.request is not None and repeated.request is not None
        self.assertEqual(first.request.id, repeated.request.id)
        current = self.store.get_run(run.id)
        assert current is not None
        self.assertEqual(current.request_count, 1)
        with self.sessions() as session:
            self.assertEqual(
                session.query(GraphOutboxRecord).count(),
                1,
            )

    def test_graph_alias_prevents_weaker_later_deduplication(self) -> None:
        run = self._run()
        source_crawl_id = uuid4()
        source_edge_id = uuid4()
        first = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/page",
            effective_policy_snapshot=policy_snapshot(),
            dedupe_mode=EdgeDedupeMode.crawl,
            source_crawl_id=source_crawl_id,
            source_edge_id=source_edge_id,
        )
        repeated = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/page",
            effective_policy_snapshot=policy_snapshot(),
        )

        self.assertTrue(first.created)
        self.assertFalse(repeated.created)
        assert first.request is not None and repeated.request is not None
        self.assertEqual(first.request.id, repeated.request.id)
        current = self.store.get_run(run.id)
        assert current is not None
        self.assertEqual(current.request_count, 1)

    def test_budget_and_outbox_are_consistent(self) -> None:
        run = self._run(max_crawls=1)
        self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/first",
            effective_policy_snapshot=policy_snapshot(),
        )
        rejected = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/second",
            effective_policy_snapshot=policy_snapshot(),
        )

        self.assertFalse(rejected.created)
        self.assertTrue(rejected.limit_reached)
        current = self.store.get_run(run.id)
        assert current is not None
        self.assertTrue(current.crawl_limit_reached)
        self.assertEqual(current.request_count, 1)
        with self.sessions() as session:
            self.assertEqual(session.query(GraphOutboxRecord).count(), 1)

    def test_outbox_claim_is_fenced_and_retryable(self) -> None:
        run = self._run()
        self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/",
            effective_policy_snapshot=policy_snapshot(),
        )

        first = self.store.claim_outbox()
        self.assertEqual(len(first), 1)
        self.assertEqual(self.store.claim_outbox(), [])
        self.assertTrue(
            self.store.release_outbox(first[0], RuntimeError("offline"))
        )
        retried = self.store.claim_outbox()
        self.assertEqual([item.id for item in retried], [first[0].id])
        self.assertTrue(self.store.mark_outbox_published(retried[0]))
        self.assertEqual(self.store.claim_outbox(), [])

    def test_pause_suspends_outbox_without_losing_work(self) -> None:
        run = self._run()
        self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/",
            effective_policy_snapshot=policy_snapshot(),
        )

        paused = self.store.pause_run(run.id)
        self.assertEqual(paused.status, "paused")
        self.assertEqual(self.store.claim_outbox(), [])
        resumed = self.store.resume_run(run.id)
        self.assertEqual(resumed.status, "running")
        self.assertEqual(len(self.store.claim_outbox()), 1)

    def test_idle_run_settlement_accepts_a_reached_budget(self) -> None:
        run = self._run(max_crawls=1)
        admitted = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/first",
            effective_policy_snapshot=policy_snapshot(),
        )
        assert admitted.request is not None
        self.store.update_run(
            run.id,
            lambda value: value.model_copy(
                update={"crawl_limit_reached": True}
            ),
        )
        self.store.settle_request(
            request_id=admitted.request.id,
            status="completed",
        )

        current = self.store.get_run(run.id)
        assert current is not None
        self.assertEqual(current.status, "completed")

    def test_failure_fences_all_nonterminal_work_atomically(self) -> None:
        run = self._run()
        admitted = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/",
            effective_policy_snapshot=policy_snapshot(),
        )
        assert admitted.request is not None

        failed = self.store.fail_run(run.id, error="deadline")

        request = self.store.get_request(admitted.request.id)
        assert request is not None
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.pending_request_count, 0)
        self.assertEqual(request.status, "cancelled")
        self.assertEqual(request.generation, 2)


    def test_request_and_run_settlement_are_transactional(self) -> None:
        run = self._run()
        admitted = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/",
            effective_policy_snapshot=policy_snapshot(),
        )
        assert admitted.request is not None

        request = self.store.settle_request(
            request_id=admitted.request.id,
            status="failed",
            error="HTTP 500",
            failure_stage="request",
            failure_code="http_status",
            status_code=500,
        )

        self.assertEqual(request.status, "failed")
        current = self.store.get_run(run.id)
        assert current is not None
        self.assertEqual(current.pending_request_count, 0)
        self.assertEqual(current.acquisition_pending_count, 0)
        self.assertEqual(current.failed_request_count, 1)
        self.assertEqual(current.failure_groups[0].status_code, 500)
        self.store.settle_request(
            request_id=admitted.request.id,
            status="failed",
            error="duplicate",
        )
        current = self.store.get_run(run.id)
        assert current is not None
        self.assertEqual(current.failed_request_count, 1)

    def test_cancellation_fences_requests_and_unpublished_work(self) -> None:
        run = self._run()
        admitted = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/",
            effective_policy_snapshot=policy_snapshot(),
        )
        assert admitted.request is not None

        cancelled = self.store.cancel_run(run.id)

        self.assertEqual(cancelled.status, "cancelled")
        request = self.store.get_request(admitted.request.id)
        assert request is not None
        self.assertEqual(request.status, "cancelled")
        self.assertEqual(request.generation, 2)
        self.assertEqual(self.store.claim_outbox(), [])

    def test_acquisition_checkpoint_and_navigation_outbox_are_atomic(self) -> None:
        run = self._run()
        admitted = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/",
            effective_policy_snapshot=policy_snapshot(),
        )
        assert admitted.request is not None
        claim = uuid4()
        self.store.update_request(
            admitted.request.id,
            lambda request: request.model_copy(
                update={"status": "crawling", "claim_token": claim}
            ),
        )
        readiness = NavigationReadinessWork(
            event_id=uuid4(),
            crawl_id=uuid4(),
            graph_run_id=run.id,
            crawl_request_id=admitted.request.id,
            generation=1,
            occurred_at=datetime.now(UTC),
        )

        request, transitioned = self.store.complete_acquisition(
            request_id=admitted.request.id,
            generation=1,
            claim_token=claim,
            document_id="document",
            acquisition_attempts=({"status": 200},),
            readiness=readiness,
        )

        self.assertTrue(transitioned)
        self.assertEqual(request.status, "awaiting_navigation")
        self.assertEqual(request.document_id, "document")
        current = self.store.get_run(run.id)
        assert current is not None
        self.assertEqual(current.acquisition_pending_count, 0)
        with self.sessions() as session:
            outboxes = list(
                session.scalars(
                    select(GraphOutboxRecord).order_by(
                        GraphOutboxRecord.created_at
                    )
                )
            )
            self.assertEqual(len(outboxes), 2)
            self.assertEqual(
                outboxes[-1].subject,
                "atlas.graph.navigation.readiness",
            )

    def test_navigation_activation_and_edge_outbox_are_atomic(self) -> None:
        run = self._run()
        admitted = self.store.admit_request(
            run_id=run.id,
            node_id=run.snapshot.root_node_id,
            url="https://example.com/",
            effective_policy_snapshot=policy_snapshot(),
        )
        assert admitted.request is not None
        claim = uuid4()
        self.store.update_request(
            admitted.request.id,
            lambda request: request.model_copy(
                update={"status": "crawling", "claim_token": claim}
            ),
        )
        crawl_id = uuid4()
        readiness = NavigationReadinessWork(
            event_id=uuid4(),
            crawl_id=crawl_id,
            graph_run_id=run.id,
            crawl_request_id=admitted.request.id,
            generation=1,
            occurred_at=datetime.now(UTC),
        )
        self.store.complete_acquisition(
            request_id=admitted.request.id,
            generation=1,
            claim_token=claim,
            document_id="document",
            acquisition_attempts=(),
            readiness=readiness,
        )
        edge = run.snapshot.edges[0]
        identity = edge_evaluation_identity(
            run.id, admitted.request.id, crawl_id, edge.id
        )
        evaluation = EdgeEvaluation(
            identity=identity,
            graph_run_id=run.id,
            crawl_request_id=admitted.request.id,
            crawl_id=crawl_id,
            edge_id=edge.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        work = EdgeWork(
            graph_run_id=run.id,
            crawl_request_id=admitted.request.id,
            crawl_id=crawl_id,
            edge_id=edge.id,
            generation=1,
            navigation={
                "object_name": "runtime/navigation/document.arrow",
                "sha256": "a" * 64,
                "schema_version": 1,
                "recipe": "page-links-v1",
                "row_count": 1,
                "byte_size": 1,
            },
        )

        result = self.store.activate_navigation(
            event=readiness,
            edges=((evaluation, work),),
        )

        self.assertEqual(result, "activated")
        request = self.store.get_request(admitted.request.id)
        assert request is not None
        self.assertEqual(request.status, "evaluating_edges")
        with self.sessions() as session:
            self.assertIsNotNone(
                session.get(EdgeEvaluationRecord, identity)
            )
            edge_outbox = session.scalar(
                select(GraphOutboxRecord).where(
                    GraphOutboxRecord.subject == "atlas.graph.edge"
                )
            )
            self.assertIsNotNone(edge_outbox)
        edge_claim = uuid4()
        self.store.update_edge_evaluation(
            identity,
            lambda item: item.model_copy(
                update={"status": "running", "claim_token": edge_claim}
            ),
        )

        completed, request_settled = self.store.finish_edge(
            identity=identity,
            claim_token=edge_claim,
            status="completed",
            output_count=3,
        )

        self.assertEqual(completed.status, "completed")
        self.assertTrue(request_settled)
        request = self.store.get_request(admitted.request.id)
        assert request is not None
        self.assertEqual(request.status, "completed")


class AsyncGraphRuntimeStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(
            self.engine,
            tables=[
                GraphRunRecord.__table__,
                CrawlRequestRecord.__table__,
                GraphAdmissionRecord.__table__,
                EdgeEvaluationRecord.__table__,
                GraphOutboxRecord.__table__,
            ],
        )
        sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.store = AsyncGraphRuntimeStore(GraphRuntimeStore(sessions))

    async def asyncTearDown(self) -> None:
        self.engine.dispose()

    async def test_repeated_run_creation_resumes_the_postgres_root_cursor(
        self,
    ) -> None:
        graph = snapshot()
        run_id = uuid4()
        arguments = {
            "runs": self.store,
            "requests": self.store,
            "progress": self.store,
            "jetstream": None,
            "snapshot": graph,
            "urls": ["https://example.com/"],
            "policy_resolver": policy_snapshot,
            "run_id": run_id,
        }

        first = await create_graph_run(**arguments)
        repeated = await create_graph_run(**arguments)

        self.assertEqual(first.id, run_id)
        self.assertEqual(repeated.id, run_id)
        self.assertEqual(repeated.request_count, 1)
        self.assertEqual(repeated.root_admission_cursor, 1)
        self.assertEqual(len(await self.store.list_requests()), 1)


if __name__ == "__main__":
    unittest.main()
