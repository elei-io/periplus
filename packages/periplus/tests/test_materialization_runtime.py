from __future__ import annotations
from operational_state_fixture import operational_state

from contextlib import contextmanager
from datetime import UTC, datetime
import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import duckdb
import pyarrow as pa

from periplus.materialization.batch import (
    BatchResult,
    PreparedBatch,
    _applied_result,
    _portable_registration_path,
    _write_partitioned_parquet,
)
from periplus.materialization.contracts import LiveBatchWork
from periplus.materialization.http import CreateMaterializationRun
from periplus.materialization.live import (
    ActiveGeneration,
    ChangeWindow,
    LiveCdcConnection,
    RegistryMismatch,
    _consumer_name,
    _run_live_owner,
    _wait_until_applied,
    _window_batches,
)
from periplus.materialization.registry import (
    PROJECTIONS,
    REGISTRY_DIGEST,
    RELATIONS,
    discover_projections,
    registry_digest,
    validate_registry,
)
from periplus.materialization.runtime import (
    ActivationWork,
    BatchWork,
    _activate_or_catch_up,
    _execute_batch,
    _handle_activation,
    _handle_batch,
    _invalidate_generation,
    _is_retryable_batch_failure,
    publish_activation,
    publish_batch,
    publish_plan,
)
from periplus.platform.messaging.catalogue_queue import (
    MATERIALIZATION_ACTIVATE_SUBJECT,
    MATERIALIZATION_BATCH_SUBJECT,
    MATERIALIZATION_PLAN_SUBJECT,
    WORK_STREAM,
)
from periplus.platform.catalogue.client import Catalogue, _column_type
from periplus.platform.catalogue.schema import expected_columns


class MaterializationRegistryTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)

    def test_legacy_active_generation_scopes_startup_validation_to_ingest(
        self,
    ) -> None:
        legacy = SimpleNamespace(
            config=SimpleNamespace(alias='periplus'),
            trusted_remote_rows=lambda sql: [('html_nodes', 'content_sha256')],
        )
        self.assertFalse(Catalogue._active_registry_matches(legacy))

    def test_registry_is_exactly_the_projection_directory(self) -> None:
        validate_registry()
        self.assertEqual(
            [spec.name for spec in PROJECTIONS],
            sorted(
                path.stem
                for path in (
                    Path(__file__).parents[1]
                    / "src"
                    / "periplus"
                    / "materialization"
                    / "projections"
                ).glob("*.py")
                if path.name != "__init__.py"
                and not path.name.startswith("_")
            ),
        )
        self.assertEqual(len(REGISTRY_DIGEST), 64)
        self.assertTrue(
            all(
                spec.ownership_grain in {"content", "visit", "generation"}
                for spec in PROJECTIONS
            )
        )
        for spec in PROJECTIONS:
            names = set(spec.arrow_schema.names)
            self.assertTrue(spec.columns)
            self.assertTrue(
                all(
                    transform.column in names
                    for transform in spec.partitioning
                )
            )
        self.assertEqual(
            sum(
                spec.content_presence_predicate is not None
                for spec in PROJECTIONS
            ),
            1,
        )

    def test_add_edit_delete_is_one_projection_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package_name = f"projection_fixture_{uuid4().hex}"
            package = Path(directory) / package_name
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            sys.path.insert(0, directory)
            try:
                self._write_fixture_projection(
                    package / "alpha.py",
                    description="alpha-v1",
                )
                importlib.invalidate_caches()
                self.assertEqual(
                    [item.name for item in discover_projections(package_name)],
                    ["alpha"],
                )
                initial = discover_projections(package_name)
                initial_digest = registry_digest(initial)
                self._write_fixture_projection(
                    package / "alpha.py",
                    description="alpha-v1",
                    implementation_marker="changed",
                )
                sys.modules.pop(f"{package_name}.alpha", None)
                importlib.invalidate_caches()
                changed = discover_projections(package_name)
                self.assertNotEqual(
                    initial_digest,
                    registry_digest(changed),
                )

                self._write_fixture_projection(
                    package / "beta.py",
                    description="beta-v1",
                )
                importlib.invalidate_caches()
                self.assertEqual(
                    [item.name for item in discover_projections(package_name)],
                    ["alpha", "beta"],
                )

                self._write_fixture_projection(
                    package / "alpha.py",
                    description="alpha-version-two",
                )
                sys.modules.pop(f"{package_name}.alpha", None)
                importlib.invalidate_caches()
                edited = discover_projections(package_name)
                self.assertEqual(edited[0].description, "alpha-version-two")

                (package / "beta.py").unlink()
                sys.modules.pop(f"{package_name}.beta", None)
                importlib.invalidate_caches()
                self.assertEqual(
                    [item.name for item in discover_projections(package_name)],
                    ["alpha"],
                )
            finally:
                sys.path.remove(directory)
                for module_name in tuple(sys.modules):
                    if module_name == package_name or module_name.startswith(
                        package_name + "."
                    ):
                        sys.modules.pop(module_name, None)

    @staticmethod
    def _write_fixture_projection(
        path: Path,
        *,
        description: str,
        implementation_marker: str = "initial",
    ) -> None:
        path.write_text(
            "\n".join(
                (
                    "import pyarrow as pa",
                    "from periplus.materialization.registry import "
                    "ProjectionColumn, ProjectionSpec",
                    f"# implementation: {implementation_marker}",
                    "def project(context):",
                    "    return pa.Table.from_arrays("
                    "[pa.array([], type=pa.string())], "
                    "names=['value'])",
                    "PROJECTION = ProjectionSpec(",
                    f"    name={path.stem!r},",
                    "    ownership_grain='visit',",
                    "    columns=(ProjectionColumn("
                    "'value', pa.string(), 'VARCHAR', 'Value.', False),),",
                    "    partitioning=(),",
                    "    sort_order=('value ASC',),",
                    "    projector=project,",
                    f"    description={description!r},",
                    "    identity_columns=('value',),",
                    ")",
                )
            )
            + "\n",
            encoding="utf-8",
        )

    def test_default_batch_size_limits_file_fanout(self) -> None:
        self.assertEqual(CreateMaterializationRun().batch_size, 500)

    def test_projection_files_do_not_own_runtime_catalogue_objects(self) -> None:
        projection_root = (
            Path(__file__).parents[1]
            / "src"
            / "periplus"
            / "materialization"
            / "projections"
        )
        violations: list[str] = []
        for path in projection_root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if (
                "CatalogueObject" in source
                or "CREATE OR REPLACE VIEW" in source.upper()
                or "CREATE OR REPLACE MACRO" in source.upper()
            ):
                violations.append(path.name)
        self.assertEqual(violations, [])

    @patch("periplus.materialization.runtime.catalogue_from_env")
    def test_rebuild_catches_up_visits_inserted_after_its_snapshot(
        self,
        catalogue_from_env,
    ) -> None:
        catalogue = MagicMock()
        catalogue_from_env.return_value.__enter__.return_value = catalogue
        catalogue.latest_snapshot.return_value = 12
        catalogue.remote_transaction.side_effect = _transaction

        def rows(sql):
            if "duckdb_tables()" in sql:
                return [(1,)]
            if "ducklake_table_changes" in sql:
                return [("visit-two",), ("visit-three",)]
            raise AssertionError(sql)

        catalogue.trusted_remote_rows.side_effect = rows
        run = _run(covered_snapshot=10, batch_size=1)

        result = _activate_or_catch_up(run)

        self.assertEqual(
            result,
            (12, [["visit-two"], ["visit-three"]]),
        )
        catalogue.activate_materialization_generations.assert_not_called()
        catalogue.trusted_remote_execute.assert_not_called()

    @patch("periplus.materialization.runtime.install_public_catalogue")
    @patch("periplus.materialization.runtime._validate_generation")
    @patch("periplus.materialization.runtime.catalogue_from_env")
    def test_activation_swaps_every_relation_public_api_and_state_atomically(
        self,
        catalogue_from_env,
        validate,
        install_public,
    ) -> None:
        inside_transaction = False

        @contextmanager
        def transaction():
            nonlocal inside_transaction
            self.assertFalse(inside_transaction)
            inside_transaction = True
            try:
                yield
            finally:
                inside_transaction = False

        catalogue = MagicMock()
        catalogue_from_env.return_value.__enter__.return_value = catalogue
        catalogue.latest_snapshot.return_value = 10
        catalogue.last_committed_snapshot.return_value = 11
        catalogue.remote_transaction.side_effect = transaction
        catalogue.trusted_remote_rows.return_value = [(1,)]
        catalogue.activate_materialization_generations.side_effect = (
            lambda *_args, **_kwargs: self.assertTrue(inside_transaction)
        )
        catalogue.trusted_remote_execute.side_effect = (
            lambda _sql: self.assertTrue(inside_transaction)
        )
        install_public.side_effect = (
            lambda *_args, **_kwargs: self.assertTrue(inside_transaction)
        )
        run = _run(covered_snapshot=10, batch_size=500)

        activated_snapshot = _activate_or_catch_up(run)

        self.assertEqual(activated_snapshot, 11)
        validate.assert_called_once_with(catalogue, run)
        catalogue.activate_materialization_generations.assert_called_once_with(
            {
                RELATIONS[name]: table
                for name, table in run.generation_tables.items()
            },
            activation_id=run.id.hex,
            transaction=False,
        )
        install_public.assert_called_once_with(
            catalogue,
            transaction=False,
        )
        self.assertEqual(catalogue.trusted_remote_execute.call_count, 0)
        from periplus.materialization.state import active_generation
        self.assertEqual(active_generation().id, run.id)
        self.assertFalse(inside_transaction)

    @patch("periplus.materialization.runtime.catalogue_from_env")
    def test_generation_fence_uses_supported_ducklake_delete(
        self,
        catalogue_from_env,
    ) -> None:
        generation_id = uuid4()
        catalogue = MagicMock()
        catalogue_from_env.return_value.__enter__.return_value = catalogue
        catalogue.remote_transaction.side_effect = _transaction
        catalogue.trusted_remote_rows.return_value = [(1,)]

        from periplus.materialization.state import publish_generation, active_generation
        publish_generation(SimpleNamespace(id=generation_id, batch_size=50, registry_digest=REGISTRY_DIGEST), 10)
        self.assertTrue(_invalidate_generation(generation_id))
        self.assertIsNone(active_generation())
        catalogue_from_env.assert_not_called()


class MaterializationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    @patch("periplus.materialization.runtime._finalize_activation")
    @patch(
        "periplus.materialization.runtime._activate_or_catch_up",
        return_value=42,
    )
    async def test_activation_is_verified_and_finalized_before_ack(
        self,
        activate,
        finalize,
    ) -> None:
        run_id = uuid4()
        activating = SimpleNamespace(id=run_id, status="activating")
        completed = SimpleNamespace(
            id=run_id,
            status="completed",
            output_rows=100,
        )
        store = AsyncMock()
        store.claim_activation.return_value = activating
        store.complete.return_value = completed
        message = _message(
            ActivationWork(
                run_id=run_id,
                completed_batches=3,
            ).model_dump_json().encode()
        )

        def finalize_after_completion(run):
            self.assertEqual(store.complete.await_count, 1)
            self.assertIs(run, completed)

        finalize.side_effect = finalize_after_completion

        await _handle_activation(message, AsyncMock(), store)

        activate.assert_called_once_with(activating)
        store.complete.assert_awaited_once_with(
            run_id,
            activation_snapshot=42,
        )
        finalize.assert_called_once_with(completed)
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    @patch("periplus.materialization.runtime._finalize_activation")
    @patch("periplus.materialization.runtime._activate_or_catch_up")
    async def test_activation_redelivery_retries_post_swap_verification(
        self,
        activate,
        finalize,
    ) -> None:
        run_id = uuid4()
        completed = SimpleNamespace(id=run_id, status="completed")
        store = AsyncMock()
        store.claim_activation.return_value = None
        store.get.return_value = completed
        message = _message(
            ActivationWork(
                run_id=run_id,
                completed_batches=3,
            ).model_dump_json().encode()
        )

        await _handle_activation(message, AsyncMock(), store)

        activate.assert_not_called()
        finalize.assert_called_once_with(completed)
        message.ack.assert_awaited_once()

    @patch(
        "periplus.materialization.runtime._finalize_activation",
        side_effect=RuntimeError("catalogue unavailable"),
    )
    @patch("periplus.materialization.runtime._activate_or_catch_up")
    async def test_activation_is_not_acked_before_verification_succeeds(
        self,
        activate,
        _finalize,
    ) -> None:
        run_id = uuid4()
        store = AsyncMock()
        store.claim_activation.return_value = None
        store.get.return_value = SimpleNamespace(
            id=run_id,
            status="completed",
        )
        message = _message(
            ActivationWork(
                run_id=run_id,
                completed_batches=3,
            ).model_dump_json().encode()
        )

        await _handle_activation(message, AsyncMock(), store)

        activate.assert_not_called()
        message.ack.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=1)

    def test_live_window_batches_are_deterministic_and_bounded(self) -> None:
        generation_id = uuid4()
        window = ChangeWindow(
            start_snapshot=10,
            end_snapshot=14,
            visit_ids=("a", "b", "c"),
        )

        first = _window_batches(generation_id, window, batch_size=2)
        second = _window_batches(generation_id, window, batch_size=2)

        self.assertEqual(first, second)
        self.assertEqual([batch.visit_ids for batch in first], [("a", "b"), ("c",)])
        self.assertEqual({batch.snapshot for batch in first}, {14})
        self.assertEqual({batch.generation_id for batch in first}, {generation_id})

    @patch("periplus.materialization.runtime._execute_batch")
    async def test_lost_ack_after_commit_reconciles_as_a_noop(
        self,
        execute,
    ) -> None:
        batch_id = uuid4()
        run_id = uuid4()
        batch = SimpleNamespace(
            id=batch_id,
            run_id=run_id,
            ordinal=0,
            visit_ids=("visit-one",),
            attempts=1,
            status="running",
        )
        run = SimpleNamespace(
            id=run_id,
            status="running",
            completed_batches=0,
            total_batches=2,
        )
        after_commit = SimpleNamespace(
            id=run_id,
            status="running",
            completed_batches=1,
            total_batches=2,
        )
        execute.return_value = _batch_result()
        store = AsyncMock()
        store.start_batch.return_value = batch
        store.get.return_value = run
        store.complete_batch.return_value = after_commit
        first_delivery = _message(
            BatchWork(batch_id=batch_id).model_dump_json().encode()
        )
        first_delivery.ack.side_effect = RuntimeError("connection lost")

        await _handle_batch(
            first_delivery,
            AsyncMock(),
            store,
            MagicMock(),
        )

        store.complete_batch.assert_awaited_once()
        store.fail.assert_not_awaited()
        first_delivery.nak.assert_not_awaited()

        # A restarted worker receives the unacknowledged message, observes
        # completed durable state, and performs no projection or append.
        redelivery_store = AsyncMock()
        redelivery_store.start_batch.return_value = SimpleNamespace(
            **{
                **batch.__dict__,
                "status": "completed",
            }
        )
        redelivery_store.get.return_value = after_commit
        redelivery = _message(
            BatchWork(batch_id=batch_id).model_dump_json().encode()
        )

        await _handle_batch(
            redelivery,
            AsyncMock(),
            redelivery_store,
            MagicMock(),
        )

        self.assertEqual(execute.call_count, 1)
        redelivery_store.complete_batch.assert_not_awaited()
        redelivery.ack.assert_awaited_once()

    @patch(
        "periplus.materialization.runtime._try_cleanup_failed_run",
        new_callable=AsyncMock,
    )
    @patch(
        "periplus.materialization.runtime._execute_batch",
        side_effect=duckdb.InvalidInputException("invalid projection"),
    )
    async def test_failure_before_commit_fails_and_cleans_generation(
        self,
        _execute,
        cleanup_failed_run,
    ) -> None:
        batch_id = uuid4()
        run_id = uuid4()
        batch = SimpleNamespace(
            id=batch_id,
            run_id=run_id,
            ordinal=4,
            visit_ids=("visit-one", "visit-two"),
            attempts=1,
            status="running",
        )
        failed = SimpleNamespace(id=run_id, status="failed")
        store = AsyncMock()
        store.start_batch.return_value = batch
        store.get.return_value = SimpleNamespace(id=run_id, status="running")
        store.get_batch.return_value = batch
        store.fail.return_value = failed
        message = _message(
            BatchWork(batch_id=batch_id).model_dump_json().encode()
        )

        await _handle_batch(message, AsyncMock(), store, MagicMock())

        store.fail.assert_awaited_once()
        self.assertIn("ordinal 4", str(store.fail.await_args.args[1]))
        cleanup_failed_run.assert_awaited_once_with(store, failed)
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    @patch(
        "periplus.materialization.runtime._execute_batch",
        side_effect=duckdb.TransactionException("transaction conflict"),
    )
    async def test_retryable_commit_conflict_leaves_batch_for_redelivery(
        self,
        _execute,
    ) -> None:
        batch_id = uuid4()
        run_id = uuid4()
        batch = SimpleNamespace(
            id=batch_id,
            run_id=run_id,
            ordinal=2,
            visit_ids=("visit-one",),
            attempts=1,
            status="running",
        )
        store = AsyncMock()
        store.start_batch.return_value = batch
        store.get.return_value = SimpleNamespace(id=run_id, status="running")
        store.get_batch.return_value = batch
        message = _message(
            BatchWork(batch_id=batch_id).model_dump_json().encode()
        )

        await _handle_batch(message, AsyncMock(), store, MagicMock())

        store.fail.assert_not_awaited()
        message.ack.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=1)

    @patch("periplus.materialization.runtime._invalidate_generation")
    @patch(
        "periplus.materialization.runtime._generation_recovery_context",
        return_value=(120, 500),
    )
    @patch(
        "periplus.materialization.runtime._execute_live_batch",
        side_effect=duckdb.IOException(
            'IO Error: Cannot open file "/lake/material/missing.parquet": '
            "No such file or directory"
        ),
    )
    async def test_unreadable_active_file_starts_full_rebuild_and_fences(
        self,
        _execute,
        recovery_context,
        invalidate,
    ) -> None:
        work = LiveBatchWork(
            batch_id=uuid4(),
            generation_id=uuid4(),
            ordinal=0,
            snapshot=121,
            visit_ids=("visit-one",),
        )
        rebuild = SimpleNamespace(id=uuid4())
        store = AsyncMock()
        store.ensure_rebuild.return_value = (rebuild, True)
        invalidate.return_value = True
        jetstream = AsyncMock()
        message = _message(work.model_dump_json().encode())

        await _handle_batch(message, jetstream, store, MagicMock())

        recovery_context.assert_called_once_with(work.generation_id)
        store.ensure_rebuild.assert_awaited_once_with(
            source_snapshot=120,
            batch_size=500,
        )
        invalidate.assert_called_once_with(work.generation_id)
        jetstream.publish.assert_awaited_once()
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    @patch("periplus.materialization.live._wait", new_callable=AsyncMock)
    async def test_live_cdc_waits_for_every_applied_marker(
        self,
        wait,
    ) -> None:
        generation_id = uuid4()
        batch_id = uuid4()
        generation = SimpleNamespace(id=generation_id)
        cdc = MagicMock()
        cdc.active_generation.return_value = generation
        cdc.applied.side_effect = [frozenset(), frozenset({batch_id})]

        applied = await _wait_until_applied(
            cdc,
            generation_id,
            "consumer",
            (batch_id,),
            __import__("asyncio").Event(),
        )

        self.assertTrue(applied)
        cdc.heartbeat.assert_called_once_with("consumer")
        wait.assert_awaited_once()

    def test_live_cdc_continues_from_active_snapshot_with_inserts_only(
        self,
    ) -> None:
        generation = ActiveGeneration(
            id=uuid4(),
            covered_snapshot=42,
            batch_size=500,
            registry_digest=REGISTRY_DIGEST,
        )
        connection = MagicMock()
        connection.execute.return_value.fetchall.side_effect = [[], []]
        cdc = LiveCdcConnection.__new__(LiveCdcConnection)
        cdc.catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="periplus"),
            trusted_connection=connection,
        )

        consumer = cdc.ensure_consumer(generation)

        self.assertIn(generation.id.hex, consumer)
        creation_sql = connection.execute.call_args_list[1].args[0]
        self.assertIn("table_name := 'ingest.visits'", creation_sql)
        self.assertIn("start_at := '42'", creation_sql)
        self.assertIn("change_types := ['insert']", creation_sql)

    def test_live_cdc_consumer_creation_is_replica_safe(self) -> None:
        generation = ActiveGeneration(
            id=uuid4(),
            covered_snapshot=42,
            batch_size=500,
            registry_digest=REGISTRY_DIGEST,
        )
        connection = MagicMock()
        connection.execute.return_value.fetchall.side_effect = [
            [],
            duckdb.ConstraintException("consumer already exists"),
            [(_consumer_name(generation.id),)],
        ]
        cdc = LiveCdcConnection.__new__(LiveCdcConnection)
        cdc.catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="periplus"),
            trusted_connection=connection,
        )

        consumer = cdc.ensure_consumer(generation)

        self.assertEqual(consumer, _consumer_name(generation.id))
        self.assertEqual(connection.execute.call_count, 3)

    @patch("periplus.materialization.live.run_live_materialization")
    async def test_live_cdc_owner_stops_when_leadership_is_lost(
        self,
        run_live_materialization,
    ) -> None:
        lost = __import__("asyncio").Event()
        owner_stopped = __import__("asyncio").Event()
        guard = SimpleNamespace(
            wait_lost=lost.wait,
        )

        async def run(_jetstream, *, stop, publish):
            del publish
            await stop.wait()
            owner_stopped.set()

        run_live_materialization.side_effect = run
        owner = __import__("asyncio").create_task(
            _run_live_owner(
                MagicMock(),
                stop=__import__("asyncio").Event(),
                guard=guard,
                publish=AsyncMock(),
            )
        )

        lost.set()
        await __import__("asyncio").wait_for(owner_stopped.wait(), timeout=1)
        await __import__("asyncio").wait_for(owner, timeout=1)

        run_live_materialization.assert_awaited_once()

    async def test_work_messages_have_stable_deduplication_ids(self) -> None:
        jetstream = AsyncMock()
        store = AsyncMock()
        run_id = uuid4()
        batch_id = uuid4()

        await publish_plan(jetstream, store, run_id)
        await publish_batch(jetstream, store, batch_id)
        await publish_activation(
            jetstream,
            store,
            run_id,
            completed_batches=7,
        )

        calls = jetstream.publish.await_args_list
        self.assertEqual(
            [call.args[0] for call in calls],
            [
                MATERIALIZATION_PLAN_SUBJECT,
                MATERIALIZATION_BATCH_SUBJECT,
                MATERIALIZATION_ACTIVATE_SUBJECT,
            ],
        )
        self.assertTrue(all(call.kwargs["stream"] == WORK_STREAM for call in calls))
        self.assertEqual(
            calls[0].kwargs["headers"]["Nats-Msg-Id"],
            f"materialization-plan:{run_id}",
        )
        self.assertEqual(
            calls[1].kwargs["headers"]["Nats-Msg-Id"],
            f"materialization-batch:{batch_id}",
        )

    async def test_failed_publication_is_not_marked(self) -> None:
        jetstream = AsyncMock()
        jetstream.publish.side_effect = RuntimeError("nats unavailable")
        store = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "nats unavailable"):
            await publish_batch(jetstream, store, uuid4())

        store.mark_batch_published.assert_not_awaited()


class MaterializationParquetTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)

    def test_every_registry_partition_layout_registers_with_ducklake(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lake = root / "lake"
            lake.mkdir()
            connection = duckdb.connect()
            connection.install_extension("ducklake")
            connection.load_extension("ducklake")
            metadata = str(root / "metadata.ducklake").replace("'", "''")
            data_path = (str(lake) + "/").replace("'", "''")
            connection.execute(
                f"ATTACH 'ducklake:{metadata}' AS lake "
                f"(DATA_PATH '{data_path}')"
            )
            connection.execute("CREATE SCHEMA lake.material")
            catalogue = SimpleNamespace(
                config=SimpleNamespace(data_path=str(lake)),
                trusted_connection=connection,
            )
            for spec in PROJECTIONS:
                definitions = ", ".join(
                    f'"{name}" {_column_type(column)}'
                    for name, column in expected_columns()[
                        spec.relation
                    ].items()
                )
                connection.execute(
                    f'CREATE TABLE lake.material."{spec.name}" '
                    f"({definitions})"
                )
                if spec.layout.partition_by:
                    connection.execute(
                        f'ALTER TABLE lake.material."{spec.name}" '
                        "SET PARTITIONED BY "
                        f"({', '.join(spec.layout.partition_by)})"
                    )
                rows = [self._row_for(spec, index=index) for index in range(2)]
                if spec.name == "link_occurrences":
                    rows[1]["observed_at"] = datetime(
                        2026,
                        2,
                        1,
                        tzinfo=UTC,
                    )
                paths = _write_partitioned_parquet(
                    catalogue,
                    pa.Table.from_pylist(rows, schema=spec.arrow_schema),
                    run_id=uuid4(),
                    batch_id=uuid4(),
                    table_name=spec.name,
                )
                for file in paths:
                    connection.execute(
                        "CALL ducklake_add_data_files("
                        "'lake', ?, ?, schema => 'material')",
                        [spec.name, file.path],
                    )
                count = connection.execute(
                    f'SELECT count(*) FROM lake.material."{spec.name}"'
                ).fetchone()[0]
                self.assertEqual(count, 2)
            connection.close()

    def test_relative_registration_is_readable_from_another_working_directory(
        self,
    ) -> None:
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer_working_directory = root / "backend"
            host_working_directory = root / "host"
            data_path = root / ".periplus" / "lake"
            writer_working_directory.mkdir()
            host_working_directory.mkdir()
            data_path.mkdir(parents=True)
            metadata = root / "metadata.ducklake"
            data_file = data_path / "material" / "data" / "value.parquet"
            data_file.parent.mkdir(parents=True)
            duckdb.connect().execute(
                f"COPY (SELECT 42::INTEGER AS value) "
                f"TO '{data_file}' (FORMAT PARQUET)"
            ).close()
            try:
                os.chdir(writer_working_directory)
                relative = _portable_registration_path(data_file)
                self.assertFalse(Path(relative).is_absolute())
                self.assertEqual(
                    _portable_registration_path(
                        "s3://periplus/material/data/value.parquet"
                    ),
                    "s3://periplus/material/data/value.parquet",
                )
                writer = duckdb.connect()
                writer.install_extension("ducklake")
                writer.load_extension("ducklake")
                writer.execute(
                    f"ATTACH 'ducklake:{metadata}' AS lake "
                    f"(DATA_PATH '{data_path}/')"
                )
                writer.execute("CREATE SCHEMA lake.material")
                writer.execute(
                    "CREATE TABLE lake.material.portable (value INTEGER)"
                )
                writer.execute(
                    "CALL ducklake_add_data_files("
                    "'lake', 'portable', ?, schema => 'material')",
                    [relative],
                )
                writer.close()

                os.chdir(host_working_directory)
                host = duckdb.connect()
                host.load_extension("ducklake")
                host.execute(
                    f"ATTACH 'ducklake:{metadata}' AS lake "
                    f"(DATA_PATH '{data_path}/')"
                )
                self.assertEqual(
                    host.execute(
                        "SELECT value FROM lake.material.portable"
                    ).fetchall(),
                    [(42,)],
                )
                host.close()
            finally:
                os.chdir(original)

    def test_output_is_registry_typed_and_bucketed(self) -> None:
        spec = next(
            (
                item
                for item in PROJECTIONS
                if any(
                    transform.kind == "bucket"
                    for transform in item.partitioning
                )
            ),
            None,
        )
        if spec is None:
            self.skipTest("registry has no bucket-partitioned projection")
        table = pa.Table.from_pylist(
            [self._row_for(spec, index=0)],
            schema=spec.arrow_schema,
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = duckdb.connect()
            catalogue = SimpleNamespace(
                config=SimpleNamespace(data_path=directory),
                trusted_connection=connection,
            )
            paths = _write_partitioned_parquet(
                catalogue,
                table,
                run_id=uuid4(),
                batch_id=uuid4(),
                table_name=spec.name,
            )
            self.assertEqual(len(paths), 1)
            self.assertIn("bucket=", paths[0].path)
            described = connection.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{paths[0].path}')"
            ).fetchall()
            self.assertEqual(
                [
                    row[0]
                    for row in described
                    if str(row[0])
                    not in {transform.kind for transform in spec.partitioning}
                ],
                list(spec.arrow_schema.names),
            )
            connection.close()

    def test_time_partition_is_driven_by_the_registry_entry(self) -> None:
        spec = next(
            (
                item
                for item in PROJECTIONS
                if any(
                    transform.kind in {"day", "month", "year"}
                    for transform in item.partitioning
                )
            ),
            None,
        )
        if spec is None:
            self.skipTest("registry has no time-partitioned projection")
        transform = next(
            item
            for item in spec.partitioning
            if item.kind in {"day", "month", "year"}
        )
        rows = []
        second_partition = (
            datetime(2027, 1, 1, tzinfo=UTC)
            if transform.kind == "year"
            else datetime(2026, 2, 1, tzinfo=UTC)
        )
        for index, observed_at in enumerate(
            (
                datetime(2026, 1, 31, tzinfo=UTC),
                second_partition,
            )
        ):
            row = self._row_for(spec, index=index)
            row[transform.column] = observed_at
            rows.append(row)
        table = pa.Table.from_pylist(rows, schema=spec.arrow_schema)
        with tempfile.TemporaryDirectory() as directory:
            connection = duckdb.connect()
            catalogue = SimpleNamespace(
                config=SimpleNamespace(data_path=directory),
                trusted_connection=connection,
            )
            paths = _write_partitioned_parquet(
                catalogue,
                table,
                run_id=uuid4(),
                batch_id=uuid4(),
                table_name=spec.name,
            )
            self.assertEqual(len(paths), 2)
            self.assertTrue(
                all(
                    f"{transform.kind}=" in file.path
                    for file in paths
                )
            )
            connection.close()

    @staticmethod
    def _row_for(spec, *, index: int) -> dict[str, object]:
        row: dict[str, object] = {}
        for column in spec.columns:
            data_type = str(column.arrow_type)
            duckdb_type = str(column.duckdb_type)
            if duckdb_type == "UUID":
                value: object = str(uuid4())
            elif duckdb_type == "JSON":
                value = '{"value":true}'
            elif data_type.startswith("timestamp"):
                value = datetime(2026, 1, 1, tzinfo=UTC)
            elif data_type.startswith("list<"):
                value = ["value"]
            elif data_type.startswith("map<"):
                value = {"key": "value"}
            elif data_type.startswith("int"):
                value = index
            elif "sha256" in column.name:
                value = f"{index:x}".rjust(64, "a")
            else:
                value = f"value-{index}"
            row[column.name] = value
        return row

    def test_schema_mismatch_is_rejected_before_file_creation(self) -> None:
        if not PROJECTIONS:
            self.skipTest("registry has no projections")
        spec = PROJECTIONS[0]
        with tempfile.TemporaryDirectory() as directory:
            catalogue = SimpleNamespace(
                config=SimpleNamespace(data_path=directory),
                trusted_connection=duckdb.connect(),
            )
            with self.assertRaisesRegex(ValueError, "does not match registry"):
                _write_partitioned_parquet(
                    catalogue,
                    pa.table({"wrong": [1]}),
                    run_id=uuid4(),
                    batch_id=uuid4(),
                    table_name=spec.name,
                )
            catalogue.trusted_connection.close()

    def test_retry_after_precommit_crash_uses_a_fresh_file_set(self) -> None:
        spec = PROJECTIONS[0]
        table = pa.Table.from_pylist(
            [self._row_for(spec, index=0)],
            schema=spec.arrow_schema,
        )
        with tempfile.TemporaryDirectory() as directory:
            catalogue = SimpleNamespace(
                config=SimpleNamespace(data_path=directory),
                trusted_connection=duckdb.connect(),
            )
            run_id = uuid4()
            batch_id = uuid4()

            abandoned = _write_partitioned_parquet(
                catalogue,
                table,
                run_id=run_id,
                batch_id=batch_id,
                table_name=spec.name,
            )
            retry = _write_partitioned_parquet(
                catalogue,
                table,
                run_id=run_id,
                batch_id=batch_id,
                table_name=spec.name,
            )

            self.assertNotEqual(abandoned, retry)
            self.assertTrue(
                all(
                    Path(file.path).exists()
                    for file in abandoned + retry
                )
            )
            catalogue.trusted_connection.close()

    def test_applied_marker_makes_redelivery_a_noop(self) -> None:
        batch_id = uuid4()

        class Catalogue:
            def trusted_remote_rows(self, _sql):
                return [(3, 10, 20, 30)]

        from periplus.materialization.state import record_applied
        record_applied(uuid4(), batch_id, 10, SimpleNamespace(source_items=3, source_bytes=10, output_rows=20, output_bytes=30))
        result = _applied_result(Catalogue(), batch_id)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.already_applied)
        self.assertEqual(result.source_items, 3)

    @patch("periplus.materialization.runtime.run_with_catalogue_retry")
    @patch("periplus.materialization.runtime.commit_prepared_batch")
    @patch("periplus.materialization.runtime.prepare_batch")
    @patch("periplus.materialization.runtime.catalogue_from_env")
    def test_commit_retry_reuses_the_same_immutable_file_set(
        self,
        catalogue_from_env,
        prepare_batch,
        commit_prepared_batch,
        run_with_retry,
    ) -> None:
        catalogue = MagicMock()
        catalogue_from_env.return_value.__enter__.return_value = catalogue
        prepared = PreparedBatch(
            source_items=2,
            source_bytes=100,
            output_rows=20,
            output_bytes=200,
            project_seconds=1,
            parquet_seconds=2,
            files={spec.name: () for spec in PROJECTIONS},
        )
        expected = _batch_result()
        prepare_batch.return_value = prepared
        commit_prepared_batch.side_effect = [
            duckdb.TransactionException("transaction conflict"),
            expected,
        ]

        def retry(operation, **_kwargs):
            with self.assertRaises(duckdb.TransactionException):
                operation()
            return operation()

        run_with_retry.side_effect = retry
        repository = MagicMock()
        run = SimpleNamespace(id=uuid4())
        batch = SimpleNamespace(id=uuid4())

        result = _execute_batch(repository, run, batch)

        self.assertIs(result, expected)
        prepare_batch.assert_called_once_with(
            catalogue,
            repository,
            run,
            batch,
            active_generation=False,
        )
        self.assertEqual(commit_prepared_batch.call_count, 2)
        self.assertTrue(
            all(
                call.args == (catalogue, run, batch, prepared)
                for call in commit_prepared_batch.call_args_list
            )
        )

    def test_registry_mismatch_fences_live_cdc(self) -> None:
        from periplus.materialization.live import LiveCdcConnection

        cdc = LiveCdcConnection.__new__(LiveCdcConnection)
        cdc.catalogue = SimpleNamespace(
            trusted_remote_rows=lambda _sql: [
                (
                    str(uuid4()),
                    10,
                    500,
                    "different-registry",
                )
            ]
        )

        from periplus.materialization.state import publish_generation
        publish_generation(SimpleNamespace(id=uuid4(), batch_size=50, registry_digest="different-registry"), 10)
        with self.assertRaises(RegistryMismatch):
            cdc.active_generation()

    def test_only_transient_catalogue_failures_retry_a_batch(self) -> None:
        self.assertTrue(
            _is_retryable_batch_failure(
                duckdb.TransactionException("transaction conflict")
            )
        )
        self.assertTrue(
            _is_retryable_batch_failure(duckdb.IOException("unavailable"))
        )
        self.assertFalse(
            _is_retryable_batch_failure(
                duckdb.IOException(
                    'Cannot open file "/lake/material/missing.parquet": '
                    "No such file or directory"
                )
            )
        )
        self.assertFalse(
            _is_retryable_batch_failure(
                duckdb.InvalidInputException("invalid projection")
            )
        )


def _batch_result() -> BatchResult:
    return BatchResult(
        source_items=1,
        source_bytes=10,
        output_rows=20,
        output_bytes=30,
        project_seconds=0.1,
        parquet_seconds=0.2,
        commit_seconds=0.3,
    )


def _message(data: bytes) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        in_progress=AsyncMock(),
        ack=AsyncMock(),
        nak=AsyncMock(),
        term=AsyncMock(),
    )


@contextmanager
def _transaction():
    yield


def _run(*, covered_snapshot: int, batch_size: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        registry_digest=REGISTRY_DIGEST,
        covered_snapshot=covered_snapshot,
        batch_size=batch_size,
        generation_tables={
            spec.name: f"_hidden_{spec.name}"
            for spec in PROJECTIONS
        },
    )


if __name__ == "__main__":
    unittest.main()
