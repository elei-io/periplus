"""Regression coverage for terminal rebuild cleanup and contention."""
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from operational_state_fixture import operational_state
from periplus.materialization.models import MaterializationRunRecord
from periplus.materialization.store import MaterializationRunStore, MaterializationRunStopped
from periplus.materialization.runtime import BatchWork, _handle_batch, _cleanup_failed_run
from periplus.retention.identities import WriteClaimUnavailable


class FailureStoreTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)
        self.id = uuid4()
        with self.sessions.begin() as session:
            session.add(MaterializationRunRecord(id=self.id, status='running',
                source_snapshot=1, covered_snapshot=1, registry_digest='test', batch_size=500))

    def test_first_failure_and_timestamp_are_preserved(self):
        store = MaterializationRunStore()
        store.assert_writable(self.id)
        first = store.fail(self.id, RuntimeError('original failure'))
        second = store.fail(self.id, RuntimeError('missing table after cleanup'))
        self.assertEqual(second.error, first.error)
        self.assertEqual(second.completed_at.replace(tzinfo=None),
                         first.completed_at.replace(tzinfo=None))
        with self.assertRaises(MaterializationRunStopped):
            store.assert_writable(self.id)

    def test_cleanup_claim_contention_prevents_drop(self):
        run = SimpleNamespace(id=self.id, generation_tables={'nodes': 'hidden'})
        with patch('periplus.materialization.runtime.write_claims',
                   side_effect=WriteClaimUnavailable('writer active')), \
             patch('periplus.materialization.runtime.catalogue_from_env') as catalogue:
            with self.assertRaises(WriteClaimUnavailable):
                _cleanup_failed_run(run)
            catalogue.assert_not_called()

    def test_cleanup_checks_failed_state_and_drops_under_claim(self):
        store = MaterializationRunStore()
        run = SimpleNamespace(id=self.id, generation_tables={'nodes': 'hidden'})
        held = []
        @contextmanager
        def claim(keys):
            self.assertEqual(keys, {'generation': [str(self.id)]})
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        catalogue = MagicMock()
        catalogue.drop_materialization_generations.side_effect = lambda _: self.assertTrue(held)
        with patch('periplus.materialization.runtime.write_claims', claim), \
             patch('periplus.materialization.runtime.catalogue_from_env') as factory:
            factory.return_value.__enter__.return_value = catalogue
            _cleanup_failed_run(run)
            catalogue.drop_materialization_generations.assert_not_called()
            store.fail(self.id, RuntimeError('stop'))
            _cleanup_failed_run(run)
            catalogue.drop_materialization_generations.assert_called_once()


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_contention_after_many_deliveries_does_not_fail_run(self):
        batch = SimpleNamespace(id=uuid4(), run_id=uuid4(), status='running',
                                attempts=50, ordinal=329, visit_ids=('visit',))
        store = AsyncMock()
        store.start_batch.return_value = store.get_batch.return_value = batch
        store.get.return_value = SimpleNamespace(id=batch.run_id, status='running')
        message = AsyncMock()
        message.data = BatchWork(batch_id=batch.id).model_dump_json().encode()
        with patch('periplus.materialization.runtime._execute_batch',
                   side_effect=WriteClaimUnavailable('writer active')):
            await _handle_batch(message, AsyncMock(), store, MagicMock())
        store.fail.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=1)

    async def test_exhausted_transaction_conflict_still_fails(self):
        import duckdb
        batch = SimpleNamespace(id=uuid4(), run_id=uuid4(), status='running',
                                attempts=5, ordinal=329, visit_ids=('visit',))
        store = AsyncMock()
        store.start_batch.return_value = store.get_batch.return_value = batch
        store.get.return_value = SimpleNamespace(id=batch.run_id, status='running')
        message = AsyncMock()
        message.data = BatchWork(batch_id=batch.id).model_dump_json().encode()
        with patch('periplus.materialization.runtime._execute_batch',
                   side_effect=duckdb.TransactionException('transaction conflict')), \
             patch('periplus.materialization.runtime._try_cleanup_failed_run', new_callable=AsyncMock):
            await _handle_batch(message, AsyncMock(), store, MagicMock())
        store.fail.assert_awaited_once()
        message.ack.assert_awaited_once()

    async def test_delayed_writer_stops_without_completing_or_refailing(self):
        batch = SimpleNamespace(id=uuid4(), run_id=uuid4(), status='running')
        store = AsyncMock()
        store.start_batch.return_value = batch
        store.get.return_value = SimpleNamespace(id=batch.run_id, status='running')
        message = AsyncMock()
        message.data = BatchWork(batch_id=batch.id).model_dump_json().encode()
        with patch('periplus.materialization.runtime._execute_batch',
                   side_effect=MaterializationRunStopped('failed while preparing')):
            await _handle_batch(message, AsyncMock(), store, MagicMock())
        store.fail.assert_not_awaited()
        store.complete_batch.assert_not_awaited()
        message.ack.assert_awaited_once()


class WriteGuardTests(unittest.TestCase):
    def test_prepared_files_cannot_write_after_cleanup(self):
        from periplus.materialization.batch import commit_prepared_batch
        catalogue = MagicMock()
        run = SimpleNamespace(id=uuid4())
        prepared = SimpleNamespace(retained_visit_ids=(), retained_content_hashes=())
        held = []
        @contextmanager
        def claim(_):
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        def stopped():
            self.assertTrue(held)
            raise MaterializationRunStopped('generation failed')
        with patch('periplus.materialization.batch.write_claims', claim), \
             patch('periplus.materialization.batch._catalogue_storage'):
            with self.assertRaises(MaterializationRunStopped):
                commit_prepared_batch(catalogue, run, SimpleNamespace(id=uuid4()),
                                      prepared, assert_writable=stopped)
        catalogue.remote_transaction.assert_not_called()
        catalogue.trusted_remote_execute.assert_not_called()

    def test_dictionary_reservation_rechecks_after_preparation(self):
        from periplus.materialization.batch import prepare_batch
        catalogue = MagicMock()
        run = SimpleNamespace(id=uuid4())
        held = []
        @contextmanager
        def claim(_):
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        def stopped():
            self.assertTrue(held)
            raise MaterializationRunStopped('failed during parsing')
        with patch('periplus.materialization.batch._applied_result', return_value=None), \
             patch('periplus.materialization.batch._visit_rows', return_value=[]), \
             patch('periplus.materialization.batch._document_sources', return_value=((), (), ())), \
             patch('periplus.materialization.batch.build_visit_batch_context'), \
             patch('periplus.materialization.batch.PROJECTIONS', ()), \
             patch('periplus.materialization.batch.write_claims', claim):
            with self.assertRaises(MaterializationRunStopped):
                prepare_batch(catalogue, MagicMock(), run, MagicMock(), assert_writable=stopped)
        catalogue.remote_transaction.assert_not_called()
