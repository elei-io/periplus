from __future__ import annotations

import base64
import hashlib
import io
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch
from uuid import UUID, uuid4

import boto3
from botocore.exceptions import ClientError

from repository import (
    ArtifactIdentity,
    FileObjectStore,
    RawHtmlRepository,
    RawArtifactRepository,
    RepositoryIntegrityError,
    RepositoryIngestor,
    RepositoryKeyError,
    RepositoryObjectNotFound,
    S3ObjectStore,
    detect_artifact_media_type,
)
from repository.catalogue import CrawlRecord, NormalizedUrl
from repository.objects.config import ensure_s3_bucket_from_env, object_store_from_env
from repository.objects.store import ObjectWriteHeaders
from repository.exceptions import RepositoryConfigError


CAPTURED_AT = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
CRAWL_ID = UUID("12345678-1234-5678-1234-567812345678")
HTML_WRITE_CONTEXT = {
    "source_url": "https://example.com/",
    "crawl_id": CRAWL_ID,
    "captured_at": CAPTURED_AT,
    "content_type": "text/html",
}
ARTIFACT_WRITE_CONTEXT = {
    **HTML_WRITE_CONTEXT,
    "source_url": "https://example.com/report.pdf",
    "content_type": "application/pdf",
}


class ObjectStoreContract:
    store: FileObjectStore | S3ObjectStore

    def test_object_lifecycle_and_conditional_write(self) -> None:
        key = "raw/html/sha256/aa/bb/example.html.zst"
        self.assertFalse(self.store.exists(key))
        self.assertTrue(self.store.put_if_absent(key, io.BytesIO(b"first")))
        self.assertFalse(self.store.put_if_absent(key, io.BytesIO(b"second")))
        self.assertTrue(self.store.exists(key))
        with self.store.open(key) as content:
            self.assertEqual(content.read(), b"first")
        self.assertTrue(self.store.delete(key))
        self.assertFalse(self.store.delete(key))
        with self.assertRaises(RepositoryObjectNotFound):
            with self.store.open(key):
                pass

    def test_unsafe_keys_are_rejected(self) -> None:
        for key in ("", "/absolute", "../escape", "raw/../escape", "raw\\escape"):
            with self.subTest(key=key):
                with self.assertRaises(RepositoryKeyError):
                    self.store.exists(key)

    def test_prefix_deletion_is_scoped(self) -> None:
        first = "runtime/navigation/run-a/documents/a/package.arrow"
        second = "runtime/navigation/run-b/documents/b/package.arrow"
        self.store.put_if_absent(first, io.BytesIO(b"a"))
        self.store.put_if_absent(second, io.BytesIO(b"b"))

        self.assertEqual(self.store.delete_prefix("runtime/navigation/run-a/"), 1)
        self.assertFalse(self.store.exists(first))
        self.assertTrue(self.store.exists(second))

    def test_listing_and_batch_deletion_are_prefix_scoped(self) -> None:
        first = "runtime/navigation/run-a/documents/a/package.arrow"
        second = "runtime/navigation/run-b/documents/b/package.arrow"
        self.store.put_if_absent(first, io.BytesIO(b"a"))
        self.store.put_if_absent(second, io.BytesIO(b"bb"))

        listed = list(self.store.list_objects("runtime/navigation/run-a/"))
        self.assertEqual([item.key for item in listed], [first])
        self.assertEqual(listed[0].size, 1)
        self.assertIsNotNone(listed[0].last_modified.tzinfo)
        self.assertEqual(self.store.delete_many((first,)), 1)
        self.assertFalse(self.store.exists(first))
        self.assertTrue(self.store.exists(second))


class FileObjectStoreTests(ObjectStoreContract, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = FileObjectStore(Path(self.temporary_directory.name))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()


class RawHtmlRepositoryTests(unittest.TestCase):
    def test_deduplication_rejects_a_corrupt_existing_object(self) -> None:
        html = "<html><body>Atlas</body></html>"
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RawHtmlRepository(FileObjectStore(Path(temp_dir)))
            stored = repository.put(html, **HTML_WRITE_CONTEXT)
            repository.store.delete(stored.object_key)
            repository.store.put_if_absent(stored.object_key, io.BytesIO(b"not-zstd"))

            with self.assertRaises(RepositoryIntegrityError):
                repository.put(html, **HTML_WRITE_CONTEXT)

    def test_concurrent_writes_do_not_share_native_compressor_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RawHtmlRepository(FileObjectStore(Path(temp_dir)))
            values = [f"<html><body>{index}</body></html>" for index in range(16)]
            with ThreadPoolExecutor(max_workers=8) as executor:
                stored = list(
                    executor.map(
                        lambda value: repository.put(
                            value,
                            **HTML_WRITE_CONTEXT,
                        ),
                        values,
                    )
                )

            self.assertEqual(len({value.sha256 for value in stored}), len(values))
            self.assertEqual(
                [repository.read(value.object_key) for value in stored],
                values,
            )

    def test_html_is_content_addressed_compressed_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RawHtmlRepository(FileObjectStore(Path(temp_dir)))
            first = repository.put(
                "<html><body>Atlas</body></html>",
                **HTML_WRITE_CONTEXT,
            )
            second = repository.put(
                "<html><body>Atlas</body></html>",
                **HTML_WRITE_CONTEXT,
            )

            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.object_key, second.object_key)
            self.assertTrue(first.object_key.endswith(f"{first.sha256}.html.zst"))
            self.assertEqual(repository.read(first.object_key), "<html><body>Atlas</body></html>")

    def test_read_preserves_newlines_used_by_content_identity(self) -> None:
        values = (
            "<html>\r\n<body>Atlas</body>\r\n</html>",
            "<html>\r<body>Atlas</body>\r</html>",
            "<html>\n<body>Atlas</body>\n</html>",
            "<html>\r\n<body>アトラス</body>\r\n</html>",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RawHtmlRepository(FileObjectStore(Path(temp_dir)))

            for html in values:
                stored = repository.put(
                    html,
                    chunk_chars=7,
                    **HTML_WRITE_CONTEXT,
                )
                restored = repository.read(stored.object_key)

                self.assertEqual(restored, html)
                self.assertEqual(repository.read_bytes(stored.object_key), html.encode("utf-8"))
                self.assertEqual(repository.identify(restored).sha256, stored.sha256)


class RawArtifactRepositoryTests(unittest.TestCase):
    def test_artifact_detection_retains_low_confidence_raw_prediction(self) -> None:
        detector = MagicMock()
        detector.identify_bytes.return_value = SimpleNamespace(
            dl=SimpleNamespace(mime_type="application/pdf"),
            output=SimpleNamespace(mime_type="application/octet-stream"),
            score=0.2,
        )
        with patch(
            "repository.objects.artifact._artifact_media_type_detector",
            return_value=detector,
        ):
            detection = detect_artifact_media_type(b"ambiguous bytes")

        self.assertEqual(detection.media_type, "application/pdf")
        self.assertEqual(detection.confidence, 0.2)

    def test_artifact_media_type_is_detected_from_bytes(self) -> None:
        pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n"
            b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
        )
        detection = detect_artifact_media_type(pdf)
        self.assertEqual(detection.media_type, "application/pdf")
        self.assertEqual(detection.detector_name, "magika")
        self.assertRegex(detection.detector_version, r"^1\.\d+\.\d+$")
        self.assertGreater(detection.confidence, 0.9)

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
            "QVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        self.assertEqual(
            detect_artifact_media_type(png).media_type,
            "image/png",
        )
        self.assertEqual(
            detect_artifact_media_type(
                b'<!doctype html><link href="chrome-extension://pdf-viewer">'
            ).media_type,
            "text/html",
        )

    def test_artifact_is_exact_content_addressed_and_deduplicated(self) -> None:
        payload = b"%PDF-1.7\r\n\x00exact-binary"
        identity = ArtifactIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RawArtifactRepository(FileObjectStore(Path(temp_dir)))
            first = repository.put(
                io.BytesIO(payload),
                identity=identity,
                **ARTIFACT_WRITE_CONTEXT,
            )
            second = repository.put(
                io.BytesIO(payload),
                identity=identity,
                **ARTIFACT_WRITE_CONTEXT,
            )

            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.artifact_id, f"sha256:{identity.sha256}")
            self.assertEqual(repository.verify(first.object_key), identity)
            self.assertEqual(repository.read_bytes(first.object_key), payload)
            with repository.store.open(first.object_key) as content:
                self.assertEqual(content.read(), payload)

    def test_existing_corrupt_artifact_is_rejected(self) -> None:
        payload = b"artifact"
        identity = ArtifactIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = RawArtifactRepository(FileObjectStore(Path(temp_dir)))
            repository.store.put_if_absent(identity.object_key, io.BytesIO(b"wrong"))

            with self.assertRaises(RepositoryIntegrityError):
                repository.put(
                    io.BytesIO(payload),
                    identity=identity,
                    **ARTIFACT_WRITE_CONTEXT,
                )


class ArtifactIngestionTests(unittest.TestCase):
    def test_artifact_row_includes_declared_and_detected_media_types(self) -> None:
        payload = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n"
            b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
        )
        identity = ArtifactIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        requested_url = NormalizedUrl.from_normalized_url(
            "https://example.com/report.pdf"
        )
        captured_at = datetime.now(UTC)
        crawl = CrawlRecord(
            crawl_id=uuid4(),
            artifact_id=identity.artifact_id,
            graph_id=uuid4(),
            graph_run_id=uuid4(),
            graph_node_id=uuid4(),
            requested_url=requested_url.normalized_url,
            url=requested_url.normalized_url,
            scheme=requested_url.scheme,
            host=requested_url.host,
            port=requested_url.port,
            registrable_domain=requested_url.registrable_domain,
            path=requested_url.path,
            query=requested_url.query,
            started_at=captured_at,
            completed_at=captured_at,
            content_captured_at=captured_at,
            status_code=200,
            response_media_type="application/pdf",
            policy_schema_version=1,
            effective_policy_hash=hashlib.sha256(b"{}").hexdigest(),
            effective_policy={},
            outcome="success",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileObjectStore(Path(temp_dir) / "objects")
            artifact_repository = RawArtifactRepository(store)
            artifact_repository.put(
                io.BytesIO(payload),
                identity=identity,
                **ARTIFACT_WRITE_CONTEXT,
            )
            ingestor = RepositoryIngestor(
                html_repository=RawHtmlRepository(store),
                artifact_repository=artifact_repository,
                catalogue=MagicMock(),
                staging_root=Path(temp_dir) / "staging",
            )

            prepared = ingestor.prepare_from_raw(
                crawl=crawl,
                crawl_attempts=(),
            )

        self.assertIsNotNone(prepared.artifact)
        assert prepared.artifact is not None
        self.assertEqual(
            prepared.artifact.response_media_type,
            "application/pdf",
        )
        self.assertEqual(
            prepared.artifact.detected_media_type,
            "application/pdf",
        )
        self.assertEqual(prepared.artifact.detector_name, "magika")
        self.assertGreater(prepared.artifact.detection_confidence, 0.9)


class S3ObjectHeadersTests(unittest.TestCase):
    def test_put_sets_representation_and_crawl_metadata_headers(self) -> None:
        client = MagicMock()
        store = S3ObjectStore(client, bucket="atlas", prefix="repository")

        created = store.put_if_absent(
            "raw/artifacts/example",
            io.BytesIO(b"artifact"),
            headers=ObjectWriteHeaders(
                content_type="application/pdf",
                metadata={
                    "url": "https://example.com/report.pdf",
                    "crawl-id": str(CRAWL_ID),
                    "captured-at": CAPTURED_AT.isoformat(),
                },
            ),
        )

        self.assertTrue(created)
        client.put_object.assert_called_once_with(
            Bucket="atlas",
            Key="repository/raw/artifacts/example",
            Body=ANY,
            IfNoneMatch="*",
            ContentType="application/pdf",
            Metadata={
                "url": "https://example.com/report.pdf",
                "crawl-id": str(CRAWL_ID),
                "captured-at": CAPTURED_AT.isoformat(),
            },
        )

    def test_html_sets_content_encoding_and_charset(self) -> None:
        client = MagicMock()
        client.head_object.side_effect = ClientError(
            {
                "Error": {"Code": "NotFound"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadObject",
        )
        repository = RawHtmlRepository(
            S3ObjectStore(client, bucket="atlas")
        )

        repository.put("<html></html>", **HTML_WRITE_CONTEXT)

        options = client.put_object.call_args.kwargs
        self.assertEqual(options["ContentType"], "text/html; charset=utf-8")
        self.assertEqual(options["ContentEncoding"], "zstd")
        self.assertEqual(options["Metadata"]["url"], "https://example.com/")
        self.assertEqual(options["Metadata"]["crawl-id"], str(CRAWL_ID))
        self.assertEqual(
            options["Metadata"]["captured-at"],
            CAPTURED_AT.isoformat(),
        )


class RepositoryConfigTests(unittest.TestCase):
    @patch("repository.objects.config.boto3.client")
    def test_s3_initializer_creates_a_missing_bucket(self, client_factory) -> None:
        client = client_factory.return_value
        client.head_bucket.side_effect = ClientError(
            {
                "Error": {"Code": "NoSuchBucket"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadBucket",
        )
        with patch.dict(
            os.environ,
            {
                "ATLAS_REPOSITORY_STORAGE": "s3",
                "ATLAS_REPOSITORY_S3_BUCKET": "atlas",
                "ATLAS_REPOSITORY_S3_REGION": "us-east-1",
            },
            clear=True,
        ):
            bucket = ensure_s3_bucket_from_env()

        self.assertEqual(bucket, "atlas")
        client.create_bucket.assert_called_once_with(Bucket="atlas")
        client.get_bucket_lifecycle_configuration.assert_not_called()
        client.put_bucket_lifecycle_configuration.assert_not_called()

    @patch("repository.objects.config.boto3.client")
    def test_s3_initializer_preserves_an_existing_bucket(self, client_factory) -> None:
        with patch.dict(
            os.environ,
            {
                "ATLAS_REPOSITORY_STORAGE": "s3",
                "ATLAS_REPOSITORY_S3_BUCKET": "atlas",
            },
            clear=True,
        ):
            bucket = ensure_s3_bucket_from_env()

        self.assertEqual(bucket, "atlas")
        client_factory.return_value.create_bucket.assert_not_called()

    @patch("repository.objects.config.boto3.client")
    def test_s3_url_style_is_applied_to_boto_client(self, client) -> None:
        with patch.dict(
            os.environ,
            {
                "ATLAS_REPOSITORY_STORAGE": "s3",
                "ATLAS_REPOSITORY_S3_BUCKET": "atlas",
                "ATLAS_REPOSITORY_S3_URL_STYLE": "path",
            },
            clear=True,
        ):
            object_store_from_env()

        options = client.call_args.kwargs
        self.assertEqual(options["config"].s3["addressing_style"], "path")

    @patch("repository.objects.config.boto3.client")
    def test_s3_pool_matches_the_callers_process_concurrency(self, client) -> None:
        with patch.dict(
            os.environ,
            {
                "ATLAS_REPOSITORY_STORAGE": "s3",
                "ATLAS_REPOSITORY_S3_BUCKET": "atlas",
            },
            clear=True,
        ):
            object_store_from_env(maximum_concurrency=12)

        options = client.call_args.kwargs
        self.assertEqual(options["config"].max_pool_connections, 12)

    def test_invalid_s3_url_style_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ATLAS_REPOSITORY_STORAGE": "s3",
                "ATLAS_REPOSITORY_S3_BUCKET": "atlas",
                "ATLAS_REPOSITORY_S3_URL_STYLE": "invalid",
            },
            clear=True,
        ):
            with self.assertRaises(RepositoryConfigError):
                object_store_from_env()


@unittest.skipUnless(os.getenv("ATLAS_TEST_MINIO") == "1", "MinIO integration is opt-in")
class S3ObjectStoreTests(ObjectStoreContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.client = boto3.client(
            "s3",
            endpoint_url=os.getenv("ATLAS_TEST_MINIO_ENDPOINT", "http://127.0.0.1:9000"),
            region_name="us-east-1",
            aws_access_key_id=os.getenv("MINIO_ROOT_USER", "atlas"),
            aws_secret_access_key=os.getenv("MINIO_ROOT_PASSWORD", "atlas-secret"),
        )
        cls.bucket = "atlas-test"
        try:
            cls.client.create_bucket(Bucket=cls.bucket)
        except cls.client.exceptions.BucketAlreadyOwnedByYou:
            pass

    def setUp(self) -> None:
        self.store = S3ObjectStore(self.client, bucket=self.bucket, prefix="repository-test")


if __name__ == "__main__":
    unittest.main()
