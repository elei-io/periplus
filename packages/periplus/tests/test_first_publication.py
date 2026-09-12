"""First publication, durable uncertainty and replacement recovery in real DuckLake."""
from dataclasses import replace
from datetime import UTC, datetime
from contextlib import contextmanager
from unittest.mock import patch

from test_term_materialization import TermMaterializationTests
from periplus.materialization.batch import commit_prepared_batch
from periplus.materialization.models import MaterializationRunRecord, MaterializationBatchRecord
from periplus.materialization.state import begin_rebuild_write
from periplus.platform.catalogue.exceptions import CatalogueOutcomePending


class FirstPublicationTests(TermMaterializationTests):
    def setUp(self):
        super().setUp()
        self.run.source_snapshot = self.batch.snapshot
        with self.sessions.begin() as session:
            session.add(MaterializationRunRecord(
                id=self.run.id, status='running', source_snapshot=self.batch.snapshot,
                covered_snapshot=self.batch.snapshot, generation_tables=self.run.generation_tables,
                registry_digest=self.run.registry_digest, batch_size=10, created_at=datetime.now(UTC)))
            session.flush()
            session.add(MaterializationBatchRecord(id=self.batch.id, run_id=self.run.id,
                ordinal=0, snapshot=self.batch.snapshot, visit_ids=[]))

    def test_first_append_then_uncertain_receipt_recovers_by_replacement(self):
        prepared = replace(self.prepare(), stable_content_ownership=True)
        original = self.catalogue.trusted_remote_execute
        with patch.object(self.catalogue, 'trusted_remote_execute', wraps=original) as execute:
            with patch('periplus.materialization.state.record_applied', side_effect=RuntimeError('lost receipt')):
                with self.assertRaises(CatalogueOutcomePending):
                    commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
            self.assertFalse(any(c.args[0].startswith('DELETE') for c in execute.call_args_list))
        expected = self.terms()
        self.expire_claims()
        with patch.object(self.catalogue, 'trusted_remote_execute', wraps=original) as execute:
            commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
            self.assertTrue(any(c.args[0].startswith('DELETE') for c in execute.call_args_list))
        self.assertEqual(expected, self.terms())
        self.assertTrue(commit_prepared_batch(self.catalogue, self.run, self.batch, prepared).already_applied)

    def test_intent_before_lake_failure_forces_replacement(self):
        prepared = replace(self.prepare(), stable_content_ownership=True)
        self.assertTrue(begin_rebuild_write(self.run, self.batch))
        self.assertFalse(begin_rebuild_write(self.run, self.batch))
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        self.assertTrue(self.terms())

    def test_intent_failure_does_not_enter_lake(self):
        prepared = replace(self.prepare(), stable_content_ownership=True)
        with patch('periplus.materialization.state.begin_rebuild_write', side_effect=RuntimeError('uncertain intent')):
            with patch.object(self.catalogue, 'remote_transaction') as transaction:
                with self.assertRaisesRegex(RuntimeError, 'uncertain intent'):
                    commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
                transaction.assert_not_called()

    def test_catchup_cannot_append(self):
        with self.sessions.begin() as session:
            session.get(MaterializationRunRecord, self.run.id).covered_snapshot += 1
        self.assertFalse(begin_rebuild_write(self.run, self.batch))

    def test_rollback_replays_as_replacement(self):
        prepared = replace(self.prepare(), stable_content_ownership=True)
        transaction = self.catalogue.remote_transaction
        @contextmanager
        def rollback():
            with transaction():
                yield
                raise RuntimeError('before commit')
        with patch.object(self.catalogue, 'remote_transaction', rollback):
            with self.assertRaisesRegex(RuntimeError, 'before commit'):
                commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        self.assertEqual(self.terms(), [])
        self.expire_claims()
        self.assertFalse(begin_rebuild_write(self.run, self.batch))
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        self.assertTrue(self.terms())

    def test_changed_content_owner_uses_replacement(self):
        prepared = replace(self.prepare(), stable_content_ownership=False)
        original = self.catalogue.trusted_remote_execute
        with patch.object(self.catalogue, 'trusted_remote_execute', wraps=original) as execute:
            commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
            self.assertTrue(any(c.args[0].startswith('DELETE') for c in execute.call_args_list))

    def test_superseded_run_cannot_append(self):
        with self.sessions.begin() as session:
            session.get(MaterializationRunRecord, self.run.id).status = 'failed'
        self.assertFalse(begin_rebuild_write(self.run, self.batch))

    def test_initial_plan_rejects_duplicate_visit_ownership(self):
        from periplus.materialization.store import MaterializationRunStore
        with self.sessions.begin() as session:
            run = session.get(MaterializationRunRecord, self.run.id)
            run.status = 'planning'
            run.generation_tables = {}
        with self.assertRaisesRegex(ValueError, 'disjoint visits'):
            MaterializationRunStore().finish_plan(self.run.id,
                generation_tables=self.run.generation_tables,
                batches=[(self.batch.snapshot, ['same']), (self.batch.snapshot, ['same'])])
