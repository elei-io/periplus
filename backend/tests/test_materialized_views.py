from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog
import pyarrow as pa

from materialization.commit import commit_scope
from materialization.compute import _write_bounded_arrow
from materialization.queue import MaterializationCommitJob, MaterializationScopeJob
from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.materialized_views import MaterializedViewStore
from repository.objects.store import FileObjectStore


class MaterializedViewStoreTests(unittest.TestCase):
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
                store = MaterializedViewStore(catalogue)
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

    def test_scoped_commit_is_atomic_and_idempotent_without_staging_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
            with Catalogue(config) as catalogue:
                catalogue.bootstrap()
                MaterializedViewStore(catalogue).create_empty_scoped(
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
                    materialized_view_id=uuid4(),
                    definition_revision_id=uuid4(),
                    query_revision_id=uuid4(),
                    target_table="document_links",
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
                            deletion_requested_at=None,
                            refresh_mode="scope_incremental",
                            definition_revision_id=scope.definition_revision_id,
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
                MaterializedViewStore(catalogue).create_empty_scoped(
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
                    materialized_view_id=uuid4(),
                    definition_revision_id=uuid4(),
                    query_revision_id=uuid4(),
                    target_table="stale_target",
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
                            deletion_requested_at=None,
                            refresh_mode="scope_incremental",
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
