from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import UUID

from repository.catalogue.quack import QuackConnection, render_bound_sql


class QuackSQLBindingTests(unittest.TestCase):
    def test_values_are_bound_as_typed_ast_literals(self) -> None:
        rendered = render_bound_sql(
            "SELECT * FROM x WHERE text = ? AND id = ? AND captured_at = ?",
            [
                "value'); DROP TABLE x; --",
                UUID(int=1),
                datetime(2026, 7, 12, tzinfo=UTC),
            ],
        )

        self.assertIn("'value''); DROP TABLE x; --'", rendered)
        self.assertIn("AS UUID", rendered)
        self.assertIn("AS TIMESTAMPTZ", rendered)

    def test_named_parameters_and_lists_are_supported(self) -> None:
        rendered = render_bound_sql(
            "SELECT * FROM read_parquet($paths) WHERE value = $value",
            {"paths": ["s3://bucket/a", "s3://bucket/b"], "value": 2},
        )

        self.assertIn("['s3://bucket/a', 's3://bucket/b']", rendered)
        self.assertIn("value = 2", rendered)

    def test_call_templates_bind_without_parsing_application_values(self) -> None:
        rendered = render_bound_sql(
            "CALL lake.set_commit_message(?, ?)",
            ["Atlas", "it's safe"],
        )
        self.assertEqual(
            rendered,
            "CALL lake.set_commit_message('Atlas', 'it''s safe')",
        )

    def test_duckdb_time_travel_is_bound_before_ast_validation(self) -> None:
        rendered = render_bound_sql(
            "SELECT * FROM atlas.main.documents AT (VERSION => ?) "
            "WHERE document_id = ?",
            [42, "sha256:example"],
        )

        self.assertIn("AT (VERSION => 42)", rendered)
        self.assertIn("document_id = 'sha256:example'", rendered)

    def test_ducklake_partition_ddl_is_preserved_for_the_server_parser(self) -> None:
        sql = (
            'ALTER TABLE "atlas"."materialized"."links" SET PARTITIONED BY '
            '(year("created_at"), month("created_at"), day("created_at"))'
        )
        self.assertEqual(render_bound_sql(sql, None), sql)


class QuackTransactionTests(unittest.TestCase):
    @patch("repository.catalogue.quack.duckdb.connect")
    def test_writes_are_sent_in_one_transaction_and_snapshot_is_connection_local(
        self, connect: Mock
    ) -> None:
        raw = connect.return_value
        raw.execute.return_value = raw
        raw.fetchall.return_value = []
        raw.fetchone.return_value = (42,)
        connection = QuackConnection(
            uri="quack:state",
            token="token",
            disable_ssl=True,
            catalogue_alias="atlas",
            stage_path=Mock(side_effect=lambda path: f"s3://bucket/{path.name}"),
            cleanup_staging=Mock(),
        )
        raw.execute.reset_mock()

        with connection.transaction():
            connection.execute("INSERT INTO atlas.main.t VALUES (?)", [1])
            connection.execute("DELETE FROM atlas.main.t WHERE id = ?", [2])

        first_sql, first_parameters = raw.execute.call_args_list[0].args
        script = first_parameters[0]
        self.assertIn("BEGIN", script)
        self.assertIn("INSERT INTO atlas.main.t VALUES (1)", script)
        self.assertIn("DELETE FROM atlas.main.t WHERE id = 2", script)
        self.assertIn("COMMIT", script)
        self.assertEqual(connection.last_committed_snapshot, 42)
        self.assertEqual(raw.execute.call_count, 2)


if __name__ == "__main__":
    unittest.main()
