from __future__ import annotations

import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.ingestion.documents_http import (
    get_document_store,
    router,
)
from atlas.ingestion.objects.document import (
    ExactDocumentRepository,
    identify_document,
)
from atlas.ingestion.objects.html import RawHtmlRepository
from atlas.ingestion.objects.store import FileObjectStore
from atlas.platform.catalogue.control import get_catalogue_control


class _FakeCatalogue:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.content_rows: dict[str, tuple[object, ...]] = {}

    def trusted_remote_rows(self, sql: str) -> list[tuple[object, ...]]:
        self.calls.append(sql)
        if "WITH filtered_documents AS" in sql:
            return [(1, 1, 123, 87)]
        if "SELECT DISTINCT detected_media_type" in sql:
            return [("application/pdf",), ("text/html",)]
        if "WHERE document_id =" in sql:
            for document_id, row in self.content_rows.items():
                if document_id in sql:
                    return [row]
            return []
        return [
            (
                "6b86fdc6-a458-5f2e-a7e9-513978c9b26f",
                "8f5d71cc-d5a4-4d0c-a69b-67d09287da19",
                None,
                "https://example.com/page",
                datetime(2026, 7, 28, 1, 2, tzinfo=UTC),
                "rendered_html",
                "text/html; charset=utf-8",
                "text/html",
                "utf-8",
                "0" * 64,
                123,
                "zstd",
                87,
            )
        ]


class _FakeControl:
    def __init__(self, catalogue: _FakeCatalogue) -> None:
        self.catalogue = catalogue

    async def run(self, operation):
        return operation(None, self.catalogue)


class DocumentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FileObjectStore(Path(self.temporary.name))
        self.catalogue = _FakeCatalogue()
        self.control = _FakeControl(self.catalogue)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_catalogue_control] = lambda: self.control
        app.dependency_overrides[get_document_store] = lambda: self.store
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temporary.cleanup()

    def test_lists_documents_with_bounded_filters_and_stable_sort(self) -> None:
        response = self.client.get(
            "/documents",
            params={
                "content_type": "text/html'; selected",
                "url": "EXAMPLE.com/O'Brien",
                "observed_from": "2026-07-01T00:00:00Z",
                "observed_to": "2026-08-01T00:00:00Z",
                "sort": "url",
                "direction": "asc",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(
            payload["summary"],
            {
                "document_count": 1,
                "unique_content_count": 1,
                "logical_bytes": 123,
                "stored_bytes": 87,
            },
        )
        self.assertEqual(payload["items"][0]["url"], "https://example.com/page")
        summary_sql = self.catalogue.calls[0]
        page_sql = self.catalogue.calls[1]
        self.assertIn("GROUP BY object_key", summary_sql)
        self.assertIn("SELECT sum(stored_bytes)", summary_sql)
        self.assertIn("text/html''; selected", page_sql)
        self.assertIn("EXAMPLE.com/O''Brien", page_sql)
        self.assertIn(
            "ORDER BY COALESCE(visits.effective_url, visits.requested_url) ASC",
            page_sql,
        )
        self.assertIn("documents.document_id ASC", page_sql)

    def test_rejects_inverted_date_range(self) -> None:
        response = self.client.get(
            "/documents",
            params={
                "observed_from": "2026-08-01T00:00:00Z",
                "observed_to": "2026-07-01T00:00:00Z",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.catalogue.calls, [])

    def test_lists_detected_media_types(self) -> None:
        response = self.client.get("/documents/media-types")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"items": ["application/pdf", "text/html"]},
        )

    def test_downloads_exact_owned_bytes_as_an_attachment(self) -> None:
        content = b"%PDF-owned-evidence"
        identity = identify_document(io.BytesIO(content))
        document_id = str(uuid4())
        stored = ExactDocumentRepository(self.store).put(
            io.BytesIO(content),
            identity=identity,
            source_url="https://example.com/report.pdf",
            visit_id=uuid4(),
            observed_at=datetime.now(UTC),
            content_type="application/pdf",
        )
        self.catalogue.content_rows[document_id] = (
            document_id,
            "application/pdf",
            None,
            identity.sha256,
            identity.size_bytes,
            stored.object_key,
            "identity",
        )

        response = self.client.get(f"/documents/{document_id}/content")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content)
        self.assertTrue(
            response.headers["content-disposition"].startswith("attachment;")
        )
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(
            response.headers["content-security-policy"],
            "sandbox; default-src 'none'",
        )

    def test_download_decompresses_owned_html(self) -> None:
        html = "<html><body>owned evidence</body></html>"
        document_id = str(uuid4())
        stored = RawHtmlRepository(self.store).put(
            html,
            source_url="https://example.com/",
            visit_id=uuid4(),
            observed_at=datetime.now(UTC),
            content_type="text/html",
        )
        self.catalogue.content_rows[document_id] = (
            document_id,
            "text/html",
            "utf-8",
            stored.sha256,
            stored.size_bytes,
            stored.object_key,
            "zstd",
        )

        response = self.client.get(f"/documents/{document_id}/content")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, html.encode())
        self.assertIn("attachment;", response.headers["content-disposition"])

    def test_missing_document_is_not_found(self) -> None:
        response = self.client.get(f"/documents/{uuid4()}/content")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "document was not found")


if __name__ == "__main__":
    unittest.main()
