from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import deployment


class DeploymentTests(TestCase):
    @patch("deployment.psycopg.connect")
    @patch("deployment.get_str")
    def test_catalogue_database_is_created_when_missing(self, get_str, connect) -> None:
        get_str.side_effect = {
            "ATLAS_CATALOGUE_CATALOG": "postgres",
            "ATLAS_CATALOGUE_CATALOG_DSN": "dbname=atlas_catalogue",
            "DATABASE_URL": "postgresql://atlas:atlas@postgres/atlas",
        }.__getitem__
        connection = connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.return_value = None

        deployment.ensure_catalogue_database()

        self.assertEqual(connection.execute.call_count, 2)

    @patch("deployment.psycopg.connect")
    @patch("deployment.get_str", return_value="duckdb")
    def test_local_catalogue_does_not_create_a_database(self, _get_str, connect) -> None:
        deployment.ensure_catalogue_database()
        connect.assert_not_called()

    @patch("deployment.bootstrap_catalogue")
    @patch("deployment.seed_catalogue_fixture_definitions")
    @patch("deployment.seed_system_control_plane")
    @patch("deployment.migrate_control_database")
    @patch("deployment.ensure_catalogue_database")
    def test_setup_order(
        self, ensure_database, migrate, seed_control, seed_fixtures, bootstrap
    ) -> None:
        manager = MagicMock()
        manager.attach_mock(ensure_database, "ensure")
        manager.attach_mock(migrate, "migrate")
        manager.attach_mock(bootstrap, "bootstrap")
        manager.attach_mock(seed_control, "seed_control")
        manager.attach_mock(seed_fixtures, "seed_fixtures")

        deployment.main([])

        self.assertEqual(
            manager.mock_calls,
            [
                call.ensure(),
                call.migrate(),
                call.seed_control(),
                call.bootstrap(),
                call.seed_fixtures(),
            ],
        )

    @patch("deployment.command.upgrade")
    def test_migrations_use_packaged_configuration(self, upgrade) -> None:
        deployment.migrate_control_database()

        config, revision = upgrade.call_args.args
        self.assertIsNone(config.config_file_name)
        self.assertEqual(revision, "head")
        self.assertTrue(config.get_main_option("script_location").endswith("db/alembic"))
