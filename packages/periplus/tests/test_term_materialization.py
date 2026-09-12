"""Real DuckLake text-key publication, replay, and content publication."""
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import duckdb
from sqlalchemy import delete

from operational_state_fixture import operational_state
from periplus.materialization.batch import prepare_batch, commit_prepared_batch
from periplus.materialization.document_projection import DocumentProjectionSource
from periplus.materialization.registry import PROJECTIONS, REGISTRY_DIGEST
from periplus.materialization.state import publish_generation
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.exceptions import CatalogueOutcomePending
from periplus.retention.models import LakeWriteClaimRecord


class TermMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.catalogue = Catalogue(CatalogueConfig(
            'periplus', str(root/'metadata.duckdb'), str(root/'data'), 'ducklake'))
        self.addCleanup(self.catalogue.close)
        self.catalogue.bootstrap()
        self.run = SimpleNamespace(id=uuid4(), batch_size=10, registry_digest=REGISTRY_DIGEST,
            generation_tables={spec.name: spec.name for spec in PROJECTIONS})
        self.batch = SimpleNamespace(id=uuid4(), snapshot=self.catalogue.latest_snapshot(), visit_ids=())
        self.html = '<body>Monkeys monkeys in the zoo. Straße STRASSE café café 日本語 中文</body>'
        self.repository = SimpleNamespace(store=None, read=lambda key: self.html, iter_bytes=lambda key: iter([self.html.encode()]))
        source = DocumentProjectionSource('a'*64, 'objects/a', 'zstd', len(self.html), ())
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch('periplus.materialization.batch._visit_rows', return_value=[]))
        stack.enter_context(patch('periplus.materialization.batch._document_sources',
                                  return_value=((source,), frozenset({'a'*64}), ())))

    def prepare(self, **kwargs):
        source = DocumentProjectionSource('a'*64, 'objects/a', 'zstd', len(self.html.encode()), ())
        with patch('periplus.materialization.batch._document_sources', return_value=((source,), frozenset({'a'*64}), ())):
            return prepare_batch(self.catalogue, self.repository, self.run, self.batch, **kwargs)

    def rows(self, sql):
        return self.catalogue.trusted_remote_rows(sql)

    def dictionary(self):
        return {row[0] for row in self.rows('SELECT text FROM material.term')}

    def terms(self):
        return self.rows('SELECT text, content_sha256, frequency FROM material.posting '
                         'ORDER BY text, content_sha256')

    def expire_claims(self):
        # Simulate the safe expiry after an injected uncertain outcome.
        with self.sessions.begin() as session:
            session.execute(delete(LakeWriteClaimRecord))

    def test_positions_commit_replay_and_replace_atomically(self):
        self.html = '<p>mon<strong>key</strong> monkey</p>'
        prepared = self.prepare()
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        sql = "SELECT text,frequency,positions,node_indexes FROM material.posting ORDER BY text"
        initial = self.rows(sql)
        self.assertEqual([(t,f) for t,f,_,_ in initial], [('monkey',2)])
        self.assertEqual([len(x) for x in initial[0][3]], [2,1])
        self.assertEqual(initial[0][2][1],initial[0][2][0]+1)
        self.assertTrue(self.prepare().already_applied)
        self.assertEqual(self.rows(sql),initial)
        self.batch = SimpleNamespace(id=uuid4(), snapshot=self.batch.snapshot, visit_ids=())
        self.html = '<title>different</title>'
        prepared = self.prepare()
        commit_prepared_batch(self.catalogue,self.run,self.batch,prepared)
        self.assertEqual([(t,f) for t,f,_,_ in self.rows(sql)], [('different',1)])

    def test_preparation_does_not_write_dictionary_and_new_terms_publish_atomically(self):
        with patch('periplus.materialization.batch._write_partitioned_parquet', side_effect=RuntimeError('encoding failed')):
            with self.assertRaisesRegex(RuntimeError, 'encoding failed'):
                self.prepare()
        self.assertEqual(self.dictionary(), set())
        prepared = self.prepare()
        self.assertEqual(self.dictionary(), set())
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        self.assertIn('monkeys', self.dictionary())
        self.batch = SimpleNamespace(id=uuid4(), snapshot=self.batch.snapshot, visit_ids=())
        self.html = '<body>newterm newterm</body>'
        prepared = self.prepare()
        self.assertNotIn('newterm', self.dictionary())
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        self.assertEqual(self.dictionary(), {'newterm'})
        self.assertEqual([(t, f) for t, _, f in self.terms()], [('newterm', 2)])

    def test_failed_registration_rolls_back_all_content_and_vocabulary(self):
        prepared = self.prepare()
        original = self.catalogue.trusted_remote_execute
        def fail_registration(sql):
            if 'ducklake_add_data_files' in sql and "'posting'" in sql:
                raise duckdb.TransactionException('registration failed')
            return original(sql)
        with patch.object(self.catalogue, 'trusted_remote_execute', side_effect=fail_registration):
            with self.assertRaisesRegex(duckdb.TransactionException, 'registration failed'):
                commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        self.assertFalse(self.dictionary())
        self.assertEqual(self.terms(), [])
        self.assertEqual(self.rows('SELECT count(*) FROM material.html_nodes'), [(0,)])
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        self.assertTrue(self.terms())

    def test_lost_receipt_replay_does_not_duplicate_postings(self):
        prepared = self.prepare()
        with patch('periplus.materialization.state.record_applied', side_effect=RuntimeError('lost receipt')):
            with self.assertRaises(CatalogueOutcomePending):
                commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        before, dictionary = self.terms(), self.dictionary()
        self.expire_claims()
        replay = self.prepare()
        commit_prepared_batch(self.catalogue, self.run, self.batch, replay)
        self.assertEqual(self.terms(), before)
        self.assertEqual(self.dictionary(), dictionary)

    def test_stale_live_preparation_cannot_reserve_into_new_generation(self):
        other = SimpleNamespace(id=uuid4(), batch_size=10, registry_digest=REGISTRY_DIGEST)
        publish_generation(other, self.catalogue.latest_snapshot())
        result = self.prepare(active_generation=True)
        self.assertTrue(result.superseded)
        self.assertEqual(self.dictionary(), set())

    def test_hidden_generation_has_equal_logical_terms(self):
        prepared = self.prepare()
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        expected = self.terms()
        from periplus.materialization.runtime import generation_table
        generation_id = uuid4()
        hidden = {spec.name: generation_table(spec.name, generation_id) for spec in PROJECTIONS}
        for spec in PROJECTIONS:
            self.catalogue.create_materialization_generation(spec.relation, hidden[spec.name])
        self.run = SimpleNamespace(id=generation_id, generation_tables=hidden)
        self.batch = SimpleNamespace(id=uuid4(), snapshot=self.batch.snapshot, visit_ids=())
        prepared = self.prepare()
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared)
        actual = self.rows(f"SELECT text, content_sha256, frequency FROM material.{hidden['posting']} "
                           "ORDER BY text, content_sha256")
        self.assertEqual(actual, expected)

    def test_tokenizer_version_mismatch_fails_closed(self):
        from periplus.materialization.tokenization import term_tokens
        with patch('periplus.materialization.tokenization.TOKENIZER_VERSIONS', ('wrong', 'wrong', 'wrong')):
            with self.assertRaisesRegex(RuntimeError, 'term tokenizer requires'):
                term_tokens('hello')
