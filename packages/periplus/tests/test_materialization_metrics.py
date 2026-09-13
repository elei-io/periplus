from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from prometheus_client import CollectorRegistry, Histogram
from periplus.materialization import metrics
from periplus.materialization.batch import PreparedBatch, commit_prepared_batch
from periplus.materialization.document_projection import (
    DocumentProjectionSource, build_visit_batch_context,
)


class MaterializationTimingTests(unittest.TestCase):
    def test_exception_observed_and_propagated(self):
        registry = CollectorRegistry()
        histogram = Histogram('test_step_seconds', 'test', ('step', 'outcome'), registry=registry)
        with patch.object(metrics, '_step', histogram), patch.object(metrics, 'perf_counter', side_effect=[3, 8]):
            with self.assertRaisesRegex(ValueError, 'broken'):
                with metrics.step('html_parse'):
                    raise ValueError('broken')
        self.assertEqual(registry.get_sample_value('test_step_seconds_sum', {'step': 'html_parse', 'outcome': 'error'}), 5)
        self.assertEqual(registry.get_sample_value('test_step_seconds_count', {'step': 'html_parse', 'outcome': 'error'}), 1)

    def test_read_finishes_before_parse_starts(self):
        order = []

        @contextmanager
        def step(name):
            order.append(('enter', name))
            yield
            order.append(('exit', name))

        repository = MagicMock()
        repository.read.return_value = '<p>Hello</p>'
        source = DocumentProjectionSource('hash', 'key', 'zstd', 12)
        with patch('periplus.materialization.document_projection.step', step):
            result = build_visit_batch_context(repository, (source,))
        self.assertIn('hash', result.parsed_nodes_by_content)
        self.assertEqual(order, [('enter', 'html_read_decode'), ('exit', 'html_read_decode'),
                                 ('enter', 'html_parse'), ('exit', 'html_parse')])

    def test_failed_claim_does_not_start_transaction(self):
        catalogue = MagicMock()
        prepared = PreparedBatch(0, 0, 0, 0, 0, 0, {})
        run = SimpleNamespace(id=uuid4())
        batch = SimpleNamespace(id=uuid4())
        registry = CollectorRegistry()
        histogram = Histogram('test_claim_seconds', 'test', ('step', 'outcome'), registry=registry)

        @contextmanager
        def unavailable(*args, **kwargs):
            raise TimeoutError('claim unavailable')
            yield

        with patch.object(metrics, '_step', histogram), patch('periplus.materialization.batch.write_claims', unavailable):
            with self.assertRaises(TimeoutError):
                commit_prepared_batch(catalogue, run, batch, prepared)
        catalogue.remote_transaction.assert_not_called()
        self.assertEqual(registry.get_sample_value('test_claim_seconds_count', {'step': 'commit_claim_acquire', 'outcome': 'error'}), 1)

    def test_transaction_includes_commit_but_excludes_claim_acquisition(self):
        order = []

        @contextmanager
        def step(name):
            order.append('start:' + name)
            yield
            order.append('end:' + name)

        @contextmanager
        def claim(*args, **kwargs):
            order.append('claim acquired')
            yield
            order.append('claim released')

        @contextmanager
        def transaction():
            order.append('begin')
            yield
            order.append('commit')

        catalogue = MagicMock()
        catalogue.remote_transaction.side_effect = transaction
        catalogue.trusted_remote_rows.return_value = []
        prepared = PreparedBatch(0, 0, 0, 0, 0, 0, {}, membership_checked=True)
        run = SimpleNamespace(id=uuid4())
        batch = SimpleNamespace(id=uuid4(), snapshot=1)
        with patch('periplus.materialization.batch.step', step), \
             patch('periplus.materialization.batch.write_claims', claim), \
             patch('periplus.materialization.batch._is_applied', return_value=False), \
             patch('periplus.materialization.batch.PROJECTIONS', ()), \
             patch('periplus.materialization.batch.state.record_applied'), \
             patch('periplus.materialization.batch.state.begin_rebuild_write', return_value=False):
            commit_prepared_batch(catalogue, run, batch, prepared)
        self.assertEqual(order, [
            'start:commit_claim_acquire', 'claim acquired', 'end:commit_claim_acquire',
            'start:lake_transaction', 'begin', 'commit', 'end:lake_transaction',
            'start:receipt_write', 'end:receipt_write', 'claim released',
        ])
