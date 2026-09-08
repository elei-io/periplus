import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from periplus.ingestion.objects.store import ObjectMetadata
from periplus.operations.api.storage import router
from periplus.operations.storage import readers
from periplus.operations.storage.models import Footprint, StorageReport
from periplus.operations.storage.service import StorageService
from periplus.platform.api_access import ApiAccessMiddleware
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory


class StorageReadersTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.config = CatalogueConfig("lake", str(root / "metadata.duckdb"), str(root / "data"), "ducklake")
        self.factory = DuckLakeConnectionFactory(self.config)
        with self.factory.connect() as connection:
            connection.execute("USE lake; CREATE SCHEMA ingest; CREATE SCHEMA material")
            connection.execute("CREATE TABLE ingest.visits (visit_id INTEGER); INSERT INTO ingest.visits VALUES (1), (2), (3)")
            connection.execute("CREATE TABLE ingest.documents (visit_id INTEGER, object_key VARCHAR, stored_bytes BIGINT); INSERT INTO ingest.documents VALUES (1, 'html/shared', 100), (2, 'html/shared', 100)")
            connection.execute("CREATE TABLE material._periplus_retention_objects (retired_snapshot BIGINT, snapshots_cleared_at TIMESTAMPTZ, stored_bytes BIGINT, retired_at TIMESTAMPTZ)")
            connection.execute("INSERT INTO material._periplus_retention_objects VALUES (-1, NULL, 10, now()), (1, NULL, 20, now()), (1, now(), 30, now())")
            connection.execute("CREATE TABLE material._periplus_retention_identities (kind VARCHAR, retired_at TIMESTAMPTZ); INSERT INTO material._periplus_retention_identities VALUES ('observation', now()), ('collection', now()), ('content', NULL)")
            connection.execute("CREATE TABLE material._periplus_rebuild_html_test (id INTEGER); CREATE TABLE material._periplus_retired_html_test (id INTEGER)")

    def test_deduplicates_content_and_preserves_retention_stage_semantics(self):
        evidence, tables, complete, retention = readers.lake(self.config)
        self.assertEqual(evidence.observations, 3)
        self.assertEqual(evidence.observations_with_documents, 2)
        self.assertEqual(evidence.documents, 2)
        self.assertEqual(evidence.unique_objects, 1)
        self.assertEqual(evidence.unique_bytes, 100)
        self.assertEqual(evidence.referenced_bytes, 200)
        self.assertEqual(evidence.median_bytes, 100)
        self.assertTrue(complete)
        self.assertEqual({table.generation for table in tables}, {"current", "rebuilding", "retired"})
        self.assertEqual([stage.expected_bytes for stage in retention.stages], [10, 20, 30])
        self.assertEqual(retention.retired_observations, 1)
        self.assertEqual(retention.retired_requests, 1)
        _, _, _, after = readers.lake(self.config)
        self.assertEqual(retention.snapshots, after.snapshots, "dashboard reads must not create snapshots")
        self.assertGreater(readers.lake_metadata(self.config).bytes, 0)

    def test_truncated_table_inventory_is_not_marked_complete(self):
        with patch.object(readers, "TABLE_LIMIT", 1):
            _, tables, complete, _ = readers.lake(self.config)
        self.assertEqual(len(tables), 1)
        self.assertFalse(complete)

    def test_object_inventory_preserves_empty_and_partial(self):
        class Objects:
            def list_objects(self, prefix):
                return iter([ObjectMetadata(f"{prefix}/a", 10, datetime.now(UTC)), ObjectMetadata(f"{prefix}/b", 20, datetime.now(UTC))])
        source = readers.inventory(Objects(), prefixes=("html",), id="raw", name="Raw")
        self.assertEqual(source.bytes, 30)
        self.assertTrue(source.complete)
        with patch.object(readers, "OBJECT_LIMIT", 1):
            source = readers.inventory(Objects(), prefixes=("html",), id="raw", name="Raw")
        self.assertEqual(source.bytes, 10)
        self.assertFalse(source.complete)
        source = readers.inventory(Objects(), prefixes=(), id="raw", name="Raw")
        self.assertEqual(source.bytes, 0)
        self.assertTrue(source.complete)


class StorageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_background_read_cannot_extend_expired_cache(self):
        service = StorageService(None, None, None, None)
        report = StorageReport(as_of=datetime.now(UTC), collected_at=datetime.now(UTC), sources=[])
        service._cached = report
        service._cached_at = -100
        service._task = asyncio.create_task(asyncio.sleep(0, result=report))
        await service._task
        fresh = report.model_copy()
        with patch.object(service, "_collect", new=AsyncMock(return_value=fresh)) as collect:
            self.assertIs(await service.read(), fresh)
            collect.assert_awaited_once()

    async def test_single_flight_cache_and_partial_sources(self):
        calls = []
        class JetStream:
            async def stream_info(self, name):
                calls.append(name)
                return SimpleNamespace(state=SimpleNamespace(bytes=0, messages=0))
        service = StorageService(None, None, None, JetStream())
        with patch.object(readers, "lake", side_effect=RuntimeError("password=secret")), patch.object(readers, "control_database", side_effect=RuntimeError()), patch.object(readers, "lake_metadata", return_value=Footprint(id="metadata", name="Metadata", bytes=0, complete=True, basis="test")), patch.object(service, "_objects", return_value=[]):
            reports = await asyncio.gather(*(service.read() for _ in range(8)))
            again = await service.read()
        self.assertTrue(all(item is again for item in reports))
        self.assertEqual(len(calls), 3)
        self.assertIsNone(again.evidence)
        self.assertEqual(next(s for s in again.sources if s.id == "metadata").bytes, 0)
        self.assertIsNone(next(s for s in again.sources if s.id == "lake").bytes)
        self.assertNotIn("secret", again.model_dump_json())
        await service.close()


class StorageAccessTests(unittest.TestCase):
    def test_public_credential_cannot_read_storage(self):
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(ApiAccessMiddleware)
        with patch.dict(os.environ, {"PERIPLUS_ADMIN_API_TOKEN": "admin-test", "PERIPLUS_PUBLIC_API_TOKEN": "public-test"}), TestClient(app) as client:
            self.assertEqual(client.get("/operations/storage").status_code, 401)
            self.assertEqual(client.get("/operations/storage", headers={"Authorization": "Bearer public-test"}).status_code, 403)
