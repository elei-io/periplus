"""Exercise the assembled loops with real repository transitions and bounded fake I/O."""
import asyncio
from test_domain_policies import FakeBucket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from frontier_fixtures import policy_snapshot
from test_frontier_capture import lease
from test_frontier_store import TABLES
from periplus.crawl.acquisition.models import AcquisitionResult
from periplus.crawl.control.collections.discovery import DiscoveryState
from periplus.crawl.control.collections.schemas import CollectionSpec
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.runtime.frontier_models import FrontierControlRecord
from periplus.crawl.runtime.frontier_runtime import run_frontier, run_dispatch, service_collection
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.platform.catalogue.records import VisitEvidence, VisitRecord


class FrontierRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'frontier.db'}")
        for table in TABLES:
            table.create(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(FrontierControlRecord(id=1, captures_per_minute=0))
            ensure_default_domain_policy(session)
        self.store = FrontierStore(self.sessions)
        self.policy = EffectivePolicySnapshot.model_validate(policy_snapshot())

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    async def test_storage_outage_preserves_request_reservation_then_dispatch_recovers(self):
        from periplus.crawl.control.collections.schemas import SelectionContext
        identity = uuid4()
        self.store.create_collection(identity, CollectionSpec(seed_urls=("https://example.com/",)))
        admission = self.store.admit(identity, "https://example.com/",
            SelectionContext(depth=0, rule_id="seeds"), self.policy)
        pipeline = SimpleNamespace(queue=SimpleNamespace(check_available=AsyncMock()),
            check_storage_available=AsyncMock(side_effect=[OSError("storage offline"), None]))
        stop = asyncio.Event()
        from periplus.crawl.runtime.frontier_health import DispatchHealth
        health = DispatchHealth()
        waited = []
        async def wait(stop, seconds):
            waited.append(seconds)
            self.assertEqual(health.snapshot().state, "blocked")
            self.assertEqual(health.snapshot().reason, "storage_unavailable")
            collection = self.store.get_collection(identity)
            self.assertEqual((collection.reserved, collection.consumed), (1, 0))
            self.assertEqual(self.store.get_acquisition(admission.acquisition_id).status, "queued")
        dispatch = self.store.dispatch_next
        def accept():
            result = dispatch()
            stop.set()
            return result
        with patch("periplus.crawl.runtime.frontier_runtime._wait", wait), \
             patch("periplus.crawl.runtime.frontier_runtime.connect_cdp", lease), \
             patch.object(self.store, "dispatch_next", side_effect=accept) as starts:
            await run_dispatch(self.store, pipeline=pipeline, playwright=object(), stop=stop, health=health)
        self.assertEqual(health.snapshot().state, "ready")
        self.assertEqual(waited, [30])
        starts.assert_called_once()
        self.assertEqual(self.store.get_collection(identity).consumed, 1)
        self.assertEqual(self.store.get_acquisition(admission.acquisition_id).status, "dispatched")

    async def test_loops_settle_seed_capture_selection_and_publish_lineage(self):
        identity = uuid4()
        self.store.create_collection(identity, CollectionSpec(seed_urls=("https://example.com/",), seed_description="Example sources"))
        stop = asyncio.Event()
        queue = asyncio.Queue()
        jobs = []
        captures = []
        message = AsyncMock()

        async def publish(subject, payload, **kwargs):
            message.data = payload
            await queue.put(message)

        async def fetch(**kwargs):
            self.assertEqual(kwargs["batch"], 1)
            return [await asyncio.wait_for(queue.get(), kwargs["timeout"])]

        async def enqueue(job):
            jobs.append(job)
            if job.kind == "lineage" and job.lineage.kind == "collection_outcome":
                stop.set()

        async def reconcile(job):
            from periplus.ingestion.queue import IngestionState
            return IngestionState(job=job, status="pending", updated_at=datetime.now(UTC))

        async def acquire(**kwargs):
            context = kwargs["context"]
            captures.append(context.acquisition_id)
            now = datetime.now(UTC)
            from periplus.platform.catalogue.records import AttemptRecord, AttemptUsage, attempt_id_for
            evidence = VisitEvidence(visit=VisitRecord(
                visit_id=context.acquisition_id, requested_url=kwargs["url"], admitted_at=context.admitted_at,
                started_at=now, finished_at=now, outcome="succeeded",
            ), attempts=(AttemptRecord(
                attempt_id=attempt_id_for(context.acquisition_id, 0),
                visit_id=context.acquisition_id, attempt_index=0,
                started_at=now, finished_at=now, outcome="succeeded",
                resource_usage=AttemptUsage(policy_version=context.dispatch_policy_version,
                                            domain_policy=context.policy.domain, exclusion_policy_version=context.exclusion_policy_version,
                                            reserved_ms=context.attempt_reserved_ms, measured_ms=100),
            ),))
            return AcquisitionResult(url=kwargs["url"], success=True, duration_seconds=0.1, evidence=evidence)

        discovery = SimpleNamespace(step=AsyncMock(return_value=DiscoveryState(
            revision=1, model="test-model", queries=("Example sources",), searches=((),),
            selected=("https://example.com/",), validation_cursor=1, urls=("https://example.com/",),
        )))
        ingestion = SimpleNamespace(enqueue=enqueue, reconcile=reconcile, check_available=AsyncMock())
        pipeline = SimpleNamespace(html_repository=SimpleNamespace(store=None), queue=ingestion, check_storage_available=AsyncMock())
        with patch("periplus.crawl.runtime.frontier_runtime.connect_cdp", lease), \
             patch("periplus.crawl.runtime.frontier_capture.connect_cdp", lease), \
             patch("periplus.crawl.runtime.frontier_capture.public_destination_url", AsyncMock(side_effect=lambda url: url)), \
             patch("periplus.crawl.runtime.frontier_capture.operation_leases", lease), \
             patch("periplus.crawl.runtime.frontier_capture.domain_permit", lease), \
             patch("periplus.crawl.runtime.frontier_capture.acquire_page", acquire):
            async with asyncio.timeout(25):
                await run_frontier(
                    self.store, subscription=SimpleNamespace(fetch=fetch), jetstream=SimpleNamespace(publish=publish),
                    ingestion=ingestion, pipeline=pipeline, playwright=object(),
                    operation_bucket=object(), domain_bucket=FakeBucket(), policy=lambda url: self.policy,
                    stop=stop, capture_lanes=2, discovery=discovery,
                )
        discovery.step.assert_awaited_once()
        terminal = next(job.lineage for job in jobs if job.kind == "lineage" and job.lineage.kind == "collection_outcome")
        self.assertEqual(terminal.seed_provenance.discovery.model, "test-model")
        self.assertEqual(terminal.seed_provenance.discovery.queries, ("Example sources",))
        self.assertEqual(len(captures), 1)
        self.assertEqual(sum(job.kind == "visit" for job in jobs), 1)
        self.assertEqual(sum(job.kind == "lineage" and job.lineage.kind == "fulfillment" for job in jobs), 1)
        self.assertEqual(self.store.get_collection(identity).status, "settled")
        message.ack.assert_awaited_once()

    def test_paused_deadline_settles_without_seed_execution(self):
        identity = uuid4()
        self.store.create_collection(identity, CollectionSpec(seed_sql="SELECT 'https://example.com/' AS url",
                                    deadline_at=datetime.now(UTC) - timedelta(seconds=1)))
        self.store.set_collection_paused(identity, True)
        work = self.store.claim_collection()
        def query(sql):
            self.fail("expired collection must not execute SQL")
        service_collection(self.store, work, lambda url: self.policy, None, seed_query=query)
        self.assertEqual(self.store.get_collection(identity).outcome, "deadline")
