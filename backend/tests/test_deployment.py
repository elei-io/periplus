from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import deployment


class DeploymentTests(TestCase):
    @patch("deployment.bootstrap_catalogue")
    @patch("deployment.seed_catalogue_fixture_definitions")
    @patch("deployment.seed_system_control_plane")
    @patch("deployment.migrate_control_database")
    def test_setup_order(
        self, migrate, seed_control, seed_fixtures, bootstrap
    ) -> None:
        manager = MagicMock()
        manager.attach_mock(migrate, "migrate")
        manager.attach_mock(bootstrap, "bootstrap")
        manager.attach_mock(seed_control, "seed_control")
        manager.attach_mock(seed_fixtures, "seed_fixtures")

        deployment.main([])

        self.assertEqual(
            manager.mock_calls,
            [
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
