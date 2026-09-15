from unittest import TestCase
from unittest.mock import MagicMock, call, patch
from types import SimpleNamespace

from periplus.entrypoints import setup


class DeploymentTests(TestCase):
    @patch("periplus.entrypoints.setup.grant_query_target")
    @patch("periplus.entrypoints.setup.install_query_user")
    @patch("periplus.entrypoints.setup.install_public_schema")
    @patch("periplus.entrypoints.setup.install_material_schema")
    @patch("periplus.entrypoints.setup.connect_clickhouse")
    @patch("periplus.entrypoints.setup.BuildControl")
    def test_setup_preserves_published_and_reclaimed_targets(
        self, controls, connect, material, public, query_user, grant
    ) -> None:
        control = controls.return_value
        control.builds.return_value = [
            SimpleNamespace(protected=True, query_database="query_" + "a" * 32),
            SimpleNamespace(protected=False, query_database="public_v1"),
        ]
        for phase in ("serving", "previous", "retired"):
            with self.subTest(phase=phase):
                control.get.return_value = SimpleNamespace(phase=phase)
                setup.bootstrap_catalogue()
                material.assert_not_called()
                public.assert_not_called()
        self.assertEqual(query_user.call_count, 3)
        self.assertEqual(grant.call_args, call(connect.return_value, "query_" + "a" * 32))

    @patch("periplus.entrypoints.setup.bootstrap_catalogue")
    @patch("periplus.entrypoints.setup.seed_system_control_plane")
    @patch("periplus.entrypoints.setup.migrate_control_database")
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
                call.bootstrap(None, refresh_public_views=False),
            ],
        )

    @patch("periplus.entrypoints.setup.command.upgrade")
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

    @patch("periplus.entrypoints.setup.grant_query_target")
    @patch("periplus.entrypoints.setup.install_query_user")
    @patch("periplus.entrypoints.setup.install_public_schema")
    @patch("periplus.entrypoints.setup.install_material_schema")
    @patch("periplus.entrypoints.setup.connect_clickhouse")
    @patch("periplus.entrypoints.setup.BuildControl")
    def test_explicit_maintenance_refreshes_only_protected_views(self, controls, connect, material, public, user, grant):
        control = controls.return_value
        control.get.return_value = SimpleNamespace(phase="serving")
        control.builds.return_value = [
            SimpleNamespace(protected=True, material_database="material_"+"a"*32, query_database="query_"+"a"*32),
            SimpleNamespace(protected=False, material_database="material_"+"b"*32, query_database="query_"+"b"*32),
        ]
        setup.bootstrap_catalogue(refresh_public_views=True)
        material.assert_not_called()
        public.assert_called_once_with(connect.return_value, "material_"+"a"*32, "query_"+"a"*32)
