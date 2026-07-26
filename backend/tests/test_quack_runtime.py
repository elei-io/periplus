from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from datetime import UTC, datetime
from uuid import uuid4

import duckdb
from nats.js.errors import (
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
import pyarrow as pa

from api.routers.catalogue import query_runtime
from atlas_sql import (
    AtlasCompiler,
    DocumentScopePlan,
    InteractiveQueryPurpose,
)
from repository.catalogue.quack_runtime import (
    CatalogueQueryExecutionError,
    QuackQueryRuntime,
    QuackRuntimeConfig,
    _BoundedArrowStream,
    _QuackSlot,
    _finish_document_scope,
    _prepare_document_scope,
)
from repository.catalogue.query import CatalogueStatementKind
from runtime.catalogue_queries import (
    CatalogueQueryState,
    create_catalogue_query,
    get_catalogue_query,
)


def _compilation(sql: str):
    return AtlasCompiler.embedded().compile(
        sql,
        purpose=InteractiveQueryPurpose(),
    )


def _config(
    root: Path,
    *,
    catalogue_alias: str = "atlas",
) -> QuackRuntimeConfig:
    return QuackRuntimeConfig(
        lake_slug="atlas_test",
        catalogue_alias=catalogue_alias,
        catalogue_schema="main",
        catalogue_schema_version="test",
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

    async def run(self, operation):
        return operation(self.connection)

    def interrupt(self) -> None:
        self.interrupted = True


class FakeQuackConnection:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False
        self.failure_once: str | None = None

    def execute(self, sql: str):
        self.calls.append(sql)
        if self.failure_once is not None:
            message = self.failure_once
            self.failure_once = None
            raise duckdb.InvalidInputException(message)
        return self

    def close(self) -> None:
        self.closed = True

    def interrupt(self) -> None:
        return


class FakeQuackMinter:
    def __init__(self) -> None:
        self.current_token_generation = 1
        self.connections: list[FakeQuackConnection] = []
        self.invalidated_generations: list[int] = []

    def target(self):
        return SimpleNamespace(catalogue_alias="basin_catalogue")

    def mint(self):
        connection = FakeQuackConnection()
        self.connections.append(connection)
        return SimpleNamespace(
            connection=connection,
            session_id=f"{len(self.connections):016x}",
            lake_slug="atlas",
            catalogue_alias="basin_catalogue",
            quack_uri=f"quack:session-{len(self.connections)}",
            token_generation=self.current_token_generation,
            close=connection.close,
        )

    def connection_credentials_stale(self, minted) -> bool:
        return minted.token_generation != self.current_token_generation

    def invalidate_connection_credentials(self, minted) -> None:
        self.invalidated_generations.append(minted.token_generation)
        if minted.token_generation == self.current_token_generation:
            self.current_token_generation += 1


class LocalRemoteConnection:
    def __init__(self) -> None:
        self.connection = duckdb.connect()
        self.remote_sql: list[str] = []

    def execute(self, _wrapper_sql: str, parameters: list[str]):
        self.remote_sql.append(parameters[0])
        return self.connection.execute(parameters[0])

    def close(self) -> None:
        self.connection.close()


class QuackRuntimeTests(unittest.TestCase):
    def test_environment_configuration_remains_server_side(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "ATLAS_QUACK_MAX_CONCURRENCY": "2",
                    "ATLAS_QUACK_POOL_WAIT_SECONDS": "1",
                    "ATLAS_QUACK_QUERY_TIMEOUT_SECONDS": "10",
                    "ATLAS_QUACK_QUERY_MAX_ROWS": "100",
                    "ATLAS_QUACK_QUERY_MAX_BYTES": "10000",
                    "ATLAS_CATALOGUE_SCHEMA": "main",
                },
                clear=True,
            ):
                config = QuackRuntimeConfig.from_env()

        runtime = QuackQueryRuntime(object(), config=config)
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(quack_runtime=runtime))
        )
        response = query_runtime(request)
        public = response.model_dump()

        self.assertEqual(response.transport, "api")
        self.assertEqual(config.catalogue_alias, "")
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

    def test_errors_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _config(Path(temp_dir))
            runtime = QuackQueryRuntime(object(), config=config)
            message = runtime.safe_error(RuntimeError("x" * 3_000))

        self.assertEqual(len(message), 2_000)

    def test_document_scope_hydrates_only_budgeted_projected_rows(self) -> None:
        remote = LocalRemoteConnection()
        try:
            remote.connection.execute(
                "CREATE TABLE documents(document_id VARCHAR, element_count BIGINT)"
            )
            remote.connection.execute(
                "CREATE TABLE elements("
                "document_id VARCHAR, element_index BIGINT, tag VARCHAR, "
                "attributes MAP(VARCHAR, VARCHAR))"
            )
            remote.connection.execute(
                "INSERT INTO documents VALUES ('doc-1', 2), ('doc-2', 1)"
            )
            remote.connection.execute(
                "INSERT INTO elements VALUES "
                "('doc-1', 0, 'html', MAP()), "
                "('doc-1', 1, 'body', MAP()), "
                "('doc-2', 0, 'html', MAP())"
            )

            active = _prepare_document_scope(
                remote,  # type: ignore[arg-type]
                DocumentScopePlan(
                    scope_sql=(
                        "SELECT document_id FROM documents "
                        "WHERE document_id = 'doc-1'"
                    ),
                    element_columns=("document_id", "tag"),
                    maximum_documents=10,
                    maximum_elements=10,
                ),
            )

            self.assertTrue(active)
            self.assertEqual(
                remote.connection.execute(
                    "SELECT * FROM _atlas_interactive_scoped_elements "
                    "ORDER BY tag"
                ).fetchall(),
                [("doc-1", "body"), ("doc-1", "html")],
            )
            self.assertEqual(
                [
                    row[1]
                    for row in remote.connection.execute(
                        "PRAGMA table_info('_atlas_interactive_scoped_elements')"
                    ).fetchall()
                ],
                ["document_id", "tag"],
            )
            hydration_sql = next(
                sql
                for sql in remote.remote_sql
                if sql.startswith("CREATE TEMP TABLE")
            )
            budget_sql = next(
                sql
                for sql in remote.remote_sql
                if sql.startswith("SELECT document_id, element_count")
            )
            self.assertIn(
                "WHERE document_id IN ('doc-1')",
                budget_sql,
            )
            self.assertIn(
                "WHERE element.document_id IN ('doc-1')",
                hydration_sql,
            )
            self.assertNotIn("JOIN (VALUES", hydration_sql)
            _finish_document_scope(
                remote,  # type: ignore[arg-type]
                commit=True,
            )
            with self.assertRaises(duckdb.CatalogException):
                remote.connection.execute(
                    "SELECT * FROM _atlas_interactive_scoped_elements"
                )
        finally:
            remote.close()


class QuackSlotCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_pool_slot_is_reminted_before_the_next_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            minter = FakeQuackMinter()
            config = _config(
                Path(temp_dir),
                catalogue_alias="basin_catalogue",
            )
            slot = _QuackSlot(0, config, minter)  # type: ignore[arg-type]
            await slot.open()
            original = minter.connections[0]
            minter.current_token_generation = 2

            await slot.run(lambda connection: connection.execute("SELECT 1"))
            await slot.close()

        self.assertTrue(original.closed)
        self.assertEqual(len(minter.connections), 2)
        self.assertEqual(
            minter.connections[1].calls,
            ['USE "basin_catalogue"."main"', "SELECT 1"],
        )

    async def test_authorization_failure_refreshes_an_idle_pool_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            minter = FakeQuackMinter()
            config = _config(
                Path(temp_dir),
                catalogue_alias="basin_catalogue",
            )
            slot = _QuackSlot(0, config, minter)  # type: ignore[arg-type]
            await slot.open()
            minter.connections[0].failure_once = "Authentication failed"

            await slot.run(lambda connection: connection.execute("SELECT 1"))
            await slot.close()

        self.assertEqual(minter.invalidated_generations, [1])
        self.assertEqual(len(minter.connections), 2)
        self.assertEqual(
            minter.connections[1].calls,
            ['USE "basin_catalogue"."main"', "SELECT 1"],
        )


class QuackQueryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_rejects_an_invalid_compiler_result(self) -> None:
        runtime = QuackQueryRuntime(FakeBucket(), config=_config(Path(".")))

        with self.assertRaisesRegex(
            CatalogueQueryExecutionError,
            "requires a valid compiler result",
        ):
            await runtime.prepare(
                query_id=uuid4(),
                compilation=_compilation(""),
                statement_kind=CatalogueStatementKind.QUERY,
            )

    async def test_success_updates_durable_state_and_releases_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query_bucket = FakeBucket()
            runtime = QuackQueryRuntime(
                query_bucket,
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
                    optimization_status="unchanged",
                    applied_rewrites=(),
                    optimization_diagnostics=(),
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
                    compilation=_compilation("SELECT 1"),
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
                    optimization_status="unchanged",
                    applied_rewrites=(),
                    optimization_diagnostics=(),
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
                    compilation=_compilation("SELECT 1"),
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
                    optimization_status="unchanged",
                    applied_rewrites=(),
                    optimization_diagnostics=(),
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
                    compilation=_compilation("SELECT 1"),
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

    async def test_document_scope_is_hydrated_in_one_remote_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query_bucket = FakeBucket()
            runtime = QuackQueryRuntime(
                query_bucket,
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
                    optimization_status="optimized",
                    applied_rewrites=(),
                    optimization_diagnostics=(),
                    status="queued",
                    created_at=datetime.now(UTC),
                ),
            )
            compilation = _compilation("SELECT 1").model_copy(
                update={
                    "executable_sql": (
                        "SELECT document_id, tag "
                        "FROM _atlas_interactive_scoped_elements"
                    ),
                    "document_scope": DocumentScopePlan(
                        scope_sql=(
                            "SELECT document_id FROM documents LIMIT 10001"
                        ),
                        element_columns=("document_id", "tag"),
                        maximum_documents=10_000,
                        maximum_elements=50_000_000,
                    ),
                }
            )
            remote_sql: list[str] = []

            def prepare_scope(_connection, plan):
                remote_sql.extend(
                    [
                        "BEGIN TRANSACTION",
                        plan.scope_sql,
                        (
                            "CREATE TEMP TABLE "
                            "_atlas_interactive_scoped_elements"
                        ),
                    ]
                )
                return True

            def finish_scope(_connection, *, commit):
                remote_sql.extend(
                    [
                        "DROP TABLE _atlas_interactive_scoped_elements",
                        "COMMIT" if commit else "ROLLBACK",
                    ]
                )

            with (
                patch(
                    "repository.catalogue.quack_runtime._prepare_document_scope",
                    side_effect=prepare_scope,
                ),
                patch(
                    "repository.catalogue.quack_runtime._finish_document_scope",
                    side_effect=finish_scope,
                ),
                patch(
                    "repository.catalogue.quack_runtime._start_arrow_stream",
                    return_value=_stream(maximum_rows=3, maximum_bytes=10_000),
                ) as start,
            ):
                active = await runtime.prepare(
                    query_id=query_id,
                    compilation=compilation,
                    statement_kind=CatalogueStatementKind.QUERY,
                )
                _ = b"".join([chunk async for chunk in active.stream()])

        self.assertEqual(
            start.call_args.args[1],
            compilation.executable_sql,
        )
        self.assertEqual(
            remote_sql,
            [
                "BEGIN TRANSACTION",
                compilation.document_scope.scope_sql,
                "CREATE TEMP TABLE _atlas_interactive_scoped_elements",
                "DROP TABLE _atlas_interactive_scoped_elements",
                "COMMIT",
            ],
        )

    async def test_document_scope_rolls_back_when_hydration_exceeds_budget(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query_bucket = FakeBucket()
            runtime = QuackQueryRuntime(
                query_bucket,
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
                    optimization_status="optimized",
                    applied_rewrites=(),
                    optimization_diagnostics=(),
                    status="queued",
                    created_at=datetime.now(UTC),
                ),
            )
            compilation = _compilation("SELECT 1").model_copy(
                update={
                    "document_scope": DocumentScopePlan(
                        scope_sql="SELECT document_id FROM documents",
                        element_columns=("document_id",),
                        maximum_documents=10_000,
                        maximum_elements=50_000_000,
                    ),
                }
            )
            finished: list[bool] = []

            with (
                patch(
                    "repository.catalogue.quack_runtime._prepare_document_scope",
                    side_effect=CatalogueQueryExecutionError(
                        "document scope exceeds 50,000,000 elements"
                    ),
                ),
                patch(
                    "repository.catalogue.quack_runtime._finish_document_scope",
                    side_effect=lambda _connection, *, commit: finished.append(commit),
                ),
                self.assertRaisesRegex(
                    CatalogueQueryExecutionError,
                    "exceeds 50,000,000 elements",
                ),
            ):
                await runtime.prepare(
                    query_id=query_id,
                    compilation=compilation,
                    statement_kind=CatalogueStatementKind.QUERY,
                )

        self.assertEqual(finished, [False])
        state = await get_catalogue_query(query_bucket, query_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "failed")
        self.assertIs(runtime._available.get_nowait(), slot)


if __name__ == "__main__":
    unittest.main()
