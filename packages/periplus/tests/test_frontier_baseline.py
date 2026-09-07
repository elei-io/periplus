"""Replacement baseline round-trip against an isolated actual PostgreSQL schema."""
import importlib.util
import os
from pathlib import Path
import unittest
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from periplus.platform.postgres import Base, get_database_url
import periplus.platform.postgres.models
from periplus.crawl.control.collections.frontier_controls import ensure_frontier_control
from periplus.crawl.runtime.frontier_models import FrontierControlRecord


@unittest.skipUnless(os.environ.get('PERIPLUS_TEST_FRONTIER_POSTGRES') == '1', 'requires isolated live Postgres')
class FrontierBaselineTests(unittest.TestCase):
    def test_migration_chain_matches_current_models_seeds_once_and_downgrades_cleanly(self):
        root = Path(periplus.platform.postgres.models.__file__).parent / 'alembic/versions'
        files = sorted(root.glob('*.py'))
        migrations = []
        previous = None
        for path in files:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            self.assertEqual(migration.down_revision, previous)
            migrations.append(migration)
            previous = migration.revision
        engine = create_engine(get_database_url())
        self.addCleanup(engine.dispose)
        schema = 'frontier_baseline_test_' + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.dialect.default_schema_name = schema
            context = MigrationContext.configure(connection, opts={'compare_type': True})
            with Operations.context(context):
                for migration in migrations:
                    migration.upgrade()
                self.assertEqual(compare_metadata(context, Base.metadata), [])
                self.assertEqual(set(inspect(connection).get_table_names()), set(Base.metadata.tables))
                self.assertFalse(any('graph' in name or 'schedule' in name for name in Base.metadata.tables))
                with Session(connection) as session:
                    ensure_frontier_control(session)
                    control = session.get(FrontierControlRecord, 1)
                    self.assertEqual(control.dispatch_limit, 48)
                    control.paused = True
                    session.flush()
                    ensure_frontier_control(session)
                    self.assertTrue(session.get(FrontierControlRecord, 1).paused)
                    session.commit()
                for migration in reversed(migrations):
                    migration.downgrade()
                self.assertEqual(inspect(connection).get_table_names(), [])
            connection.execute(text('SET search_path TO public'))
            connection.execute(text(f'DROP SCHEMA "{schema}"'))
