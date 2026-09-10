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
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord


@unittest.skipUnless(os.environ.get('PERIPLUS_TEST_FRONTIER_POSTGRES') == '1', 'requires isolated live Postgres')
class FrontierBaselineTests(unittest.TestCase):
    def test_migration_chain_matches_current_models_preserves_controls_and_rejects_lossy_downgrade(self):
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
        queued_id = uuid4()
        schema = 'frontier_baseline_test_' + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.dialect.default_schema_name = schema
            context = MigrationContext.configure(connection, opts={'compare_type': True})
            with Operations.context(context):
                for migration in migrations:
                    if migration.revision == '20260910_0013':
                        # Populate the old schema at exhausted lifetime limits.
                        for name in migration.COLUMNS:
                            connection.execute(text(f'ALTER TABLE frontier_control ALTER COLUMN {name} SET DEFAULT 0'))
                        with Session(connection) as session:
                            ensure_frontier_control(session)
                            control = session.get(FrontierControlRecord, 1)
                            control.dispatch_limit = 7
                            control.pending_count = 12
                            control.active_count = 2
                            control.capture_timeout_ms = 45000
                            session.add(AcquisitionRecord(id=queued_id, url='https://example.com/',
                                domain='example.com', capture_key='migration-test', requirements={}))
                            session.commit()
                        connection.execute(text('UPDATE frontier_control SET attempt_allowance=100, started_attempts=100, capture_time_allowance_ms=172800000, charged_capture_ms=172800000'))
                    migration.upgrade()
                self.assertEqual(compare_metadata(context, Base.metadata), [])
                self.assertEqual(set(inspect(connection).get_table_names()), set(Base.metadata.tables))
                self.assertFalse(any('graph' in name for name in Base.metadata.tables))
                with Session(connection) as session:
                    ensure_frontier_control(session)
                    control = session.get(FrontierControlRecord, 1)
                    self.assertEqual(control.dispatch_limit, 7)
                    self.assertEqual((control.pending_count, control.active_count), (12, 2))
                    self.assertEqual(control.capture_timeout_ms, 45000)
                    self.assertEqual(session.get(AcquisitionRecord, queued_id).status, 'queued')
                    self.assertNotIn('started_attempts', {column['name'] for column in inspect(connection).get_columns('frontier_control')})
                    control.paused = True
                    session.flush()
                    ensure_frontier_control(session)
                    self.assertTrue(session.get(FrontierControlRecord, 1).paused)
                    session.commit()
                with self.assertRaisesRegex(RuntimeError, 'Lifetime crawl usage was removed'):
                    migrations[-1].downgrade()
            connection.execute(text('SET search_path TO public'))
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
