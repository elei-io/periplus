from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog
from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, NotFoundError

from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.schemas import ViewMaterializationPut
from repository.catalogue import Catalogue, CatalogueConfig
from repository.catalogue.materializations import (
    MaterializationAppendOnlyViolation,
    MaterializationStore,
)
from materialization.executor import _backing_view_sql, _source_view
from runtime.catalogue_events import (
    CatalogueDDLEvent,
    CatalogueDMLTick,
    DDL_SUBJECT,
    DML_ALL_SUBJECT,
    EVENT_STREAM,
    dml_subject,
    ensure_catalogue_event_stream,
    materialization_durable,
    relay_ddl_consumer,
    relay_dml_consumer,
)


class MaterializationEventContractTests(unittest.TestCase):
    def test_missing_source_view_is_recreated_from_durable_definition(self) -> None:
        reference = SimpleNamespace(
            ducklake_view_uuid=uuid4(),
            view_name="page_links",
        )
        model = SimpleNamespace(source_sql="SELECT 1", source_view_uuid=uuid4())
        restored = SimpleNamespace(view_uuid=uuid4())
        store = SimpleNamespace(
            get=lambda _view_uuid: None,
            list=lambda: [],
            create=lambda **_kwargs: restored,
        )

        self.assertIs(_source_view(store, reference, model), restored)
        self.assertEqual(reference.ducklake_view_uuid, restored.view_uuid)
        self.assertEqual(model.source_view_uuid, restored.view_uuid)

    def test_backing_view_is_a_select_over_the_physical_table(self) -> None:
        catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="atlas", schema="main")
        )

        self.assertEqual(
            _backing_view_sql(catalogue, "page_links"),
            'SELECT * FROM "atlas"."_atlas_materializations"."page_links"',
        )

    def test_control_model_has_refresh_strategy_without_scope_or_revision(self) -> None:
        columns = set(CatalogueMaterialization.__table__.columns.keys())
        self.assertTrue(
            {
                "source_table_uuid",
                "control_snapshot",
                "refresh_strategy",
                "key_columns",
                "desired_state",
                "observed_state",
                "nats_consumer_name",
                "processed_snapshot",
            }.issubset(columns)
        )
        self.assertFalse(
            {
                "definition_revision_id",
                "scope_kind",
                "scope_column",
                "activation_snapshot",
                "live_enabled",
                "backfill_enabled",
            }
            & columns
        )

    def test_refresh_strategy_requires_the_right_key_shape(self) -> None:
        keyed = ViewMaterializationPut(
            name="links",
            source_table="crawls",
            refresh_strategy="keyed",
            key_columns=["tenant_id", "crawl_id"],
        )
        self.assertEqual(keyed.key_columns, ["tenant_id", "crawl_id"])

        append = ViewMaterializationPut(
            name="events",
            source_table="crawl_steps",
            refresh_strategy="append",
            key_columns=["crawl_id", "step_index"],
        )
        self.assertEqual(append.refresh_strategy, "append")

        full = ViewMaterializationPut(
            name="totals",
            source_table="crawls",
            refresh_strategy="full",
        )
        self.assertEqual(full.key_columns, [])

        for strategy, key_columns in (
            ("keyed", []),
            ("append", []),
            ("full", ["crawl_id"]),
        ):
            with self.subTest(strategy=strategy), self.assertRaises(ValueError):
                ViewMaterializationPut(
                    name="invalid",
                    source_table="crawls",
                    refresh_strategy=strategy,
                    key_columns=key_columns,
                )

    def test_subjects_and_message_ids_are_physical_incarnation_stable(self) -> None:
        table_uuid = uuid4()
        tick = CatalogueDMLTick(
            table_id=7,
            table_uuid=table_uuid,
            schema_name="main",
            table_name="documents",
            snapshot_id=19,
            snapshot_time=None,
            schema_version=2,
        )
        self.assertEqual(dml_subject(table_uuid), f"atlas.catalogue.dml.{table_uuid.hex}")
        self.assertEqual(tick.message_id, f"dml:{table_uuid}:19")
        self.assertEqual(
            relay_dml_consumer(), "atlas-catalogue-dml-relay"
        )
        self.assertEqual(
            relay_ddl_consumer(), "atlas-catalogue-global-ddl-relay"
        )
        incarnation = uuid4()
        self.assertEqual(
            materialization_durable(incarnation),
            f"atlas-materialization-{incarnation.hex}",
        )
        ddl = CatalogueDDLEvent(
            event_kind="altered",
            object_kind="table",
            snapshot_id=20,
            snapshot_time=None,
            schema_id=1,
            schema_name="main",
            object_id=7,
            object_name="documents",
            details=None,
        )
        self.assertEqual(ddl.message_id, "ddl:20:table:7:altered")

    def test_full_refresh_retains_target_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            ) as catalogue:
                catalogue.bootstrap()
                catalogue.connection.execute(
                    "INSERT INTO documents BY NAME SELECT "
                    "'doc-1' AS document_id, 'h' AS html_sha256, "
                    "'key' AS html_object_key, 'text/html' AS html_content_type, "
                    "'utf-8' AS html_encoding, 1 AS html_size_bytes, "
                    "1 AS html_compressed_size_bytes, 'none' AS compression, "
                    "1 AS dom_schema_version, 'test' AS parser_name, "
                    "'1' AS parser_version, 'hash' AS parser_options_hash, "
                    "0 AS element_count, now() AS created_at"
                )
                store = MaterializationStore(catalogue)
                table, _snapshot = store.create_full(
                    name="document_ids",
                    sql="SELECT document_id FROM documents",
                )
                original_uuid = table.table_uuid
                catalogue.connection.execute(
                    "UPDATE documents SET document_id = 'doc-2' "
                    "WHERE document_id = 'doc-1'"
                )
                refreshed = store.refresh_full(
                    name="document_ids",
                    expected_uuid=original_uuid,
                    sql="SELECT document_id FROM documents",
                )
                self.assertEqual(refreshed.table_uuid, original_uuid)
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT document_id FROM _atlas_materializations.document_ids"
                    ).fetchone()[0],
                    "doc-2",
                )

    def test_composite_key_refresh_replaces_only_changed_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            ) as catalogue:
                catalogue.bootstrap()
                catalogue.connection.execute(
                    "CREATE TABLE keyed_source "
                    "(tenant_id INTEGER, document_id INTEGER, value VARCHAR)"
                )
                catalogue.connection.execute(
                    "INSERT INTO keyed_source VALUES "
                    "(1, 10, 'old-a'), (1, 11, 'old-b'), (2, 10, 'untouched')"
                )
                store = MaterializationStore(catalogue)
                table, source_snapshot = store.create_full(
                    name="keyed_result",
                    sql="SELECT * FROM keyed_source",
                )
                catalogue.connection.execute(
                    "UPDATE keyed_source SET value = 'new-a' "
                    "WHERE tenant_id = 1 AND document_id = 10"
                )
                catalogue.connection.execute(
                    "UPDATE keyed_source SET value = 'not-in-window' "
                    "WHERE tenant_id = 2 AND document_id = 10"
                )
                self._install_changes_macro(
                    catalogue,
                    columns="tenant_id INTEGER, document_id INTEGER",
                    values=f"({source_snapshot + 1}, 1, 'update_postimage', 1, 10)",
                )

                refreshed = store.refresh_keyed(
                    name="keyed_result",
                    expected_uuid=table.table_uuid,
                    sql="SELECT * FROM keyed_source",
                    source_table_id=store.table_identity("keyed_source").table_id,
                    from_snapshot=source_snapshot + 1,
                    to_snapshot=source_snapshot + 1,
                    key_columns=("tenant_id", "document_id"),
                )

                self.assertEqual(refreshed.table_uuid, table.table_uuid)
                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT * FROM _atlas_materializations.keyed_result "
                        "ORDER BY tenant_id, document_id"
                    ).fetchall(),
                    [
                        (1, 10, "new-a"),
                        (1, 11, "old-b"),
                        (2, 10, "untouched"),
                    ],
                )

    def test_append_refresh_is_idempotent_and_rejects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            ) as catalogue:
                catalogue.bootstrap()
                catalogue.connection.execute(
                    "CREATE TABLE append_source "
                    "(tenant_id INTEGER, event_id INTEGER, value VARCHAR)"
                )
                catalogue.connection.execute(
                    "INSERT INTO append_source VALUES (1, 10, 'first')"
                )
                store = MaterializationStore(catalogue)
                table, source_snapshot = store.create_full(
                    name="append_result",
                    sql="SELECT * FROM append_source",
                    append_key_columns=("tenant_id", "event_id"),
                )
                catalogue.connection.execute(
                    "INSERT INTO append_source VALUES (1, 11, 'second')"
                )
                self._install_changes_macro(
                    catalogue,
                    columns="tenant_id INTEGER, event_id INTEGER",
                    values=f"({source_snapshot + 1}, 1, 'insert', 1, 11)",
                )
                kwargs = {
                    "name": "append_result",
                    "expected_uuid": table.table_uuid,
                    "sql": "SELECT * FROM append_source",
                    "source_table_id": store.table_identity("append_source").table_id,
                    "from_snapshot": source_snapshot + 1,
                    "to_snapshot": source_snapshot + 1,
                    "key_columns": ("tenant_id", "event_id"),
                }

                store.refresh_append(**kwargs)
                store.refresh_append(**kwargs)

                self.assertEqual(
                    catalogue.connection.execute(
                        "SELECT * FROM _atlas_materializations.append_result "
                        "ORDER BY tenant_id, event_id"
                    ).fetchall(),
                    [(1, 10, "first"), (1, 11, "second")],
                )

                self._install_changes_macro(
                    catalogue,
                    columns="tenant_id INTEGER, event_id INTEGER",
                    values=f"({source_snapshot + 1}, 1, 'delete', 1, 11)",
                )
                with self.assertRaises(MaterializationAppendOnlyViolation):
                    store.refresh_append(**kwargs)

    @staticmethod
    def _install_changes_macro(
        catalogue: Catalogue, *, columns: str, values: str
    ) -> None:
        catalogue.connection.execute("DROP MACRO IF EXISTS cdc_dml_changes_query")
        catalogue.connection.execute("DROP TABLE IF EXISTS materialization_test_changes")
        catalogue.connection.execute(
            "CREATE TEMP TABLE materialization_test_changes "
            f"(snapshot_id BIGINT, rowid BIGINT, change_type VARCHAR, {columns})"
        )
        catalogue.connection.execute(
            f"INSERT INTO materialization_test_changes VALUES {values}"
        )
        catalogue.connection.execute(
            "CREATE TEMP MACRO cdc_dml_changes_query("
            "catalog_name, from_snapshot, to_snapshot, table_id := NULL"
            ") AS TABLE SELECT * FROM materialization_test_changes "
            "WHERE snapshot_id BETWEEN from_snapshot AND to_snapshot"
        )


class CatalogueEventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_stream_creation_attaches_and_validates(self) -> None:
        config = StreamConfig(
            name=EVENT_STREAM,
            subjects=[DML_ALL_SUBJECT, DDL_SUBJECT],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            num_replicas=1,
            max_age=3600,
            max_bytes=1024,
            discard=DiscardPolicy.OLD,
        )

        class JetStream:
            calls = 0

            async def stream_info(self, _name):
                self.calls += 1
                if self.calls == 1:
                    raise NotFoundError()
                return SimpleNamespace(config=config)

            async def add_stream(self, **_kwargs):
                raise BadRequestError()

        with (
            patch(
                "runtime.catalogue_events.get_int",
                side_effect=lambda name: {
                    "ATLAS_CATALOGUE_EVENT_STREAM_REPLICAS": 1,
                    "ATLAS_CATALOGUE_EVENT_MAX_BYTES": 1024,
                }[name],
            ),
            patch(
                "runtime.catalogue_events.get_float",
                return_value=3600,
            ),
        ):
            await ensure_catalogue_event_stream(JetStream())


if __name__ == "__main__":
    unittest.main()
