from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from ducklake_client import DiskStorage, DuckDBCatalog

from catalogue_relay.executor import (
    RelayedTable,
    _open_ddl,
    _open_dml,
    _publish_dml,
    _reconcile,
    load_relayed_tables,
)
from repository.catalogue import Catalogue, CatalogueConfig
from runtime.catalogue_events import relay_ddl_consumer, relay_dml_consumer


def _table(
    table_id: int,
    *,
    name: str = "documents",
    live: bool = True,
) -> RelayedTable:
    return RelayedTable(
        table_id=table_id,
        table_uuid=uuid4(),
        schema_name="main",
        table_name=name,
        is_live=live,
    )


class CatalogueRelayTests(unittest.IsolatedAsyncioTestCase):
    def test_table_index_reads_live_physical_identities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            ) as catalogue:
                catalogue.bootstrap()
                tables = load_relayed_tables(catalogue)

        documents = next(
            table for table in tables.values() if table.table_name == "documents"
        )
        self.assertTrue(documents.is_live)
        self.assertEqual(documents.schema_name, "main")

    def test_relay_opens_one_global_dml_and_one_global_ddl_consumer(self) -> None:
        catalogue = MagicMock()

        with (
            patch("catalogue_relay.executor.DMLConsumer") as dml,
            patch("catalogue_relay.executor.DDLConsumer") as ddl,
        ):
            _open_dml(catalogue)
            _open_ddl(catalogue)

        self.assertEqual(
            dml.call_args.args[1],
            relay_dml_consumer(),
        )
        self.assertEqual(dml.call_args.kwargs["mode"], "ticks")
        self.assertEqual(
            dml.call_args.kwargs["connection"],
            catalogue.connection,
        )
        self.assertNotIn("table", dml.call_args.kwargs)
        self.assertNotIn("table_id", dml.call_args.kwargs)
        self.assertEqual(ddl.call_args.args[1], relay_ddl_consumer())
        self.assertEqual(ddl.call_args.kwargs["mode"], "changes")
        self.assertNotIn("schemas", ddl.call_args.kwargs)
        self.assertNotIn("connection", ddl.call_args.kwargs)

    async def test_reconcile_updates_identity_metadata_without_opening_consumers(
        self,
    ) -> None:
        previous = _table(7)
        renamed = RelayedTable(
            table_id=previous.table_id,
            table_uuid=previous.table_uuid,
            schema_name=previous.schema_name,
            table_name="renamed_documents",
            is_live=True,
        )

        with patch(
            "catalogue_relay.executor.load_relayed_tables",
            return_value={renamed.table_id: renamed},
        ):
            reconciled = await _reconcile(
                MagicMock(),
                {previous.table_id: previous},
            )

        self.assertEqual(reconciled, {renamed.table_id: renamed})

    async def test_global_tick_fans_out_one_event_per_touched_table(self) -> None:
        first = _table(7)
        second = _table(8, name="urls")
        batch = MagicMock()
        batch.ticks = [
            SimpleNamespace(
                snapshot_id=10,
                snapshot_time=None,
                schema_version=1,
                table_ids=(first.table_id, second.table_id),
            )
        ]
        consumer = MagicMock()
        consumer.read.return_value = batch
        jetstream = MagicMock()
        jetstream.publish = AsyncMock()

        worked, tables = await _publish_dml(
            jetstream,
            MagicMock(),
            {first.table_id: first, second.table_id: second},
            consumer,
        )

        self.assertTrue(worked)
        self.assertEqual(set(tables), {7, 8})
        self.assertEqual(jetstream.publish.await_count, 2)
        self.assertEqual(
            {
                call.kwargs["headers"]["Nats-Msg-Id"]
                for call in jetstream.publish.await_args_list
            },
            {
                f"dml:{first.table_uuid}:10",
                f"dml:{second.table_uuid}:10",
            },
        )
        batch.commit.assert_called_once_with()

    async def test_publish_failure_does_not_advance_the_global_cursor(self) -> None:
        first = _table(7)
        second = _table(8, name="urls")
        batch = MagicMock()
        batch.ticks = [
            SimpleNamespace(
                snapshot_id=10,
                snapshot_time=None,
                schema_version=1,
                table_ids=(first.table_id, second.table_id),
            )
        ]
        consumer = MagicMock()
        consumer.read.return_value = batch
        jetstream = MagicMock()
        jetstream.publish = AsyncMock(
            side_effect=[None, RuntimeError("JetStream unavailable")]
        )

        with self.assertRaisesRegex(RuntimeError, "JetStream unavailable"):
            await _publish_dml(
                jetstream,
                MagicMock(),
                {first.table_id: first, second.table_id: second},
                consumer,
            )

        batch.commit.assert_not_called()

    async def test_unknown_table_refreshes_identity_index_before_publish(
        self,
    ) -> None:
        table = _table(7)
        batch = MagicMock()
        batch.ticks = [
            SimpleNamespace(
                snapshot_id=10,
                snapshot_time=None,
                schema_version=1,
                table_ids=(table.table_id,),
            )
        ]
        consumer = MagicMock()
        consumer.read.return_value = batch
        jetstream = MagicMock()
        jetstream.publish = AsyncMock()

        with patch(
            "catalogue_relay.executor.load_relayed_tables",
            return_value={table.table_id: table},
        ):
            worked, tables = await _publish_dml(
                jetstream,
                MagicMock(),
                {},
                consumer,
            )

        self.assertTrue(worked)
        self.assertEqual(tables, {table.table_id: table})
        batch.commit.assert_called_once_with()

    async def test_replayed_batch_uses_the_same_deterministic_message_id(
        self,
    ) -> None:
        table = _table(7)
        batch = MagicMock()
        batch.ticks = [
            SimpleNamespace(
                snapshot_id=10,
                snapshot_time=None,
                schema_version=1,
                table_ids=(table.table_id,),
            )
        ]
        consumer = MagicMock()
        consumer.read.return_value = batch
        jetstream = MagicMock()
        jetstream.publish = AsyncMock()
        tables = {table.table_id: table}

        await _publish_dml(jetstream, MagicMock(), tables, consumer)
        await _publish_dml(jetstream, MagicMock(), tables, consumer)

        self.assertEqual(
            [
                call.kwargs["headers"]["Nats-Msg-Id"]
                for call in jetstream.publish.await_args_list
            ],
            [f"dml:{table.table_uuid}:10", f"dml:{table.table_uuid}:10"],
        )
        self.assertEqual(batch.commit.call_count, 2)


if __name__ == "__main__":
    unittest.main()
