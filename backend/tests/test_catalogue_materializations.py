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

from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.service import (
    put_for_query,
    rebuild,
    request_dematerialization,
    summary,
)
from control.catalogue_queries.service import (
    CatalogueQueryConflictError,
    archive_query,
)
from materialization.commit import commit_scope
from materialization.compute import _write_bounded_arrow, compute_scope
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

    def test_attached_materialization_blocks_query_archive(self) -> None:
        query = SimpleNamespace(id=uuid4(), archived_at=None)
        session = MagicMock()
        session.scalar.return_value = SimpleNamespace(id=uuid4())

        with self.assertRaisesRegex(
            CatalogueQueryConflictError, "Dematerialize this query"
        ):
            archive_query(session, query)

        self.assertIsNone(query.archived_at)

    def test_collection_summary_exposes_compact_materialization_state(self) -> None:
        materialization_id = uuid4()
        model = SimpleNamespace(
            id=materialization_id,
            name="latest_documents",
            refresh_mode="full",
            dematerialization_requested_at=None,
            source_state="current",
        )
        store = MagicMock()
        store.inspect.return_value = SimpleNamespace(
            row_count=42,
            active_storage_bytes=1_024,
        )

        result = summary(
            store,
            model,
            active_query_revision=3,
            definition_is_current=False,
        )

        self.assertEqual(result.id, materialization_id)
        self.assertEqual(result.status, "full_refresh")
        self.assertEqual(result.row_count, 42)
        self.assertEqual(result.storage_bytes, 1_024)
        self.assertEqual(result.active_query_revision, 3)
        self.assertFalse(result.definition_is_current)

    def test_collection_summary_degrades_when_table_is_unavailable(self) -> None:
        model = SimpleNamespace(
            id=uuid4(),
            name="missing_table",
            refresh_mode="full",
            dematerialization_requested_at=None,
            source_state="current",
        )
        store = MagicMock()
        store.inspect.side_effect = MaterializationError("missing")

        result = summary(
            store,
            model,
            active_query_revision=None,
            definition_is_current=True,
        )

        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.row_count, 0)
        self.assertEqual(result.storage_bytes, 0)

    def test_query_put_returns_existing_materialization_idempotently(self) -> None:
        query_id = uuid4()
        existing = SimpleNamespace(id=uuid4())
        session = MagicMock()
        session.scalar.side_effect = [
            SimpleNamespace(id=query_id, current_revision_id=uuid4()),
            existing,
        ]
        store = MagicMock()
        expected = MagicMock()

        with patch(
            "control.catalogue_materializations.service.record",
            return_value=expected,
        ) as make_record:
            result = put_for_query(
                session,
                store,
                query_id=query_id,
                active_query_revision_id=None,
                name="document_links",
                display_name=None,
                description=None,
                refresh_mode="full",
                scope_kind=None,
                scope_column=None,
                live_enabled=False,
                backfill_enabled=False,
                backfill_scopes_per_minute=60,
                partition_column=None,
            )

        self.assertIs(result, expected)
        make_record.assert_called_once_with(session, store, existing)
        store.create.assert_not_called()

    def test_control_plane_enforces_one_materialization_per_definition(self) -> None:
        table = CatalogueMaterialization.__table__
        self.assertEqual(table.name, "catalogue_materializations")
        self.assertTrue(
            {
                "query_id",
                "view_reference_id",
                "active_query_revision_id",
                "bound_ducklake_view_uuid",
            }.issubset(table.columns.keys())
        )
        indexes = {index.name: index for index in table.indexes}
        self.assertTrue(indexes["uq_catalogue_materializations_active_name"].unique)
        self.assertTrue(indexes["uq_catalogue_materializations_active_query"].unique)
        self.assertTrue(indexes["uq_catalogue_materializations_active_view"].unique)
        foreign_keys = {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in table.foreign_key_constraints
        }
        self.assertEqual(
            foreign_keys["fk_catalogue_materializations_active_query_revision"],
            ("query_id", "active_query_revision_id"),
        )

    def test_paused_scope_job_is_rejected_before_evaluation(self) -> None:
        job = MaterializationScopeJob(
            materialization_id=uuid4(),
            definition_revision_id=uuid4(),
            query_revision_id=uuid4(),
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
            refresh_mode="scope_incremental",
            definition_revision_id=job.definition_revision_id,
            active_query_revision_id=job.query_revision_id,
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
        query_id = uuid4()
        view_reference_id = None
        table_uuid = uuid4()
        model = SimpleNamespace(
            id=uuid4(),
            name="document_links",
            query_id=query_id,
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
        self.assertEqual(model.query_id, query_id)
        self.assertIsNone(model.view_reference_id)
        store.drop_managed.assert_not_called()

    def test_incremental_rebuild_switches_revision_and_restarts_coverage(self) -> None:
        query_id = uuid4()
        old_revision_id = uuid4()
        target_revision_id = uuid4()
        old_definition_id = uuid4()
        table_uuid = uuid4()
        revision = SimpleNamespace(
            id=target_revision_id,
            query_id=query_id,
            sql="SELECT $document_id AS document_id",
        )
        model = SimpleNamespace(
            id=uuid4(),
            name="document_rows",
            query_id=query_id,
            active_query_revision_id=old_revision_id,
            view_reference_id=None,
            bound_ducklake_view_uuid=None,
            definition_revision_id=old_definition_id,
            refresh_mode="scope_incremental",
            scope_kind="document",
            scope_column="document_id",
            activation_snapshot=10,
            backfill_enabled=False,
            dematerialization_requested_at=None,
            source_state="current",
        )
        session = MagicMock()
        session.get.return_value = revision
        store = MagicMock()
        store.inspect.return_value = SimpleNamespace(table_uuid=table_uuid)
        store.catalogue.latest_snapshot.return_value = 42
        expected = MagicMock()

        with (
            patch(
                "control.catalogue_materializations.service._seed_scope",
                return_value="seed-document",
            ),
            patch(
                "control.catalogue_materializations.service.record",
                return_value=expected,
            ),
        ):
            result = rebuild(
                session,
                store,
                model,
                expected_uuid=table_uuid,
                target_query_revision_id=target_revision_id,
            )

        self.assertIs(result, expected)
        self.assertEqual(model.active_query_revision_id, target_revision_id)
        self.assertNotEqual(model.definition_revision_id, old_definition_id)
        self.assertEqual(model.activation_snapshot, 42)
        self.assertTrue(model.backfill_enabled)
        store.validate_scoped_schema.assert_called_once_with(
            name="document_rows",
            sql=revision.sql,
            parameters={"document_id": "seed-document"},
        )

    def test_rebuild_rejects_revision_from_another_query(self) -> None:
        model = SimpleNamespace(
            query_id=uuid4(),
            active_query_revision_id=uuid4(),
            dematerialization_requested_at=None,
            source_state="current",
        )
        session = MagicMock()
        session.get.return_value = SimpleNamespace(
            id=uuid4(), query_id=uuid4(), sql="SELECT 1"
        )

        with self.assertRaisesRegex(LookupError, "not found"):
            rebuild(
                session,
                MagicMock(),
                model,
                expected_uuid=uuid4(),
                target_query_revision_id=uuid4(),
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

    def test_create_refresh_and_drop_real_ducklake_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                store = MaterializationStore(catalogue)
                created = store.create(name="example", sql="SELECT 1 AS value")
                self.assertEqual(created.row_count, 1)
                self.assertEqual(created.columns[0][0], "value")

                refreshed = store.refresh(
                    name="example",
                    expected_uuid=created.table_uuid,
                    sql="SELECT * FROM range(3) AS values(value)",
                )
                self.assertEqual(refreshed.row_count, 3)
                self.assertNotEqual(refreshed.table_uuid, created.table_uuid)

                store.drop(name="example", expected_uuid=refreshed.table_uuid)
                self.assertEqual(catalogue.lake.table.list(schema_name="materialized"), [])

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
                    catalogue, view, scope_column="Any document key"
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
                    query_revision_id=uuid4(),
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

                @contextmanager
                def active_definition_scope():
                    session = SimpleNamespace(
                        scalar=lambda _statement: SimpleNamespace(
                            archived_at=None,
                            dematerialization_requested_at=None,
                            refresh_mode="scope_incremental",
                            source_state="current",
                            definition_revision_id=scope.definition_revision_id,
                            active_query_revision_id=scope.query_revision_id,
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
                    commit_scope(catalogue, job)
                    self.assertFalse(object_store.exists(key))
                    commit_scope(catalogue, job)

                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT document_id, element_index "
                        "FROM atlas.materialized.document_links"
                    ).fetchall(),
                    [("sha256:example", 7)],
                )
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT count(*) FROM atlas.main.materialization_scope_results"
                    ).fetchone()[0],
                    1,
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
                    query_revision_id=uuid4(),
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
                            refresh_mode="scope_incremental",
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
                        "SELECT count(*) FROM atlas.materialized.stale_target"
                    ).fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
