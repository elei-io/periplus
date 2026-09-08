from datetime import UTC, datetime
from types import SimpleNamespace
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from periplus.platform.api_access import ApiAccessMiddleware
from nats.js.errors import NotFoundError

from periplus.operations.api import ingestion_status as module
from periplus.platform.messaging.catalogue_workers import CatalogueWorkerState, CatalogueLaneState
from periplus.platform.messaging.catalogue_queue import INGEST_DEAD_LETTER_SUBJECT


class IngestionDashboardTests(unittest.IsolatedAsyncioTestCase):
    def request(self, jetstream):
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            jetstream=jetstream, catalogue_workers=object(),
        )))

    async def test_isolates_ingestion_and_reads_subject_count(self):
        now = datetime.now(UTC)
        states = [CatalogueWorkerState(
            worker_id=capability, capability=capability, started_at=now,
            last_seen_at=now, process_ready=True,
            lanes=(CatalogueLaneState(lane_index=0, status="active", active=True),),
        ) for capability in ("ingestion", "materialization")]
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(return_value=SimpleNamespace(
                num_pending=2, num_ack_pending=1, num_redelivered=1,
            )),
            stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(
                messages=99, subjects={}, first_seq=1, last_seq=99,
            ))),
        )
        with patch.object(module, "list_catalogue_worker_states", AsyncMock(return_value=states)):
            report = await module.ingestion_status(self.request(jetstream))
        self.assertEqual(report.status, "processing")
        self.assertEqual(report.queue.total, 3)
        self.assertEqual(report.dead_letters, 0)
        self.assertEqual(report.capacity.worker_count, 1)
        self.assertEqual([worker.worker_id for worker in report.workers], ["ingestion"])
        jetstream.consumer_info.assert_awaited_once()
        self.assertTrue(report.recent_dead_letters.complete)

    async def test_missing_sources_are_not_zero_or_leaked(self):
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(side_effect=NotFoundError()),
            stream_info=AsyncMock(side_effect=RuntimeError("secret-password")),
        )
        with patch.object(module, "list_catalogue_worker_states", AsyncMock(side_effect=RuntimeError("secret-password"))):
            report = await module.ingestion_status(self.request(jetstream))
        self.assertEqual(report.status, "unavailable")
        self.assertIsNone(report.queue.total)
        self.assertIsNone(report.capacity)
        self.assertIsNone(report.workers)
        self.assertIsNone(report.dead_letters)
        self.assertNotIn("secret-password", report.model_dump_json())

    async def test_sparse_dead_letter_scan_is_bounded(self):
        jetstream = SimpleNamespace(
            stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(
                messages=10000, subjects={INGEST_DEAD_LETTER_SUBJECT: 1},
                first_seq=1, last_seq=10000,
            ))),
            get_msg=AsyncMock(side_effect=NotFoundError()),
        )
        count, recent = await module._dead_letters(jetstream)
        self.assertEqual(count, 1)
        self.assertFalse(recent.complete)
        self.assertEqual(recent.scanned_sequences, 200)
        self.assertEqual(jetstream.get_msg.await_count, 200)

    async def test_recent_dead_letters_are_newest_first(self):
        now = datetime.now(UTC)
        jetstream = SimpleNamespace(
            stream_info=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(
                messages=80, subjects={INGEST_DEAD_LETTER_SUBJECT: 2},
                first_seq=1, last_seq=80,
            ))),
            get_msg=AsyncMock(return_value=SimpleNamespace(
                subject=INGEST_DEAD_LETTER_SUBJECT, data=b"entry",
            )),
        )
        entry = SimpleNamespace(job=SimpleNamespace(
            request_id="visit-id", kind="visit", enqueued_at=now,
        ), error="password=secret-native-error", failed_at=now, processing_failure_count=3)
        with patch.object(module, "decode_dead_letter", return_value=entry):
            count, recent = await module._dead_letters(jetstream)
        self.assertEqual(count, 2)
        self.assertTrue(recent.complete)
        self.assertEqual([item.sequence for item in recent.items], [80, 79])
        self.assertEqual(recent.items[0].processing_failure_count, 3)
        self.assertNotIn("secret-native-error", recent.model_dump_json())


class IngestionAccessTests(unittest.TestCase):
    def test_ingestion_dashboard_requires_admin(self):
        app = FastAPI()
        app.include_router(module.router)
        app.add_middleware(ApiAccessMiddleware)
        with patch.dict(os.environ, {"PERIPLUS_ADMIN_API_TOKEN": "admin-test", "PERIPLUS_PUBLIC_API_TOKEN": "public-test"}), TestClient(app) as client:
            self.assertEqual(client.get("/operations/ingestion").status_code, 401)
            self.assertEqual(client.get("/operations/ingestion", headers={"Authorization": "Bearer public-test"}).status_code, 403)
