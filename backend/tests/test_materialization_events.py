from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from nats.js.api import DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError, NotFoundError

from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.schemas import ViewMaterializationPut
from materialization.dematerialization import dematerialize_one
from materialization.executor import (
    _backing_view_sql,
    _refresh_materialization,
    _source_view,
)
from repository.catalogue.materializations import (
    DuckLakeTableIdentity,
    MaterializationError,
    MaterializationStore,
    physical_materialization_name,
)
from runtime.catalogue_events import (
    DDL_SUBJECT,
    DML_ALL_SUBJECT,
    EVENT_STREAM,
    CatalogueDDLEvent,
    CatalogueDMLTick,
    basin_ddl_durable,
    basin_ddl_subject,
    basin_dml_durable,
    basin_dml_subject,
    dml_subject,
    ensure_catalogue_event_stream,
    materialization_durable,
)


class MaterializationEventContractTests(unittest.TestCase):
    def test_dematerialization_recovers_unrecorded_private_table_identity(self) -> None:
        materialization_id = uuid4()
        reference = SimpleNamespace(
            ducklake_view_uuid=uuid4(),
            view_name="page_links",
        )
        model = SimpleNamespace(
            id=materialization_id,
            archived_at=None,
            desired_state="deleting",
            view_reference_id=uuid4(),
            source_sql="SELECT 1",
            source_view_uuid=uuid4(),
            name="page_links",
            ducklake_table_uuid=None,
            last_error="bootstrap interrupted",
        )
        session = MagicMock()
        session.get.side_effect = [model, reference]
        restored = SimpleNamespace(view_uuid=uuid4())
        view_store = MagicMock()
        view_store.name_for_uuid.return_value = "page_links"
        view_store.replace.return_value = restored
        target = DuckLakeTableIdentity(
            table_id=42,
            table_uuid=uuid4(),
            schema_name="_atlas_materializations",
            table_name=physical_materialization_name(materialization_id),
        )
        materialization_store = MagicMock()
        materialization_store.table_identity.return_value = target

        with (
            patch(
                "materialization.dematerialization.session_scope"
            ) as session_scope,
            patch(
                "materialization.dematerialization.CatalogueViewStore",
                return_value=view_store,
            ),
            patch(
                "materialization.dematerialization.MaterializationStore",
                return_value=materialization_store,
            ),
        ):
            session_scope.return_value.__enter__.return_value = session
            dematerialize_one(MagicMock(), materialization_id)

        materialization_store.drop.assert_called_once_with(
            name=physical_materialization_name(materialization_id),
            expected_uuid=target.table_uuid,
        )
        self.assertIsNotNone(model.archived_at)
        self.assertIsNone(model.last_error)

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

    def test_backing_view_targets_incarnation_private_table(self) -> None:
        catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="atlas", schema="main")
        )
        incarnation = UUID("25b2c3a6-2810-4089-8459-ae15c3d7bd6e")
        physical_name = physical_materialization_name(incarnation)
        self.assertEqual(
            physical_name,
            "m_25b2c3a6281040898459ae15c3d7bd6e",
        )
        self.assertEqual(
            _backing_view_sql(catalogue, physical_name),
            'SELECT * FROM "atlas"."_atlas_materializations".'
            '"m_25b2c3a6281040898459ae15c3d7bd6e"',
        )

    def test_refresh_targets_incarnation_private_table(self) -> None:
        materialization_id = uuid4()
        table_uuid = uuid4()
        model = SimpleNamespace(
            id=materialization_id,
            archived_at=None,
            desired_state="live",
            ducklake_table_uuid=table_uuid,
            refresh_strategy="full",
            source_sql="SELECT 1",
            processed_snapshot=None,
            last_refreshed_at=None,
            observed_state="live",
            last_error=None,
        )
        session = MagicMock()
        session.get.return_value = model
        store = MagicMock()

        with (
            patch("materialization.executor.session_scope") as session_scope,
            patch(
                "materialization.executor.MaterializationStore",
                return_value=store,
            ),
        ):
            session_scope.return_value.__enter__.return_value = session
            _refresh_materialization(
                MagicMock(),
                materialization_id,
                from_snapshot=10,
                processed_snapshot=12,
            )

        store.refresh_full.assert_called_once_with(
            name=physical_materialization_name(materialization_id),
            expected_uuid=table_uuid,
            sql="SELECT 1",
        )
        self.assertEqual(model.processed_snapshot, 12)

    def test_control_model_has_direct_refresh_state(self) -> None:
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

    def test_refresh_strategy_requires_the_right_key_shape(self) -> None:
        self.assertEqual(
            ViewMaterializationPut(
                name="links",
                source_table="crawls",
                refresh_strategy="keyed",
                key_columns=["crawl_id"],
            ).key_columns,
            ["crawl_id"],
        )
        self.assertEqual(
            ViewMaterializationPut(
                name="totals",
                source_table="crawls",
                refresh_strategy="full",
            ).key_columns,
            [],
        )
        with self.assertRaises(ValueError):
            ViewMaterializationPut(
                name="invalid",
                source_table="crawls",
                refresh_strategy="append",
                key_columns=[],
            )

    def test_subjects_and_message_ids_are_incarnation_stable(self) -> None:
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
            basin_dml_subject("atlas"), "basin.cdc.atlas.lake.dml_ticks"
        )
        self.assertEqual(
            basin_ddl_subject("atlas"), "basin.cdc.atlas.lake.ddl"
        )
        self.assertEqual(basin_dml_durable("atlas"), "atlas-basin-atlas-dml")
        self.assertEqual(basin_ddl_durable("atlas"), "atlas-basin-atlas-ddl")
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

    def test_incremental_sql_requires_direct_unshadowed_driver(self) -> None:
        catalogue = SimpleNamespace(
            config=SimpleNamespace(alias="atlas", schema="main")
        )
        store = MaterializationStore(catalogue)
        with self.assertRaisesRegex(
            MaterializationError,
            "must directly read its declared driving table",
        ):
            store._scope_incremental_query(
                "SELECT document_id FROM elements",
                source_table="documents",
                key_columns=("document_id",),
            )
        with self.assertRaisesRegex(MaterializationError, "may not shadow"):
            store._scope_incremental_query(
                "WITH documents AS (SELECT 1 AS document_id) "
                "SELECT document_id FROM documents",
                source_table="documents",
                key_columns=("document_id",),
            )

    def test_incremental_changes_use_native_ducklake_history(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.config.schema = "main"
        store = MaterializationStore(catalogue)
        store.table_identity_from_id = MagicMock(
            return_value=DuckLakeTableIdentity(
                table_id=7,
                table_uuid=uuid4(),
                schema_name="main",
                table_name="documents",
            )
        )

        store._prepare_changes(
            source_table_id=7,
            from_snapshot=10,
            to_snapshot=12,
            key_columns=("document_id",),
        )

        sql = catalogue.remote_execute.call_args_list[0].args[0]
        self.assertIn("ducklake_table_changes(", sql)
        self.assertNotIn("cdc_dml_changes_query", sql)


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
            patch("runtime.catalogue_events.get_float", return_value=3600),
        ):
            await ensure_catalogue_event_stream(JetStream())


if __name__ == "__main__":
    unittest.main()
