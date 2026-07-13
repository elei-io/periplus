from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog
import pyarrow as pa

from api.routers.graph_runs import _graph_run_materialization_lag_rows
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.service import (
    _pending_live_scopes,
    put_for_view,
    rebuild,
    request_dematerialization,
    summary,
)
from materialization.commit import commit_scope, commit_scope_batch
from materialization.compute import _write_bounded_arrow, compute_scope
from materialization.definitions import publish_scope
from materialization.fencing import StaleMaterializationJob
from materialization.queue import MaterializationCommitJob, MaterializationScopeJob
from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.materializations import (
    MaterializationError,
    MaterializationStore,
    scoped_view_query,
)
from repository.catalogue.views import CatalogueViewStore
from repository.objects.store import FileObjectStore


class CatalogueMaterializationTests(unittest.TestCase):
    def test_graph_run_lag_filters_active_materialization_revisions(self) -> None:
        first = (uuid4(), uuid4())
        second = (uuid4(), uuid4())
        expected = [(uuid4(), 2, 4, 1)]
        cursor = MagicMock()
        cursor.fetchall.return_value = expected
        connection = MagicMock()
        connection.execute.return_value = cursor
        catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="atlas", schema="main"),
            connection=connection,
        )

        result = _graph_run_materialization_lag_rows(
            catalogue, [first, second]
        )

        self.assertEqual(result, expected)
        sql, parameters = connection.execute.call_args.args
        self.assertIn("c.graph_run_id", sql)
        self.assertIn("m.status = 'planned'", sql)
        self.assertEqual(parameters, [*first, *second])

    def test_pending_live_scopes_are_attributed_to_current_definition(self) -> None:
        materialization_id = uuid4()
        revision_id = uuid4()
        cursor = MagicMock()
        cursor.fetchone.return_value = (7,)
        connection = MagicMock()
        connection.execute.return_value = cursor
        store = SimpleNamespace(
            catalogue=SimpleNamespace(
                config=SimpleNamespace(alias="atlas", schema="main"),
                connection=connection,
            )
        )

        result = _pending_live_scopes(
            store,
            SimpleNamespace(id=materialization_id, definition_revision_id=revision_id),
        )

        self.assertEqual(result, 7)
        sql, parameters = connection.execute.call_args.args
        self.assertIn("status = 'planned'", sql)
        self.assertEqual(parameters, [materialization_id, revision_id])

    def test_view_activation_keeps_public_name_and_hides_backing_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                view_store = CatalogueViewStore(catalogue)
                view = view_store.create(
                    name="documents_live",
                    sql="SELECT document_id FROM documents",
                )
                reference = SimpleNamespace(
                    id=uuid4(), ducklake_view_uuid=view.view_uuid, archived_at=None
                )
                session = MagicMock()
                session.scalar.side_effect = [reference, None]
                expected = MagicMock()
                with patch(
                    "control.catalogue_materializations.service._record",
                    return_value=expected,
                ):
                    result = put_for_view(
                        session,
                        MaterializationStore(catalogue),
                        view_reference_id=reference.id,
                        name="documents_live",
                        display_name="Documents live",
                        description=None,
                        scope_kind="document",
                        scope_column="document_id",
                        backfill_scopes_per_minute=60,
                        partition_column=None,
                    )

                self.assertIs(result, expected)
                model = session.add.call_args.args[0]
                self.assertEqual(model.source_sql, "SELECT document_id FROM documents")
                self.assertNotEqual(reference.ducklake_view_uuid, view.view_uuid)
                public_view = view_store.get(reference.ducklake_view_uuid)
                self.assertIn("_atlas_materializations", public_view.sql)
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT count(*) FROM views.documents_live"
                    ).fetchone()[0],
                    0,
                )

    def test_scope_publication_uses_the_frozen_operation_id(self) -> None:
        async def scenario() -> None:
            definition = SimpleNamespace(
                id=uuid4(),
                definition_revision_id=uuid4(),
                name="page_links",
                scope_kind="crawl",
                scope_column="crawl_id",
            )
            jetstream = MagicMock()
            jetstream.publish = unittest.mock.AsyncMock()

            await publish_scope(jetstream, definition, str(uuid4()), "live")

            published = jetstream.publish.await_args
            payload = MaterializationScopeJob.model_validate_json(published.args[1])
            self.assertEqual(
                published.kwargs["headers"]["Nats-Msg-Id"], payload.operation_id
            )

        import asyncio

        asyncio.run(scenario())

    def test_scoped_view_query_uses_crawl_parameter(self) -> None:
        view = SimpleNamespace(
            qualified_name="views.page_links",
            schema_name="views",
            view_name="page_links",
            columns=("crawl_id", "url"),
        )
        catalogue = SimpleNamespace(config=SimpleNamespace(alias="atlas"))
        sql = scoped_view_query(
            catalogue, view, scope_kind="crawl", scope_column="crawl_id"
        )
        self.assertIn('"crawl_id" = $crawl_id', sql)

    def test_metadata_lookup_uses_catalogue_boundary_not_physical_config(self) -> None:
        connection = MagicMock()
        cursors = [MagicMock(), MagicMock(), MagicMock()]
        cursors[0].fetchone.return_value = (uuid4(),)
        cursors[1].fetchone.return_value = (0, 0)
        cursors[2].fetchall.return_value = []
        connection.execute.side_effect = cursors
        table = MagicMock()
        table.info.return_value = SimpleNamespace(row_count=0, columns=[])
        catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="atlas"),
            metadata_schema="public",
            connection=connection,
            lake=SimpleNamespace(table=table),
        )

        MaterializationStore(catalogue).inspect("example")
        metadata_sql = connection.execute.call_args_list[0].args[0]
        self.assertIn('"__ducklake_metadata_atlas"."public"', metadata_sql)

    def test_collection_summary_exposes_compact_materialization_state(self) -> None:
        materialization_id = uuid4()
        model = SimpleNamespace(
            id=materialization_id,
            name="latest_documents",
            dematerialization_requested_at=None,
            source_state="current",
            source_sql="SELECT document_id FROM documents",
            live_enabled=True,
            backfill_enabled=False,
        )
        store = MagicMock()
        store.inspect.return_value = SimpleNamespace(
            row_count=42,
            active_storage_bytes=1_024,
        )

        with (
            patch(
                "control.catalogue_materializations.service._scope_progress",
                return_value=(1, 0, None, 1),
            ),
            patch(
                "control.catalogue_materializations.service._pending_live_scopes",
                return_value=3,
            ),
        ):
            result = summary(store, model, definition_is_current=False)

        self.assertEqual(result.id, materialization_id)
        self.assertEqual(result.status, "live")
        self.assertEqual(result.row_count, 42)
        self.assertEqual(result.storage_bytes, 1_024)
        self.assertFalse(result.definition_is_current)
        self.assertEqual(result.pending_live_scopes, 3)
        self.assertEqual(result.remaining_backfill_scopes, 0)

    def test_collection_summary_degrades_when_table_is_unavailable(self) -> None:
        model = SimpleNamespace(
            id=uuid4(),
            name="missing_table",
            dematerialization_requested_at=None,
            source_state="current",
            source_sql="SELECT document_id FROM documents",
        )
        store = MagicMock()
        store.inspect.side_effect = MaterializationError("missing")

        result = summary(store, model, definition_is_current=True)

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.row_count, 0)
        self.assertEqual(result.storage_bytes, 0)

    def test_control_plane_enforces_one_materialization_per_definition(self) -> None:
        table = CatalogueMaterialization.__table__
        self.assertEqual(table.name, "catalogue_materializations")
        self.assertTrue(
            {
                "view_reference_id",
                "bound_ducklake_view_uuid",
                "scope_kind",
                "scope_column",
            }.issubset(table.columns.keys())
        )
        indexes = {index.name: index for index in table.indexes}
        self.assertTrue(indexes["uq_catalogue_materializations_active_name"].unique)
        self.assertTrue(indexes["uq_catalogue_materializations_active_view"].unique)

    def test_paused_scope_job_is_rejected_before_evaluation(self) -> None:
        job = MaterializationScopeJob(
            materialization_id=uuid4(),
            definition_revision_id=uuid4(),
            target_table="document_links",
            scope_kind="document",
            scope_column="document_id",
            scope_id="sha256:paused",
            operation_id="c" * 64,
            source="live",
            enqueued_at=datetime.now(UTC),
        )
        paused = SimpleNamespace(
            archived_at=None,
            dematerialization_requested_at=None,
            source_state="current",
            definition_revision_id=job.definition_revision_id,
            scope_kind=job.scope_kind,
            scope_column=job.scope_column,
            name=job.target_table,
            live_enabled=False,
            backfill_enabled=True,
        )

        @contextmanager
        def paused_definition_scope():
            yield SimpleNamespace(get=lambda _model, _identity: paused)

        with (
            patch("materialization.compute.session_scope", paused_definition_scope),
            patch("materialization.compute.catalogue_from_env") as catalogue,
        ):
            with self.assertRaises(StaleMaterializationJob):
                compute_scope(job)
        catalogue.assert_not_called()

    def test_dematerialization_request_stops_work_without_removing_source(self) -> None:
        view_reference_id = uuid4()
        table_uuid = uuid4()
        model = SimpleNamespace(
            id=uuid4(),
            name="document_links",
            view_reference_id=view_reference_id,
            ducklake_table_uuid=table_uuid,
            live_enabled=True,
            backfill_enabled=True,
            dematerialization_requested_at=None,
        )
        session = MagicMock()
        store = MagicMock()
        store.inspect.return_value = SimpleNamespace(table_uuid=table_uuid)
        expected = MagicMock()

        with patch(
            "control.catalogue_materializations.service.record",
            return_value=expected,
        ):
            result = request_dematerialization(
                session,
                store,
                model,
                expected_uuid=table_uuid,
            )

        self.assertIs(result, expected)
        self.assertFalse(model.live_enabled)
        self.assertFalse(model.backfill_enabled)
        self.assertIsNotNone(model.dematerialization_requested_at)
        self.assertEqual(model.view_reference_id, view_reference_id)
        store.drop_managed.assert_not_called()

    def test_rebuild_rebinds_view_and_restarts_coverage(self) -> None:
        view_reference_id = uuid4()
        old_view_uuid = uuid4()
        new_view_uuid = uuid4()
        old_definition_id = uuid4()
        table_uuid = uuid4()
        reference = SimpleNamespace(id=view_reference_id, ducklake_view_uuid=new_view_uuid)
        view = SimpleNamespace(view_uuid=new_view_uuid, schema_name="views", view_name="document_rows", columns=("document_id",), qualified_name="views.document_rows")
        model = SimpleNamespace(
            id=uuid4(),
            name="document_rows",
            view_reference_id=view_reference_id,
            bound_ducklake_view_uuid=old_view_uuid,
            definition_revision_id=old_definition_id,
            scope_kind="document",
            scope_column="document_id",
            activation_snapshot=10,
            source_sql="SELECT document_id FROM documents",
            backfill_enabled=False,
            dematerialization_requested_at=None,
            source_state="current",
        )
        session = MagicMock()
        session.get.return_value = reference
        store = MagicMock()
        store.inspect.return_value = SimpleNamespace(table_uuid=table_uuid)
        store.catalogue.latest_snapshot.return_value = 42
        expected = MagicMock()

        with (
            patch(
                "control.catalogue_materializations.service._seed_scope",
                return_value="seed-document",
            ),
            patch("control.catalogue_materializations.service.record", return_value=expected),
            patch("control.catalogue_materializations.service.CatalogueViewStore.get", return_value=view),
        ):
            result = rebuild(
                session,
                store,
                model,
                expected_uuid=table_uuid,
            )

        self.assertIs(result, expected)
        self.assertEqual(model.bound_ducklake_view_uuid, new_view_uuid)
        self.assertNotEqual(model.definition_revision_id, old_definition_id)
        self.assertEqual(model.activation_snapshot, 42)
        self.assertTrue(model.backfill_enabled)
        store.validate_scoped_schema.assert_called_once_with(
            name="document_rows",
            sql=unittest.mock.ANY,
            parameters={"document_id": "seed-document"},
        )

    def test_arrow_writer_enforces_limits_before_collecting_the_result(self) -> None:
        batch = pa.record_batch({"value": [1, 2, 3]})
        reader = pa.RecordBatchReader.from_batches(batch.schema, [batch])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bounded.arrow"
            with self.assertRaisesRegex(RuntimeError, "2 row limit"):
                _write_bounded_arrow(
                    reader,
                    path,
                    maximum_rows=2,
                    maximum_bytes=1024,
                )

    def test_incremental_rebuild_schema_check_uses_parameterized_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                store = MaterializationStore(catalogue)
                created = store.create_empty_scoped(
                    name="scoped_rows",
                    sql="SELECT CAST($document_id AS VARCHAR) AS document_id",
                    parameters={"document_id": "seed"},
                )

                store.validate_scoped_schema(
                    name="scoped_rows",
                    sql="SELECT CAST($document_id AS VARCHAR) AS document_id",
                    parameters={"document_id": "another-document"},
                )
                with self.assertRaisesRegex(MaterializationError, "changes the durable schema"):
                    store.validate_scoped_schema(
                        name="scoped_rows",
                        sql="SELECT CAST($document_id AS VARCHAR) AS changed_name",
                        parameters={"document_id": "another-document"},
                    )

                store.drop(name="scoped_rows", expected_uuid=created.table_uuid)

    def test_view_output_can_be_scoped_by_any_selected_column_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                view = CatalogueViewStore(catalogue).create(
                    name="aliased_documents",
                    sql=(
                        "SELECT * FROM (VALUES ('first', 1), ('second', 2)) "
                        'AS source("Any document key", value)'
                    ),
                )
                sql = scoped_view_query(
                    catalogue, view, scope_kind="document", scope_column="Any document key"
                )
                rows = catalogue.connection.execute(
                    sql, {"document_id": "second"}
                ).fetchall()
                self.assertEqual(rows, [("second", 2)])

                table = MaterializationStore(catalogue).create_empty_scoped(
                    name="aliased_documents",
                    sql=sql,
                    parameters={"document_id": "first"},
                )
                self.assertEqual(
                    [column[0] for column in table.columns],
                    ["Any document key", "value"],
                )

    def test_scoped_commit_is_atomic_and_idempotent_without_staging_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                MaterializationStore(catalogue).create_empty_scoped(
                    name="document_links",
                    sql=(
                        "SELECT CAST($document_id AS VARCHAR) AS document_id, "
                        "CAST(1 AS BIGINT) AS element_index"
                    ),
                    parameters={"document_id": "seed"},
                )
                table = pa.table(
                    {"document_id": ["sha256:example"], "element_index": [7]}
                )
                staging = root / "objects"
                key = "staging/materializations/operation.arrow"
                path = root / "operation.arrow"
                path.parent.mkdir(parents=True, exist_ok=True)
                with pa.OSFile(str(path), "wb") as sink:
                    with pa.ipc.new_file(sink, table.schema) as writer:
                        writer.write_table(table)
                now = datetime.now(UTC)
                scope = MaterializationScopeJob(
                    materialization_id=uuid4(),
                    definition_revision_id=uuid4(),
                    target_table="document_links",
                    scope_kind="document",
                    scope_column="document_id",
                    scope_id="sha256:example",
                    operation_id="a" * 64,
                    source="backfill",
                    enqueued_at=now,
                )
                job = MaterializationCommitJob(
                    scope=scope,
                    staging_key=key,
                    staging_sha256=sha256(path.read_bytes()).hexdigest(),
                    row_count=1,
                    output_bytes=table.nbytes,
                    file_bytes=path.stat().st_size,
                    started_at=now,
                    completed_at=now,
                )
                object_store = FileObjectStore(staging)
                with path.open("rb") as content:
                    object_store.put_if_absent(key, content)
                second_table = pa.table(
                    {"document_id": ["sha256:second"], "element_index": [8]}
                )
                second_path = root / "operation-second.arrow"
                with pa.OSFile(str(second_path), "wb") as sink:
                    with pa.ipc.new_file(sink, second_table.schema) as writer:
                        writer.write_table(second_table)
                second_scope = scope.model_copy(
                    update={"scope_id": "sha256:second", "operation_id": "c" * 64}
                )
                second_key = "staging/materializations/operation-second.arrow"
                second_job = MaterializationCommitJob(
                    scope=second_scope,
                    staging_key=second_key,
                    staging_sha256=sha256(second_path.read_bytes()).hexdigest(),
                    row_count=1,
                    output_bytes=second_table.nbytes,
                    file_bytes=second_path.stat().st_size,
                    started_at=now,
                    completed_at=now,
                )
                with second_path.open("rb") as content:
                    object_store.put_if_absent(second_key, content)

                @contextmanager
                def active_definition_scope():
                    session = SimpleNamespace(
                        scalar=lambda _statement: SimpleNamespace(
                            archived_at=None,
                            dematerialization_requested_at=None,
                            source_state="current",
                            definition_revision_id=scope.definition_revision_id,
                            scope_kind=scope.scope_kind,
                            scope_column=scope.scope_column,
                            name=scope.target_table,
                            live_enabled=False,
                            backfill_enabled=True,
                            partition_column=None,
                        )
                    )
                    yield session

                with (
                    patch(
                        "materialization.commit.object_store_from_env",
                        return_value=object_store,
                    ),
                    patch(
                        "materialization.commit.session_scope",
                        active_definition_scope,
                    ),
                    patch(
                        "materialization.commit.staging_root_from_env",
                        return_value=root / "temporary",
                    ),
                ):
                    outcomes = commit_scope_batch(catalogue, [job, second_job])
                    self.assertEqual(set(outcomes.values()), {"committed"})
                    self.assertFalse(object_store.exists(key))
                    self.assertFalse(object_store.exists(second_key))
                    commit_scope(catalogue, job)

                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT document_id, element_index "
                        "FROM atlas._atlas_materializations.document_links"
                    ).fetchall(),
                    [("sha256:example", 7), ("sha256:second", 8)],
                )
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT count(*) FROM atlas.main.materialization_scope_results"
                    ).fetchone()[0],
                    2,
                )

    def test_stale_definition_cannot_commit_and_discards_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                MaterializationStore(catalogue).create_empty_scoped(
                    name="stale_target",
                    sql="SELECT CAST($document_id AS VARCHAR) AS document_id",
                    parameters={"document_id": "seed"},
                )
                table = pa.table({"document_id": ["sha256:stale"]})
                arrow = root / "stale.arrow"
                with pa.OSFile(str(arrow), "wb") as sink:
                    with pa.ipc.new_file(sink, table.schema) as writer:
                        writer.write_table(table)
                store = FileObjectStore(root / "objects")
                key = "staging/materializations/stale.arrow"
                with arrow.open("rb") as content:
                    store.put_if_absent(key, content)
                scope = MaterializationScopeJob(
                    materialization_id=uuid4(),
                    definition_revision_id=uuid4(),
                    target_table="stale_target",
                    scope_kind="document",
                    scope_column="document_id",
                    scope_id="sha256:stale",
                    operation_id="b" * 64,
                    source="live",
                    enqueued_at=datetime.now(UTC),
                )
                job = MaterializationCommitJob(
                    scope=scope,
                    staging_key=key,
                    staging_sha256=sha256(arrow.read_bytes()).hexdigest(),
                    row_count=1,
                    output_bytes=table.nbytes,
                    file_bytes=arrow.stat().st_size,
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                )

                @contextmanager
                def stale_definition_scope():
                    yield SimpleNamespace(
                        scalar=lambda _statement: SimpleNamespace(
                            archived_at=None,
                            dematerialization_requested_at=None,
                            source_state="current",
                            live_enabled=True,
                            definition_revision_id=uuid4(),
                        )
                    )

                with (
                    patch(
                        "materialization.commit.object_store_from_env",
                        return_value=store,
                    ),
                    patch(
                        "materialization.commit.session_scope",
                        stale_definition_scope,
                    ),
                ):
                    self.assertEqual(commit_scope(catalogue, job), "stale")
                self.assertFalse(store.exists(key))
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT count(*) FROM atlas._atlas_materializations.stale_target"
                    ).fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
