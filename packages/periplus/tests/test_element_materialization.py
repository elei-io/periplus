"""Real DuckLake element publication, replay, and generation supersession."""
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
from periplus.materialization.batch import PreparedMembershipChanged, prepare_batch, commit_prepared_batch
from periplus.materialization.document_projection import DocumentProjectionSource
from periplus.materialization.registry import PROJECTIONS, REGISTRY_DIGEST
from periplus.materialization.state import publish_generation
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.exceptions import CatalogueOutcomePending
from periplus.retention.models import LakeWriteClaimRecord


class ElementMaterializationTests(unittest.TestCase):
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
        source = DocumentProjectionSource('a'*64, 'objects/a', 'zstd', len(self.html.encode()), ())
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

    def elements(self, name='html_elements'):
        return self.rows(f'SELECT content_sha256,node_index,tag,text,text_direct FROM material.{name} ORDER BY content_sha256,node_index')

    def expire_claims(self):
        # Simulate the safe expiry after an injected uncertain outcome.
        with self.sessions.begin() as session:
            session.execute(delete(LakeWriteClaimRecord))

    def test_commit_replay_and_repeated_content_append_no_duplicates(self):
        self.html='<p>mon<strong>key</strong> monkey</p>'
        prepared=self.prepare()
        self.assertEqual(self.elements(),[])
        commit_prepared_batch(self.catalogue,self.run,self.batch,prepared)
        initial=self.elements()
        postings=self.rows("SELECT term,content_id,node_indexes FROM public_v1.html_term ORDER BY term,content_id")
        self.assertEqual([row[0] for row in postings], ['monkey'])
        self.assertTrue(postings[0][2])
        self.assertEqual([r[3:] for r in initial if r[2]=='p'],[('monkey monkey','mon monkey')])
        self.assertTrue(self.prepare().already_applied)
        self.assertEqual(self.elements(),initial)
        self.assertEqual(self.rows("SELECT term,content_id,node_indexes FROM public_v1.html_term ORDER BY term,content_id"),postings)
        self.batch=SimpleNamespace(id=uuid4(),snapshot=self.batch.snapshot,visit_ids=())
        commit_prepared_batch(self.catalogue,self.run,self.batch,self.prepare())
        self.assertEqual(self.elements(), initial)
        self.assertEqual(self.rows("SELECT term,content_id,node_indexes FROM public_v1.html_term ORDER BY term,content_id"),postings)

    def test_preparation_failure_has_no_published_rows(self):
        with patch('periplus.materialization.batch._write_partitioned_parquet',side_effect=RuntimeError('encoding failed')):
            with self.assertRaisesRegex(RuntimeError,'encoding failed'):self.prepare()
        self.assertEqual(self.elements(),[])

    def test_overlapping_preparations_recheck_then_append_only_missing_identities(self):
        first, shared, last = (str(uuid4()) for _ in range(3))
        now = datetime.now(UTC)
        def visits(ids):
            return [(identity, None, 'https://example.com/', now) for identity in ids]
        with patch('periplus.materialization.batch._visit_rows', return_value=visits([first, shared])):
            prepared_first = self.prepare()
        other = SimpleNamespace(id=uuid4(), snapshot=self.batch.snapshot, visit_ids=())
        with patch('periplus.materialization.batch._visit_rows', return_value=visits([shared, last])):
            prepared_other = prepare_batch(self.catalogue, self.repository, self.run, other)
        commit_prepared_batch(self.catalogue, self.run, self.batch, prepared_first)
        before = self.elements()
        with self.assertRaises(PreparedMembershipChanged):
            commit_prepared_batch(self.catalogue, self.run, other, prepared_other)
        with patch('periplus.materialization.batch._visit_rows', return_value=visits([shared, last])):
            prepared_other = prepare_batch(self.catalogue, self.repository, self.run, other)
        with patch.object(self.catalogue, 'trusted_remote_execute', wraps=self.catalogue.trusted_remote_execute) as execute:
            commit_prepared_batch(self.catalogue, self.run, other, prepared_other)
            self.assertFalse(any(call.args[0].startswith('DELETE') for call in execute.call_args_list))
        self.assertEqual(self.elements(), before)
        self.assertEqual(sorted(str(row[0]) for row in self.rows('SELECT visit_id FROM material.visit_readiness')),
                         sorted([first, shared, last]))

    def test_runtime_reprepares_when_publication_membership_changes(self):
        from periplus.materialization.runtime import _commit_retained_batch
        prepared = self.prepare()
        result = SimpleNamespace(output_rows=prepared.output_rows)
        with patch('periplus.materialization.runtime.prepare_batch', return_value=prepared) as prepare, patch(
            'periplus.materialization.runtime.commit_prepared_batch',
            side_effect=[PreparedMembershipChanged('concurrent append'), result]):
            self.assertIs(_commit_retained_batch(self.catalogue, self.repository, self.run, self.batch), result)
        self.assertEqual(prepare.call_count, 2)

    def test_registration_failure_rolls_back(self):
        prepared=self.prepare();original=self.catalogue.trusted_remote_execute
        def fail(sql):
            if 'ducklake_add_data_files' in sql and "'html_elements'" in sql:raise duckdb.TransactionException('registration failed')
            return original(sql)
        with patch.object(self.catalogue,'trusted_remote_execute',side_effect=fail):
            with self.assertRaises(duckdb.TransactionException):commit_prepared_batch(self.catalogue,self.run,self.batch,prepared)
        self.assertEqual(self.elements(),[])
        commit_prepared_batch(self.catalogue,self.run,self.batch,prepared)
        self.assertTrue(self.elements())

    def test_lost_receipt_replay_does_not_duplicate_elements(self):
        prepared=self.prepare()
        with patch('periplus.materialization.state.record_applied',side_effect=RuntimeError('lost receipt')):
            with self.assertRaises(CatalogueOutcomePending):commit_prepared_batch(self.catalogue,self.run,self.batch,prepared)
        before=self.elements();self.expire_claims()
        recovered = commit_prepared_batch(self.catalogue,self.run,self.batch,self.prepare())
        self.assertEqual(recovered.output_rows, prepared.output_rows)
        self.assertEqual(recovered.output_bytes, prepared.output_bytes)
        self.assertEqual(self.elements(),before)

    def test_stale_live_preparation_cannot_write_new_generation(self):
        other=SimpleNamespace(id=uuid4(),batch_size=10,registry_digest=REGISTRY_DIGEST)
        publish_generation(other,self.catalogue.latest_snapshot())
        self.assertTrue(self.prepare(active_generation=True).superseded)
        self.assertEqual(self.elements(),[])

    def test_hidden_generation_has_identical_elements(self):
        commit_prepared_batch(self.catalogue,self.run,self.batch,self.prepare())
        expected=self.elements()
        from periplus.materialization.runtime import generation_table
        generation_id=uuid4();hidden={s.name:generation_table(s.name,generation_id) for s in PROJECTIONS}
        for spec in PROJECTIONS:self.catalogue.create_materialization_generation(spec.relation,hidden[spec.name])
        self.assertEqual(self.rows(
            "SELECT value FROM periplus.options() WHERE option_name='parquet_row_group_size' "
            f"AND scope='TABLE' AND scope_entry='material.{hidden['html_terms']}'"
        ), [('2048',)])
        self.run=SimpleNamespace(id=generation_id,generation_tables=hidden)
        self.batch=SimpleNamespace(id=uuid4(),snapshot=self.batch.snapshot,visit_ids=())
        commit_prepared_batch(self.catalogue,self.run,self.batch,self.prepare())
        self.assertEqual(self.elements(hidden['html_elements']),expected)
