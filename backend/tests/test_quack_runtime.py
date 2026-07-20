from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from datetime import UTC, datetime
from uuid import uuid4

from nats.js.errors import (
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
import pyarrow as pa

from api.routers.catalogue import query_runtime
from repository.catalogue.quack_runtime import (
    QuackQueryRuntime,
    QuackRuntimeConfig,
    _BoundedArrowStream,
)
from repository.catalogue.query import CatalogueStatementKind
from runtime.catalogue_queries import (
    CatalogueQueryState,
    create_catalogue_query,
    get_catalogue_query,
)


def _config(root: Path) -> QuackRuntimeConfig:
    return QuackRuntimeConfig(
        uri="quack:catalogue.internal:9494",
        token="secret-token",
        disable_ssl=True,
        catalogue_alias="atlas",
        catalogue_schema="main",
        metadata_schema="main",
        catalogue_schema_version="test",
        setup_sql=(),
        attach_sql=f"ATTACH 'ducklake:{root}' AS atlas",
        maximum_concurrency=2,
        pool_wait_seconds=1,
        query_timeout_seconds=10,
        maximum_rows=100,
        maximum_result_bytes=10_000,
    )


def _stream(*, maximum_rows: int, maximum_bytes: int) -> _BoundedArrowStream:
    schema = pa.schema([("number", pa.int64())])
    reader = pa.RecordBatchReader.from_batches(
        schema,
        [pa.record_batch([[1, 2, 3]], schema=schema)],
    )
    return _BoundedArrowStream(
        reader,
        maximum_rows=maximum_rows,
        maximum_bytes=maximum_bytes,
    )


def _payload(stream: _BoundedArrowStream) -> bytes:
    chunks: list[bytes] = []
    while (chunk := stream.next_chunk()) is not None:
        chunks.append(chunk)
    return b"".join(chunks)


class FakeBucket:
    def __init__(self) -> None:
        self.values: dict[str, tuple[int, bytes]] = {}
        self.revision = 0

    async def get(self, key: str):
        try:
            revision, value = self.values[key]
        except KeyError:
            raise KeyNotFoundError from None
        return SimpleNamespace(revision=revision, value=value)

    async def create(self, key: str, value: bytes) -> int:
        if key in self.values:
            raise KeyWrongLastSequenceError
        self.revision += 1
        self.values[key] = (self.revision, value)
        return self.revision

    async def update(self, key: str, value: bytes, *, last: int) -> int:
        if key not in self.values or self.values[key][0] != last:
            raise KeyWrongLastSequenceError
        self.revision += 1
        self.values[key] = (self.revision, value)
        return self.revision

    async def delete(self, key: str, *, last: int) -> None:
        if key not in self.values:
            raise KeyDeletedError
        if self.values[key][0] != last:
            raise KeyWrongLastSequenceError
        del self.values[key]


class FakeSlot:
    def __init__(self) -> None:
        self.connection = object()
        self.interrupted = False

    async def submit(self, operation):
        return operation()

    def interrupt(self) -> None:
        self.interrupted = True


class QuackRuntimeTests(unittest.TestCase):
    def test_environment_configuration_remains_server_side(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "ATLAS_QUACK_URI": "quack:catalogue.internal:9494",
                    "ATLAS_QUACK_TOKEN": "secret-token",
                    "ATLAS_QUACK_DISABLE_SSL": "true",
                    "ATLAS_QUACK_MAX_CONCURRENCY": "2",
                    "ATLAS_QUACK_POOL_WAIT_SECONDS": "1",
                    "ATLAS_QUACK_QUERY_TIMEOUT_SECONDS": "10",
                    "ATLAS_QUACK_QUERY_MAX_ROWS": "100",
                    "ATLAS_QUACK_QUERY_MAX_BYTES": "10000",
                    "ATLAS_CATALOGUE_CATALOG": "duckdb",
                    "ATLAS_CATALOGUE_ROOT": temp_dir,
                    "ATLAS_REPOSITORY_ROOT": temp_dir,
                    "ATLAS_REPOSITORY_STORAGE": "disk",
                },
                clear=True,
            ):
                config = QuackRuntimeConfig.from_env()

        runtime = QuackQueryRuntime(object(), object(), config=config)
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(quack_runtime=runtime))
        )
        response = query_runtime(request)
        public = response.model_dump()

        self.assertEqual(response.transport, "api")
        self.assertEqual(response.mutation_policy, "read_only")
        self.assertNotIn("uri", public)
        self.assertNotIn("token", public)
        self.assertNotIn("attach_sql", public)
        self.assertNotIn("setup_sql", public)

    def test_arrow_stream_is_valid_and_bounded_by_rows(self) -> None:
        complete = _stream(maximum_rows=3, maximum_bytes=10_000)
        table = pa.ipc.open_stream(_payload(complete)).read_all()
        self.assertEqual(table.to_pylist(), [{"number": 1}, {"number": 2}, {"number": 3}])
        self.assertIsNone(complete.limit_error)

        limited = _stream(maximum_rows=2, maximum_bytes=10_000)
        table = pa.ipc.open_stream(_payload(limited)).read_all()
        self.assertEqual(table.num_rows, 0)
        self.assertIn("2 rows", limited.limit_error or "")

    def test_errors_redact_the_quack_endpoint_and_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _config(Path(temp_dir))
            runtime = QuackQueryRuntime(object(), object(), config=config)
            message = runtime.safe_error(
                RuntimeError(f"{config.uri} rejected {config.token}")
            )

        self.assertEqual(message, "[redacted] rejected [redacted]")


class QuackQueryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_updates_durable_state_and_releases_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query_bucket = FakeBucket()
            resource_bucket = FakeBucket()
            runtime = QuackQueryRuntime(
                query_bucket,
                resource_bucket,
                config=_config(Path(temp_dir)),
            )
            slot = FakeSlot()
            runtime._available.put_nowait(slot)
            query_id = uuid4()
            await create_catalogue_query(
                query_bucket,
                CatalogueQueryState(
                    id=query_id,
                    statement_kind=CatalogueStatementKind.QUERY,
                    status="queued",
                    created_at=datetime.now(UTC),
                ),
            )
            with patch(
                "repository.catalogue.quack_runtime._start_arrow_stream",
                return_value=_stream(maximum_rows=3, maximum_bytes=10_000),
            ):
                active = await runtime.prepare(
                    query_id=query_id,
                    sql="SELECT 1",
                    statement_kind=CatalogueStatementKind.QUERY,
                )
                chunks = active.stream()
                payload_parts: list[bytes] = []
                while not b"".join(payload_parts).endswith(
                    b"\xff\xff\xff\xff\x00\x00\x00\x00"
                ):
                    payload_parts.append(await anext(chunks))
                state_before_http_eof = await get_catalogue_query(
                    query_bucket,
                    query_id,
                )
                payload = b"".join(payload_parts)
                await chunks.aclose()

        state = await get_catalogue_query(query_bucket, query_id)
        self.assertIsNotNone(state)
        self.assertIsNotNone(state_before_http_eof)
        self.assertEqual(state_before_http_eof.status, "succeeded")
        self.assertEqual(state.status, "succeeded")
        self.assertEqual(state.row_count, 3)
        self.assertEqual(
            pa.ipc.open_stream(payload).read_all().num_rows,
            3,
        )
        self.assertNotIn(query_id, runtime._active)
        self.assertIs(runtime._available.get_nowait(), slot)

    async def test_cancellation_marks_the_query_and_interrupts_its_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query_bucket = FakeBucket()
            runtime = QuackQueryRuntime(
                query_bucket,
                FakeBucket(),
                config=_config(Path(temp_dir)),
            )
            slot = FakeSlot()
            runtime._available.put_nowait(slot)
            query_id = uuid4()
            await create_catalogue_query(
                query_bucket,
                CatalogueQueryState(
                    id=query_id,
                    statement_kind=CatalogueStatementKind.QUERY,
                    status="queued",
                    created_at=datetime.now(UTC),
                ),
            )
            with patch(
                "repository.catalogue.quack_runtime._start_arrow_stream",
                return_value=_stream(maximum_rows=3, maximum_bytes=10_000),
            ):
                active = await runtime.prepare(
                    query_id=query_id,
                    sql="SELECT 1",
                    statement_kind=CatalogueStatementKind.QUERY,
                )
                runtime.interrupt_local(query_id, "cancelled by test")
                _ = b"".join([chunk async for chunk in active.stream()])

        state = await get_catalogue_query(query_bucket, query_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "cancelled")
        self.assertEqual(state.error, "cancelled by test")
        self.assertTrue(slot.interrupted)

    async def test_closing_the_result_stream_cancels_the_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query_bucket = FakeBucket()
            runtime = QuackQueryRuntime(
                query_bucket,
                FakeBucket(),
                config=_config(Path(temp_dir)),
            )
            slot = FakeSlot()
            runtime._available.put_nowait(slot)
            query_id = uuid4()
            await create_catalogue_query(
                query_bucket,
                CatalogueQueryState(
                    id=query_id,
                    statement_kind=CatalogueStatementKind.QUERY,
                    status="queued",
                    created_at=datetime.now(UTC),
                ),
            )
            with patch(
                "repository.catalogue.quack_runtime._start_arrow_stream",
                return_value=_stream(maximum_rows=3, maximum_bytes=10_000),
            ):
                active = await runtime.prepare(
                    query_id=query_id,
                    sql="SELECT 1",
                    statement_kind=CatalogueStatementKind.QUERY,
                )
                chunks = active.stream()
                await anext(chunks)
                await chunks.aclose()

        state = await get_catalogue_query(query_bucket, query_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "cancelled")
        self.assertEqual(state.error, "client disconnected")
        self.assertTrue(slot.interrupted)


if __name__ == "__main__":
    unittest.main()
