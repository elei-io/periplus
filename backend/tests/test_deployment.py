from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from atlas.entrypoints import setup


class DeploymentTests(TestCase):
    @patch("atlas.entrypoints.setup.bootstrap_catalogue")
    @patch("atlas.entrypoints.setup.seed_system_control_plane")
    @patch("atlas.entrypoints.setup.migrate_control_database")
    def test_setup_order(
        self, migrate, seed_control, bootstrap
    ) -> None:
        manager = MagicMock()
        manager.attach_mock(migrate, "migrate")
        manager.attach_mock(bootstrap, "bootstrap")
        manager.attach_mock(seed_control, "seed_control")

        setup.main([])

        self.assertEqual(
            manager.mock_calls,
            [
                call.migrate(),
                call.seed_control(),
                call.bootstrap(),
            ],
        )

    @patch("atlas.entrypoints.setup.command.upgrade")
    def test_migrations_use_packaged_configuration(self, upgrade) -> None:
        setup.migrate_control_database()

        config, revision = upgrade.call_args.args
        self.assertIsNone(config.config_file_name)
        self.assertEqual(revision, "head")
        self.assertTrue(
            config.get_main_option("script_location").endswith(
                "platform/postgres/alembic"
            )
        )
