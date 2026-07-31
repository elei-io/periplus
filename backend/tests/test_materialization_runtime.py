from __future__ import annotations

from datetime import UTC, datetime
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import duckdb
import pyarrow as pa

from atlas.materialization.batch import (
    BatchResult,
    PreparedBatch,
    _applied_result,
    _write_link_identity_parquet,
    _write_partitioned_parquet,
    discard_batch_staging,
    discard_run_link_staging,
    merge_live_links,
    populate_final_links,
)
from atlas.materialization.contracts import LiveBatchWork
from atlas.materialization.document_projection import LINK_SCHEMA
from atlas.materialization.http import CreateMaterializationRun
from atlas.materialization.live import ChangeWindow, _window_batches
from atlas.materialization.runtime import (
    ActivationWork,
    BatchWork,
    _execute_batch,
    _handle_activation,
    _handle_batch,
    _is_retryable_batch_failure,
    publish_activation,
    publish_batch,
    publish_plan,
)
from atlas.platform.messaging.catalogue_queue import (
    MATERIALIZATION_ACTIVATE_SUBJECT,
    MATERIALIZATION_BATCH_SUBJECT,
    MATERIALIZATION_PLAN_SUBJECT,
    WORK_STREAM,
)


class MaterializationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    @patch("atlas.materialization.runtime._finalize_activation")
    @patch(
        "atlas.materialization.runtime._activate_or_catch_up",
        return_value=42,
    )
    async def test_activation_finalizes_retired_tables_before_ack(
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

        def finalize_after_completion(run):
            self.assertEqual(store.complete.await_count, 1)
            self.assertIs(run, completed)

        finalize.side_effect = finalize_after_completion
        message = SimpleNamespace(
            data=ActivationWork(
                run_id=run_id,
                completed_batches=3,
            ).model_dump_json().encode(),
            in_progress=AsyncMock(),
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
        )

        await _handle_activation(message, AsyncMock(), store)

        activate.assert_called_once_with(activating)
        store.complete.assert_awaited_once_with(
            run_id,
            activation_snapshot=42,
        )
        finalize.assert_called_once_with(completed)
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    @patch("atlas.materialization.runtime._finalize_activation")
    @patch("atlas.materialization.runtime._activate_or_catch_up")
    async def test_completed_activation_redelivery_retries_finalization(
        self,
        activate,
        finalize,
    ) -> None:
        run_id = uuid4()
        completed = SimpleNamespace(id=run_id, status="completed")
        store = AsyncMock()
        store.claim_activation.return_value = None
        store.get.return_value = completed
        message = SimpleNamespace(
            data=ActivationWork(
                run_id=run_id,
                completed_batches=3,
            ).model_dump_json().encode(),
            in_progress=AsyncMock(),
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
        )

        await _handle_activation(message, AsyncMock(), store)

        activate.assert_not_called()
        finalize.assert_called_once_with(completed)
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    @patch(
        "atlas.materialization.runtime._finalize_activation",
        side_effect=RuntimeError("catalogue unavailable"),
    )
    @patch("atlas.materialization.runtime._activate_or_catch_up")
    async def test_completed_activation_is_not_acked_until_finalized(
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
        message = SimpleNamespace(
            data=ActivationWork(
                run_id=run_id,
                completed_batches=3,
            ).model_dump_json().encode(),
            in_progress=AsyncMock(),
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
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

    @patch("atlas.materialization.runtime.discard_batch_link_staging")
    @patch("atlas.materialization.runtime.catalogue_config_from_env")
    @patch("atlas.materialization.runtime._execute_live_batch")
    async def test_live_work_uses_the_existing_batch_consumer(
        self,
        execute,
        config,
        discard_links,
    ) -> None:
        work = LiveBatchWork(
            batch_id=uuid4(),
            generation_id=uuid4(),
            ordinal=0,
            snapshot=12,
            visit_ids=("visit-one",),
        )
        execute.return_value = BatchResult(
            source_items=1,
            source_bytes=10,
            output_rows=4,
            output_bytes=20,
            project_seconds=0.1,
            parquet_seconds=0.1,
            commit_seconds=0.1,
        )
        config.return_value = SimpleNamespace(data_path="/lake")
        message = SimpleNamespace(
            data=work.model_dump_json().encode(),
            in_progress=AsyncMock(),
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
        )
        store = AsyncMock()

        await _handle_batch(message, AsyncMock(), store, AsyncMock())

        execute.assert_called_once()
        discard_links.assert_called_once_with(
            "/lake",
            run_id=work.generation_id,
            batch_id=work.batch_id,
        )
        store.start_batch.assert_not_awaited()
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    @patch("atlas.materialization.runtime.discard_batch_staging")
    @patch("atlas.materialization.runtime.catalogue_config_from_env")
    @patch("atlas.materialization.runtime._invalidate_generation")
    @patch(
        "atlas.materialization.runtime._generation_recovery_context",
        return_value=(120, 200),
    )
    @patch(
        "atlas.materialization.runtime._execute_live_batch",
        side_effect=duckdb.IOException(
            'IO Error: Cannot open file "/lake/material/data/missing.parquet": '
            "No such file or directory"
        ),
    )
    async def test_corrupt_live_generation_starts_rebuild_and_releases_delivery(
        self,
        _execute,
        recovery_context,
        invalidate,
        config,
        discard,
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
        def invalidate_after_rebuild(_generation_id):
            self.assertEqual(store.ensure_rebuild.await_count, 1)
            return True

        invalidate.side_effect = invalidate_after_rebuild
        jetstream = AsyncMock()
        config.return_value = SimpleNamespace(data_path="/lake")
        message = SimpleNamespace(
            data=work.model_dump_json().encode(),
            in_progress=AsyncMock(),
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
        )

        await _handle_batch(message, jetstream, store, AsyncMock())

        recovery_context.assert_called_once_with(work.generation_id)
        store.ensure_rebuild.assert_awaited_once_with(
            source_snapshot=120,
            batch_size=200,
        )
        invalidate.assert_called_once_with(work.generation_id)
        jetstream.publish.assert_awaited_once()
        self.assertEqual(
            jetstream.publish.await_args.args[0],
            MATERIALIZATION_PLAN_SUBJECT,
        )
        store.mark_plan_published.assert_awaited_once_with(rebuild.id)
        discard.assert_called_once_with(
            "/lake",
            run_id=work.generation_id,
            batch_id=work.batch_id,
        )
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

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
        self.assertNotIn("headers", calls[2].kwargs)
        store.mark_plan_published.assert_awaited_once_with(run_id)
        store.mark_batch_published.assert_awaited_once_with(batch_id)
        store.mark_activation_published.assert_awaited_once_with(
            run_id,
            completed_batches=7,
        )

    async def test_failed_publication_is_not_marked(self) -> None:
        jetstream = AsyncMock()
        jetstream.publish.side_effect = RuntimeError("nats unavailable")
        store = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "nats unavailable"):
            await publish_batch(jetstream, store, uuid4())

        store.mark_batch_published.assert_not_awaited()

    @patch(
        "atlas.materialization.runtime._try_cleanup_failed_run",
        new_callable=AsyncMock,
    )
    @patch(
        "atlas.materialization.runtime._discard_staging",
        new_callable=AsyncMock,
    )
    @patch(
        "atlas.materialization.runtime._execute_batch",
        side_effect=duckdb.InvalidInputException("invalid projection"),
    )
    async def test_deterministic_batch_failure_fails_and_cleans_once(
        self,
        _execute,
        discard_staging,
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
        run = SimpleNamespace(id=run_id, status="running")
        failed = SimpleNamespace(id=run_id, status="failed")
        store = AsyncMock()
        store.start_batch.return_value = batch
        store.get.return_value = run
        store.get_batch.return_value = batch
        store.fail.return_value = failed
        message = SimpleNamespace(
            data=BatchWork(batch_id=batch_id).model_dump_json().encode(),
            in_progress=AsyncMock(),
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
        )

        await _handle_batch(message, AsyncMock(), store, AsyncMock())

        store.fail.assert_awaited_once()
        failure = store.fail.await_args.args[1]
        self.assertIn("ordinal 4", str(failure))
        self.assertIn("visit-one", str(failure))
        discard_staging.assert_awaited_once_with(batch)
        cleanup_failed_run.assert_awaited_once_with(store, failed)
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()

    @patch(
        "atlas.materialization.runtime._execute_batch",
        side_effect=duckdb.TransactionException("transaction conflict"),
    )
    async def test_transient_batch_failure_is_left_to_jetstream(
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
        message = SimpleNamespace(
            data=BatchWork(batch_id=batch_id).model_dump_json().encode(),
            in_progress=AsyncMock(),
            ack=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
        )

        await _handle_batch(message, AsyncMock(), store, AsyncMock())

        store.fail.assert_not_awaited()
        message.ack.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=1)


class MaterializationParquetTests(unittest.TestCase):
    def test_default_batch_size_is_two_hundred_visits(self) -> None:
        self.assertEqual(CreateMaterializationRun().batch_size, 200)

    def test_batch_staging_cleanup_is_scoped_to_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_id = uuid4()
            batch_id = uuid4()
            sibling_id = uuid4()
            batch_path = (
                Path(directory)
                / "material"
                / "staging"
                / run_id.hex
                / batch_id.hex
            )
            sibling_path = batch_path.parent / sibling_id.hex
            batch_path.mkdir(parents=True)
            sibling_path.mkdir()

            discard_batch_staging(
                directory,
                run_id=run_id,
                batch_id=batch_id,
            )

            self.assertFalse(batch_path.exists())
            self.assertTrue(sibling_path.exists())

    def test_link_staging_cleanup_preserves_registered_batch_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_id = uuid4()
            batch_id = uuid4()
            batch_path = (
                Path(directory)
                / "material"
                / "staging"
                / run_id.hex
                / batch_id.hex
            )
            registered = (
                Path(directory)
                / "material"
                / "data"
                / run_id.hex
                / "html_elements"
                / batch_id.hex
                / "data.parquet"
            )
            sidecar = batch_path / "link_identities" / "data.parquet"
            registered.parent.mkdir(parents=True)
            sidecar.parent.mkdir(parents=True)
            registered.touch()
            sidecar.touch()

            discard_run_link_staging(directory, run_id=run_id)

            self.assertTrue(registered.exists())
            self.assertFalse(sidecar.parent.exists())

    def test_large_output_is_typed_and_bucketed_before_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = duckdb.connect(":memory:")
            catalogue = SimpleNamespace(
                config=SimpleNamespace(data_path=directory),
                trusted_connection=connection,
            )
            page_id = str(uuid4())
            visit_id = str(uuid4())
            table = pa.Table.from_pylist(
                [
                    {
                        "page_id": page_id,
                        "visit_id": visit_id,
                        "document_id": None,
                        "visit_at": "2026-01-01T00:00:00Z",
                    }
                ]
            )

            paths = _write_partitioned_parquet(
                catalogue,
                table,
                run_id=uuid4(),
                batch_id=uuid4(),
                table_name="page_observations",
            )

            self.assertEqual(len(paths), 1)
            self.assertIn("/material/data/", str(paths[0]))
            self.assertNotIn("/material/staging/", str(paths[0]))
            self.assertRegex(str(paths[0]), r"/bucket=\d+/data\.parquet$")
            types = {
                row[0]: row[1]
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{paths[0]}')"
                ).fetchall()
            }
            self.assertEqual(types["page_id"], "UUID")
            self.assertEqual(types["visit_id"], "UUID")
            self.assertEqual(types["visit_at"], "TIMESTAMP WITH TIME ZONE")

    def test_heterogeneous_jsonld_is_typed_in_projected_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = duckdb.connect(":memory:")
            catalogue = SimpleNamespace(
                config=SimpleNamespace(data_path=directory),
                trusted_connection=connection,
            )
            table = pa.Table.from_pylist(
                [
                    {
                        "content_sha256": "a" * 64,
                        "element_index": 3,
                        "type_terms": ["Thing", "Other"],
                        "value": (
                            '{"@graph":[{"@type":"Thing","name":"one"},'
                            '{"@type":["Other"],"values":[1,"x",true]}]}'
                        ),
                    }
                ]
            )

            paths = _write_partitioned_parquet(
                catalogue,
                table,
                run_id=uuid4(),
                batch_id=uuid4(),
                table_name="jsonld_values",
            )

            self.assertEqual(len(paths), 1)
            data_type, value = connection.execute(
                f"SELECT typeof(value), "
                f"json_extract_string(value, '$.@graph[0].name') "
                f"FROM read_parquet('{paths[0]}')"
            ).fetchone()
            self.assertEqual(data_type, "JSON")
            self.assertEqual(value, "one")

    def test_link_dimension_is_deduplicated_once_from_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = duckdb.connect(":memory:")
            connection.execute("CREATE SCHEMA material")
            connection.execute(
                """
                CREATE TABLE material.final_links (
                  link_id UUID, source_page_id UUID, target_page_id UUID,
                  source_url VARCHAR, target_url VARCHAR,
                  relation_scope VARCHAR, first_seen_at TIMESTAMPTZ,
                  last_seen_at TIMESTAMPTZ, visit_count BIGINT,
                  distinct_content_count BIGINT, occurrence_count BIGINT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE material.final_occurrences (
                  occurrence_id UUID, link_id UUID, visit_id UUID,
                  document_id UUID, content_sha256 VARCHAR,
                  element_index INTEGER, raw_href VARCHAR,
                  observed_at TIMESTAMPTZ
                )
                """
            )
            catalogue = SimpleNamespace(
                config=SimpleNamespace(data_path=directory),
                trusted_connection=connection,
                trusted_remote_execute=connection.execute,
            )
            run_id = uuid4()
            link_id = uuid4()
            source_page_id = uuid4()
            target_page_id = uuid4()
            identity = pa.Table.from_pylist(
                [
                    {
                        "link_id": str(link_id),
                        "source_page_id": str(source_page_id),
                        "target_page_id": str(target_page_id),
                        "source_url": "https://example.com/",
                        "target_url": "https://example.com/next",
                        "relation_scope": "same_origin",
                        "first_seen_at": datetime(2026, 1, 1, tzinfo=UTC),
                        "last_seen_at": datetime(2026, 1, 1, tzinfo=UTC),
                        "visit_count": 1,
                        "distinct_content_count": 1,
                        "occurrence_count": 1,
                    }
                ],
                schema=LINK_SCHEMA,
            )
            first_path = _write_link_identity_parquet(
                catalogue,
                identity,
                run_id=run_id,
                batch_id=uuid4(),
            )
            second_path = _write_link_identity_parquet(
                catalogue,
                identity,
                run_id=run_id,
                batch_id=uuid4(),
            )
            self.assertIsNotNone(first_path)
            self.assertIsNotNone(second_path)
            visits = [uuid4(), uuid4()]
            documents = [uuid4(), uuid4(), uuid4()]
            contents = ["a" * 64, "a" * 64, "b" * 64]
            for index in range(3):
                connection.execute(
                    "INSERT INTO material.final_occurrences VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        uuid4(),
                        link_id,
                        visits[min(index, 1)],
                        documents[index],
                        contents[index],
                        index,
                        "/next",
                        f"2026-01-0{index + 1}T00:00:00Z",
                    ],
                )

            populate_final_links(
                catalogue,
                links_table="final_links",
                occurrences_table="final_occurrences",
                identities=(first_path, second_path),
            )

            rows = connection.execute(
                "SELECT count(*), min(first_seen_at), max(last_seen_at), "
                "max(visit_count), max(distinct_content_count), "
                "max(occurrence_count) FROM material.final_links"
            ).fetchone()
            self.assertEqual(rows[0], 1)
            self.assertEqual(rows[3:], (2, 2, 3))

            connection.execute(
                "INSERT INTO material.final_occurrences VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    uuid4(),
                    link_id,
                    uuid4(),
                    uuid4(),
                    "c" * 64,
                    3,
                    "/next",
                    "2026-01-04T00:00:00Z",
                ],
            )
            merge_live_links(
                catalogue,
                links_table="final_links",
                occurrences_table="final_occurrences",
                identities=first_path,
            )
            live_row = connection.execute(
                "SELECT visit_count, distinct_content_count, occurrence_count "
                "FROM material.final_links"
            ).fetchone()
            self.assertEqual(live_row, (3, 3, 4))

    def test_ducklake_marker_makes_commit_redelivery_a_noop(self) -> None:
        batch_id = uuid4()

        class MarkerCatalogue:
            def trusted_remote_rows(self, sql):
                self.sql = sql
                return [(3, 1024, 40, 8192)]

        catalogue = MarkerCatalogue()
        result = _applied_result(catalogue, batch_id)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.already_applied)
        self.assertEqual(result.source_items, 3)
        self.assertIn(str(batch_id), catalogue.sql)

    def test_only_transient_catalogue_errors_are_retryable(self) -> None:
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
                    'Cannot open file "/lake/material/data/file.parquet": '
                    "No such file or directory"
                )
            )
        )
        self.assertFalse(
            _is_retryable_batch_failure(
                duckdb.InvalidInputException("invalid projection")
            )
        )

    @patch("atlas.materialization.runtime.run_with_catalogue_retry")
    @patch("atlas.materialization.runtime.commit_prepared_batch")
    @patch("atlas.materialization.runtime.prepare_batch")
    @patch("atlas.materialization.runtime.catalogue_from_env")
    def test_transaction_retry_reuses_prepared_projection_and_files(
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
            pages=(),
            heads=(),
            files={},
            link_identities=None,
        )
        expected = BatchResult(
            source_items=2,
            source_bytes=100,
            output_rows=20,
            output_bytes=200,
            project_seconds=1,
            parquet_seconds=2,
            commit_seconds=3,
        )
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
        )
        self.assertEqual(commit_prepared_batch.call_count, 2)
        self.assertTrue(
            all(
                call.args == (catalogue, run, batch, prepared)
                for call in commit_prepared_batch.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
