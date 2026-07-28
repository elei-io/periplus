from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import duckdb

from atlas.materialization.cdc.relay import (
    BasinDDLEvent,
    RelayedTable,
    TableResolver,
    _publish_ddl_message,
    _publish_dml_message,
    _retry_transient_source_operation,
    load_relayed_tables,
)


def _message(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        data=json.dumps(payload).encode(),
        ack=AsyncMock(),
    )


def _table(table_id: int, *, name: str = "documents") -> RelayedTable:
    return RelayedTable(
        table_id=table_id,
        table_uuid=uuid4(),
        schema_name="main",
        table_name=name,
    )


class CDCRelayTests(unittest.IsolatedAsyncioTestCase):
    def test_table_index_uses_public_ducklake_metadata(self) -> None:
        table_uuid = uuid4()
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.trusted_remote_rows.return_value = [
            (7, table_uuid, "main", "documents", 1)
        ]

        tables = load_relayed_tables(catalogue)

        self.assertEqual(
            tables,
            {
                7: RelayedTable(
                    table_id=7,
                    table_uuid=table_uuid,
                    schema_name="main",
                    table_name="documents",
                )
            },
        )
        sql = catalogue.trusted_remote_rows.call_args.args[0]
        self.assertIn("ducklake_table_info('atlas')", sql)
        self.assertNotIn("__ducklake_metadata", sql)
        self.assertIn("table_type = 'BASE TABLE'", sql)

    def test_ambiguous_cross_schema_table_names_are_rejected(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas"
        catalogue.trusted_remote_rows.return_value = [
            (7, uuid4(), "main", "documents", 2)
        ]

        with self.assertRaisesRegex(RuntimeError, "unique across schemas"):
            load_relayed_tables(catalogue)

    async def test_global_tick_fans_out_and_then_acks_source(self) -> None:
        first = _table(7)
        second = _table(8, name="urls")
        resolver = MagicMock()
        resolver.resolve.return_value = {
            first.table_id: first,
            second.table_id: second,
        }
        message = _message(
            {
                "consumer_name": "basin",
                "start_snapshot": 10,
                "end_snapshot": 10,
                "snapshot_id": 10,
                "snapshot_time": None,
                "schema_version": 1,
                "table_ids": [first.table_id, second.table_id],
            }
        )
        jetstream = MagicMock()
        jetstream.publish = AsyncMock()

        await _publish_dml_message(jetstream, resolver, message)

        self.assertEqual(jetstream.publish.await_count, 2)
        relayed = [
            json.loads(call.args[1])
            for call in jetstream.publish.await_args_list
        ]
        self.assertEqual(
            {
                (
                    event["start_snapshot"],
                    event["snapshot_id"],
                    event["end_snapshot"],
                )
                for event in relayed
            },
            {(10, 10, 10)},
        )
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
        message.ack.assert_awaited_once_with()

    async def test_publish_failure_does_not_ack_source(self) -> None:
        first = _table(7)
        resolver = MagicMock()
        resolver.resolve.return_value = {first.table_id: first}
        message = _message(
            {
                "start_snapshot": 10,
                "end_snapshot": 10,
                "snapshot_id": 10,
                "snapshot_time": None,
                "schema_version": 1,
                "table_ids": [first.table_id],
            }
        )
        jetstream = MagicMock()
        jetstream.publish = AsyncMock(
            side_effect=RuntimeError("JetStream unavailable")
        )

        with self.assertRaisesRegex(RuntimeError, "JetStream unavailable"):
            await _publish_dml_message(jetstream, resolver, message)

        message.ack.assert_not_awaited()

    async def test_ddl_is_republished_before_source_ack(self) -> None:
        resolver = MagicMock()
        message = _message(
            {
                "consumer_name": "basin",
                "start_snapshot": 2,
                "end_snapshot": 2,
                "snapshot_id": 2,
                "snapshot_time": "2026-07-23 13:15:32.529749+00:00",
                "event_kind": "created",
                "object_kind": "table",
                "schema_id": 0,
                "schema_name": "main",
                "object_id": 7,
                "object_name": "documents",
                "details": "{}",
            }
        )
        jetstream = MagicMock()
        jetstream.publish = AsyncMock()

        await _publish_ddl_message(jetstream, resolver, message)

        resolver.note_ddl.assert_called_once()
        relayed = resolver.note_ddl.call_args.args[0]
        self.assertIsInstance(relayed, BasinDDLEvent)
        self.assertEqual(relayed.object_name, "documents")
        self.assertEqual(
            jetstream.publish.call_args.kwargs["headers"]["Nats-Msg-Id"],
            "ddl:2:table:7:created",
        )
        message.ack.assert_awaited_once_with()

    def test_retired_tables_do_not_block_historical_dml_replay(self) -> None:
        resolver = TableResolver(MagicMock())
        resolver.retired_table_ids.add(7)
        resolver._tables = {}

        self.assertEqual(resolver.resolve({7}), {})

    @patch("atlas.materialization.cdc.relay.load_relayed_tables", return_value={})
    def test_absent_table_is_retired_during_historical_replay(
        self,
        load_tables: MagicMock,
    ) -> None:
        resolver = TableResolver(MagicMock())

        self.assertEqual(resolver.resolve({22}), {})
        self.assertEqual(resolver.retired_table_ids, {22})
        load_tables.assert_called_once()

    async def test_transient_remote_failure_keeps_source_delivery_alive(self) -> None:
        operation = AsyncMock(
            side_effect=[duckdb.IOException("Quack unavailable"), "resolved"]
        )
        message = SimpleNamespace(in_progress=AsyncMock())
        on_retry = MagicMock()

        with patch(
            "atlas.materialization.cdc.relay.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await _retry_transient_source_operation(
                operation,
                message=message,
                operation_name="resolve table identities",
                on_retry=on_retry,
            )

        self.assertEqual(result, "resolved")
        self.assertEqual(operation.await_count, 2)
        message.in_progress.assert_awaited_once_with()
        on_retry.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
