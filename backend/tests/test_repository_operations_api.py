from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.repository_operations import router
from repository import (
    ArtifactIdentity,
    FileObjectStore,
    RawArtifactRepository,
    RawHtmlRepository,
)


class _RepositoryContext:
    def __init__(
        self,
        store: FileObjectStore,
        *,
        document: object | None = None,
        artifact: object | None = None,
    ) -> None:
        self.html_repository = RawHtmlRepository(store)
        self.artifact_repository = RawArtifactRepository(store)
        self.catalogue_service = Mock()
        self.catalogue_service.get_document.return_value = document
        self.catalogue_service.get_artifact.return_value = artifact

    def validate(self) -> None:
        pass

    def __enter__(self) -> _RepositoryContext:
        return self

    def __exit__(self, *_args: object) -> None:
        pass


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
            stored = html_repository.put(html, chunk_chars=7)
            repository = _RepositoryContext(
                store,
                document=SimpleNamespace(
                    html_object_key=stored.object_key,
                    html_sha256=stored.sha256,
                    html_content_type="text/html",
                ),
            )

            with patch(
                "api.routers.repository_operations.repository_ingestor_from_env",
                return_value=repository,
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
            repository = _RepositoryContext(
                FileObjectStore(Path(temp_dir) / "objects"),
            )
            with patch(
                "api.routers.repository_operations.repository_ingestor_from_env",
                return_value=repository,
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
            )
            repository = _RepositoryContext(
                store,
                artifact=SimpleNamespace(
                    object_key=stored.object_key,
                    sha256=stored.sha256,
                ),
            )

            with patch(
                "api.routers.repository_operations.repository_ingestor_from_env",
                return_value=repository,
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
            repository = _RepositoryContext(
                FileObjectStore(Path(temp_dir) / "objects"),
            )
            with patch(
                "api.routers.repository_operations.repository_ingestor_from_env",
                return_value=repository,
            ):
                response = self.client.get(
                    "/operations/repository/artifacts/sha256:missing/content"
                )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "artifact was not found"})
