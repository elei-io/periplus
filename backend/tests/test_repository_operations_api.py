from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.repository_operations import router
from repository import (
    ArtifactIdentity,
    FileObjectStore,
    RawArtifactRepository,
    RawHtmlRepository,
)


class RepositoryOperationsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def test_document_content_returns_exact_stored_html_bytes_inline(self) -> None:
        html = "<!doctype html>\r\n<html><body>アトラス</body></html>\r\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileObjectStore(Path(temp_dir) / "objects")
            html_repository = RawHtmlRepository(store)
            stored = html_repository.put(
                html,
                source_url="https://example.com/",
                crawl_id=UUID("12345678-1234-5678-1234-567812345678"),
                captured_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
                content_type="text/html",
                chunk_chars=7,
            )
            with patch(
                "api.routers.repository_operations.object_store_from_env",
                return_value=store,
            ):
                response = self.client.get(
                    f"/operations/repository/documents/sha256:{stored.sha256}/content"
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, html.encode("utf-8"))
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(
            response.headers["content-disposition"],
            f'inline; filename="{stored.sha256}.html"',
        )

    def test_document_content_returns_not_found_for_unknown_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileObjectStore(Path(temp_dir) / "objects")
            with patch(
                "api.routers.repository_operations.object_store_from_env",
                return_value=store,
            ):
                response = self.client.get(
                    "/operations/repository/documents/sha256:missing/content"
                )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "document was not found"})

    def test_artifact_content_returns_exact_stored_bytes_inline(self) -> None:
        payload = b"%PDF-1.7\r\n\x00\xffexact-artifact"
        identity = ArtifactIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileObjectStore(Path(temp_dir) / "objects")
            artifact_repository = RawArtifactRepository(store)
            stored = artifact_repository.put(
                io.BytesIO(payload),
                identity=identity,
                source_url="https://example.com/report.pdf",
                crawl_id=UUID("12345678-1234-5678-1234-567812345678"),
                captured_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
                content_type="application/pdf",
            )
            with patch(
                "api.routers.repository_operations.object_store_from_env",
                return_value=store,
            ):
                response = self.client.get(
                    f"/operations/repository/artifacts/{stored.artifact_id}/content"
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, payload)
        self.assertEqual(response.headers["content-type"], "application/octet-stream")
        self.assertEqual(
            response.headers["content-disposition"],
            f'inline; filename="{stored.sha256}"',
        )

    def test_artifact_content_returns_not_found_for_unknown_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileObjectStore(Path(temp_dir) / "objects")
            with patch(
                "api.routers.repository_operations.object_store_from_env",
                return_value=store,
            ):
                response = self.client.get(
                    "/operations/repository/artifacts/sha256:missing/content"
                )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "artifact was not found"})
