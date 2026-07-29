from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

import httpx

from atlas.platform.catalogue.bulk import (
    BulkCatalogueAction,
    BulkCommitFile,
    BulkPartitionValue,
    DuckBasinBulkClient,
    DuckBasinBulkCommitError,
)
from atlas.platform.catalogue.duckbasin import (
    DuckBasinConfig,
    DuckBasinTarget,
    DuckBasinToken,
    DuckBasinUnavailableError,
)


LAKE_ID = UUID("e412ec7c-5016-420d-941c-cf8f1b563712")
OPERATION_ID = "8c0a1583-cd47-4d74-9cb2-6fb808297488"


class _Tokens:
    def __init__(self) -> None:
        self.generation = 1
        self.invalidated: list[int] = []

    def get(self) -> DuckBasinToken:
        return DuckBasinToken(f"token-{self.generation}", self.generation)

    def invalidate(self, token: DuckBasinToken) -> None:
        self.invalidated.append(token.generation)
        self.generation += 1


def _operation(state: str, *, upload: bool = False, **overrides):
    payload = {
        "id": OPERATION_ID,
        "ducklake": str(LAKE_ID),
        "idempotency_key": "atlas:rebuild:one",
        "manifest_hash": "a" * 64,
        "state": state,
        "snapshot_id": 91 if state == "committed" else None,
        "error_code": "",
        "error_message": "",
        "retryable": False,
        "files": [
            {
                "file_id": "content-0",
                "upload": (
                    {
                        "strategy": "single_put",
                        "method": "PUT",
                        "url": "https://upload.example/exact",
                        "headers": {
                            "content-md5": "signed-md5",
                            "x-amz-checksum-sha256": "signed-sha256",
                        },
                    }
                    if upload
                    else None
                ),
            }
        ],
    }
    payload.update(overrides)
    return payload


class DuckBasinBulkClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "content.parquet"
        self.path.write_bytes(b"parquet")
        self.file = BulkCommitFile(
            file_id="content-0",
            schema="material",
            table="content_stats",
            ingest_mode="register",
            mutation_mode="append",
            path=self.path,
            rows=12,
            sha256=hashlib.sha256(b"parquet").hexdigest(),
            md5=hashlib.md5(b"parquet").hexdigest(),
            partition_values=(BulkPartitionValue("bucket", 3),),
        )
        self.tokens = _Tokens()

    def client(self, handler, **kwargs) -> DuckBasinBulkClient:
        config = DuckBasinConfig(
            base_url="https://basin.example",
            lake="atlas",
            token_endpoint="https://basin.example/token",
            client_id="atlas",
            client_secret="secret",
            request_timeout_seconds=1,
            token_refresh_seconds=60,
        )
        target = DuckBasinTarget(
            lake_id=LAKE_ID,
            lake_slug="atlas",
            catalogue_alias="atlas",
            quack_uri="quack:lake.example",
            quack_scope="quack:lake.example",
            disable_ssl=False,
        )
        return DuckBasinBulkClient(
            config,
            target,
            self.tokens,  # type: ignore[arg-type]
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            poll_interval_seconds=0.001,
            **kwargs,
        )

    def test_prepares_uploads_completes_and_waits_for_snapshot(self) -> None:
        requests: list[tuple[str, str]] = []
        uploaded_headers: dict[str, str] = {}
        detail_states = iter(("prepared", "validating", "committed"))

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.host == "upload.example":
                uploaded_headers.update(request.headers)
                self.assertEqual(request.content, b"parquet")
                return httpx.Response(200)
            if request.method == "POST" and request.url.path.endswith(
                "/bulk-commits/"
            ):
                manifest = request.read()
                self.assertIn(b'"ingest_mode":"register"', manifest)
                self.assertIn(b'"mutation_mode":"append"', manifest)
                self.assertIn(b'"match_columns":[]', manifest)
                self.assertIn(b'"partition_values":[{"key":"bucket","value":3}]', manifest)
                return httpx.Response(202, json=_operation("pending"))
            if request.method == "POST" and request.url.path.endswith(
                "/complete/"
            ):
                return httpx.Response(202, json=_operation("submitted"))
            return httpx.Response(
                200,
                json=_operation(
                    next(detail_states),
                    upload=(
                        len(requests) == 2
                    ),
                ),
            )

        operation = self.client(handler).commit(
            [self.file],
            idempotency_key="atlas:rebuild:one",
        )

        self.assertEqual(operation.state, "committed")
        self.assertEqual(operation.snapshot_id, 91)
        self.assertEqual(uploaded_headers["content-md5"], "signed-md5")
        self.assertEqual(
            uploaded_headers["x-amz-checksum-sha256"],
            "signed-sha256",
        )
        self.assertIn(
            (
                "POST",
                f"/api/ducklakes/{LAKE_ID}/bulk-commits/"
                f"{OPERATION_ID}/complete/",
            ),
            requests,
        )

    def test_committed_prepare_response_recovers_lost_ack_without_upload(self) -> None:
        requests = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(200, json=_operation("committed"))

        operation = self.client(handler).commit(
            [self.file],
            idempotency_key="atlas:rebuild:one",
        )

        self.assertEqual(operation.snapshot_id, 91)
        self.assertEqual(requests, 1)

    def test_action_only_operation_completes_without_upload(self) -> None:
        states = iter(("pending", "prepared", "submitted", "committed"))
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.method == "POST" and request.url.path.endswith(
                "/bulk-commits/"
            ):
                self.assertIn(b'"kind":"clone_tables"', request.read())
            return httpx.Response(
                202,
                json=_operation(next(states), files=[]),
            )

        operation = self.client(handler).commit(
            idempotency_key="atlas:generation:one",
            actions=(
                BulkCatalogueAction(
                    action_id="clone-content",
                    kind="clone_tables",
                    entries=(
                        {
                            "schema": "material",
                            "source_table": "content_stats",
                            "target_table": "_atlas_rebuild_content",
                        },
                    ),
                ),
            ),
        )

        self.assertEqual(operation.state, "committed")
        self.assertFalse(
            any("upload.example" in request for request in requests)
        )

    def test_submitted_prepare_response_resumes_polling_without_reupload(self) -> None:
        states = iter(("submitted", "committing", "committed"))
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.host or "")
            return httpx.Response(200, json=_operation(next(states)))

        operation = self.client(handler).commit(
            [self.file],
            idempotency_key="atlas:rebuild:one",
        )

        self.assertEqual(operation.snapshot_id, 91)
        self.assertNotIn("upload.example", requests)

    def test_retries_one_rejected_token_generation(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(401)
            self.assertEqual(
                request.headers["authorization"],
                "Bearer token-2",
            )
            return httpx.Response(200, json=_operation("committed"))

        operation = self.client(handler).commit(
            [self.file],
            idempotency_key="atlas:rebuild:one",
        )

        self.assertEqual(operation.snapshot_id, 91)
        self.assertEqual(self.tokens.invalidated, [1])

    def test_terminal_failure_preserves_basin_error_contract(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                202,
                json=_operation(
                    "failed",
                    error_code="SCHEMA_CHANGED",
                    error_message="Target changed",
                ),
            )

        with self.assertRaises(DuckBasinBulkCommitError) as raised:
            self.client(handler).commit(
                [self.file],
                idempotency_key="atlas:rebuild:one",
            )

        self.assertEqual(raised.exception.error_code, "SCHEMA_CHANGED")
        self.assertFalse(raised.exception.retryable)

    def test_poll_timeout_is_retryable_without_changing_operation_identity(
        self,
    ) -> None:
        now = [0.0]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(202, json=_operation("pending"))

        def sleep(seconds: float) -> None:
            now[0] += seconds

        with self.assertRaises(DuckBasinUnavailableError):
            self.client(
                handler,
                operation_timeout_seconds=0.002,
                monotonic=lambda: now[0],
                sleep=sleep,
            ).commit(
                [self.file],
                idempotency_key="atlas:rebuild:one",
            )
