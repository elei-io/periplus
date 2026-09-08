from unittest import TestCase
from unittest.mock import MagicMock, patch

from periplus.platform.catalogue.cdc_extension import load_cdc_extension


class CommunityCdcTests(TestCase):
    def test_loads_expected_community_revision(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (
            "ducklake_cdc 0.6.3", "f909296",
        )
        load_cdc_extension(connection)
        self.assertEqual(
            [call.args[0] for call in connection.execute.call_args_list],
            ["INSTALL ducklake_cdc FROM community", "LOAD ducklake_cdc",
             "SELECT cdc_version(), cdc_build_revision()"],
        )

    def test_rejects_changed_community_source(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (
            "ducklake_cdc 0.6.3", "unexpected",
        )
        with self.assertRaisesRegex(RuntimeError, "Expected CDC"):
            load_cdc_extension(connection)

    @patch("periplus.platform.catalogue.cdc_extension.duckdb.__version__", "1.5.4")
    def test_rejects_wrong_duckdb_before_loading(self):
        connection = MagicMock()
        with self.assertRaisesRegex(RuntimeError, "requires DuckDB 1.5.5"):
            load_cdc_extension(connection)
        connection.execute.assert_not_called()
