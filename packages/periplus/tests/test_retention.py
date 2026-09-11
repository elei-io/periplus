from operational_state_fixture import operational_state
"""Retention correctness against disposable real DuckLake catalogues."""
from capture_policy_fixture import capture_policy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.service import CatalogueService
from periplus.platform.catalogue.records import VisitEvidence, VisitRecord
from periplus.platform.catalogue.lineage import CollectionDefinition, CollectionOutcome, FulfillmentRecord, AcquisitionReason
from periplus.retention.catalogue import RetentionCatalogue
from periplus.retention.identities import EvidenceRetired, retired, write_claims
from periplus.retention.runtime import RetentionSettings, RetentionSweep
from periplus.crawl.control.collections.schemas import CollectionSpec


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.catalogue = Catalogue(CatalogueConfig('periplus', str(root/'metadata.duckdb'), str(root/'data'), 'ducklake'))
        self.addCleanup(self.catalogue.close)
        self.catalogue.bootstrap()
        self.service = CatalogueService(self.catalogue)
        self.retention = RetentionCatalogue(self.catalogue)
        self.now = datetime.now(UTC)
        self.old = self.now - timedelta(days=10)

    def visit(self):
        visit = VisitEvidence(visit=VisitRecord(capture_policy=capture_policy(), visit_id=uuid4(), requested_url='https://example.com/',
            admitted_at=self.old, finished_at=self.old, outcome='failed'), attempts=())
        self.service.record_visits([visit])
        return visit

    def request(self, visit, seconds=None, *, settled=True, visibility='public'):
        identity = uuid4()
        records = [CollectionDefinition(record_id=identity, collection_id=identity,
             recorded_at=self.old,
            specification=CollectionSpec(retention_seconds=seconds, request_class=visibility).model_dump(mode='json')),
            FulfillmentRecord(record_id=uuid4(), collection_id=identity, observation_id=visit.visit.visit_id,
                 recorded_at=self.old, requested_url='https://example.com/', depth=0, rule_id='seed', mode='acquired')]
        if settled:
            records.append(CollectionOutcome(record_id=identity, collection_id=identity,
                 recorded_at=self.old, outcome='eligible_links_exhausted',
                consumed_pages=1, supplied_pages=0, failed_pages=1))
        self.service.record_lineage(records)
        return identity

    def candidates(self):
        return self.retention.plan(now=self.now).candidates

    def test_commit_conflict_retries_retirement_after_rollback(self):
        import duckdb
        from unittest.mock import patch
        visit = self.document_visit()
        candidate = self.candidates()[0]
        execute = self.catalogue.trusted_remote_execute
        commits = 0
        def conflicting_commit(sql, *args, **kwargs):
            nonlocal commits
            if sql == 'COMMIT':
                commits += 1
                if commits == 1:
                    execute('ROLLBACK')
                    raise duckdb.TransactionException('concurrent compaction')
            return execute(sql, *args, **kwargs)
        with patch.object(self.catalogue, 'trusted_remote_execute', side_effect=conflicting_commit):
            self.assertTrue(self.retention.purge_observation(candidate, now=self.now))
        self.assertEqual(commits, 2)
        self.assertTrue(retired('observation', str(visit.visit.visit_id)))
        self.assertEqual(self.object_count(), 1)
        self.assertEqual(self.candidates(), [])

    def test_request_conflict_rechecks_protection_and_retry_bound(self):
        import duckdb
        from unittest.mock import patch
        visit = self.visit()
        identity = self.request(visit, 1)
        original = self.retention._purge_request
        calls = 0
        def conflict_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise duckdb.TransactionException('concurrent writer')
            return original(*args, **kwargs)
        with patch.object(self.retention, '_purge_request', side_effect=conflict_once):
            self.assertTrue(self.retention.purge_request(identity, now=self.now))
        self.assertEqual(calls, 2)
        with patch.object(self.retention, '_purge_request', side_effect=duckdb.TransactionException('conflict')) as purge, patch(
                'periplus.platform.catalogue.operations.time.sleep'):
            with self.assertRaises(duckdb.TransactionException):
                self.retention.purge_request(identity, now=self.now)
            self.assertEqual(purge.call_count, 5)

    def test_observation_protection_is_rechecked_after_conflict(self):
        import duckdb
        from unittest.mock import patch
        visit = self.visit()
        candidate = self.candidates()[0]
        original = self.retention._purge_observation
        calls = 0
        def conflict_then_protected(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.request(visit)  # A concurrent indefinite request wins before retirement.
                raise duckdb.TransactionException('concurrent fulfillment')
            return original(*args, **kwargs)
        with patch.object(self.retention, '_purge_observation', side_effect=conflict_then_protected):
            self.assertFalse(self.retention.purge_observation(candidate, now=self.now))
        self.assertEqual(calls, 2)
        self.assertFalse(retired('observation', str(visit.visit.visit_id)))
        self.assertIn(visit.visit.visit_id, self.service.get_visit_evidence([visit.visit.visit_id]))

    def test_ambiguous_storage_failure_is_not_retried(self):
        import duckdb
        from unittest.mock import patch
        with patch.object(self.retention, '_purge_request', side_effect=duckdb.IOException('unknown commit')) as purge:
            with self.assertRaises(duckdb.IOException):
                self.retention.purge_request(uuid4(), now=self.now)
            self.assertEqual(purge.call_count, 1)

    def test_shared_forever_private_request_protects_expired_public_result(self):
        visit = self.visit()
        expired_request = self.request(visit, 86400)
        self.assertEqual(len(self.candidates()), 1)
        self.request(visit, visibility='admin')
        self.assertEqual(self.candidates(), [])
        self.assertTrue(self.retention.purge_request(expired_request, now=self.now))
        self.assertEqual(self.candidates(), [])

    def test_active_and_missing_definition_protect_even_with_finite_retention(self):
        visit = self.visit()
        self.request(visit, 1, settled=False)
        self.assertEqual(self.candidates(), [])
        unknown = self.visit()
        self.service.record_lineage([FulfillmentRecord(record_id=uuid4(), collection_id=uuid4(),
            observation_id=unknown.visit.visit_id, requested_url='https://example.com/',
            recorded_at=self.old, depth=0, rule_id='seed', mode='acquired')])
        self.assertEqual(self.candidates(), [])

    def test_expiry_boundary_and_defaults(self):
        from periplus.retention.policy import expires_at
        self.assertIsNone(CollectionSpec().retention_seconds)
        self.assertIsNone(expires_at(None, self.old))
        self.assertIsNone(expires_at(1, None))
        visit = self.visit()
        self.request(visit, 86400)
        self.assertEqual(self.retention.plan(now=self.old + timedelta(seconds=86399)).candidates, [])
        self.assertEqual(len(self.retention.plan(now=self.old + timedelta(days=1)).candidates), 1)
        for invalid in [0, -1, 315360001]:
            with self.assertRaises(ValueError): CollectionSpec(retention_seconds=invalid)

    def test_capture_policy_round_trips_and_retires_with_observation(self):
        from periplus.platform.catalogue.public import install_public_catalogue
        install_public_catalogue(self.catalogue)
        visit = self.visit()
        identity = visit.visit.visit_id
        # This catalogue has no operational database: evidence is self-contained.
        self.assertEqual(self.service.get_visit_evidence([identity])[identity], visit)
        row = self.catalogue.trusted_connection.execute(
            "SELECT capture_policy::JSON FROM ingest.visits WHERE visit_id = ?",
            [identity],
        ).fetchone()
        import json
        self.assertEqual(json.loads(row[0]), visit.visit.capture_policy.model_dump(mode="json"))
        self.assertFalse(self.service.record_visits([visit])[0].created)
        from periplus.platform.catalogue import CatalogueConflictError
        changed = visit.model_copy(update={"visit": visit.visit.model_copy(update={
            "capture_policy": visit.visit.capture_policy.model_copy(update={"slug": "changed"})})})
        with self.assertRaises(CatalogueConflictError):
            self.service.record_visits([changed])
        self.request(visit, 1)
        self.retention.purge_observation(self.candidates()[0], now=self.now)
        self.assertEqual(self.service.get_visit_evidence([identity]), {})
        self.assertEqual(self.catalogue.trusted_connection.execute(
            "SELECT count(*) FROM ingest.visits WHERE visit_id = ?", [identity],
        ).fetchone()[0], 0)

    def test_retirement_is_idempotent_and_delayed_evidence_cannot_resurrect(self):
        visit = self.visit()
        identity = self.request(visit, 1)
        candidate = self.candidates()[0]
        self.assertTrue(self.retention.purge_observation(candidate, now=self.now))
        self.assertFalse(self.retention.purge_observation(candidate, now=self.now))
        with self.assertRaises(EvidenceRetired): self.service.record_visits([visit])
        self.assertTrue(self.retention.purge_request(identity, now=self.now))
        self.assertFalse(self.retention.purge_request(identity, now=self.now))
        with self.assertRaises(EvidenceRetired): self.service.record_lineage([CollectionDefinition(
            record_id=identity, collection_id=identity, recorded_at=self.now,  specification={})])
        self.assertTrue(retired('observation', str(visit.visit.visit_id)))

    def test_new_fulfillment_between_plan_and_delete_is_rechecked(self):
        visit = self.visit()
        candidate = self.candidates()[0]
        self.request(visit)
        self.assertFalse(self.retention.purge_observation(candidate, now=self.now))
        self.assertIsNotNone(self.service.get_visit_evidence([visit.visit.visit_id]).get(visit.visit.visit_id))

    def test_ancestor_reference_is_not_a_retention_root(self):
        parent, child = self.visit(), self.visit()
        request = self.request(child)
        self.service.record_lineage([AcquisitionReason(record_id=uuid4(), observation_id=child.visit.visit_id,
            parent_observation_id=parent.visit.visit_id, recorded_at=self.old,  collection_id=request, reason='collection', policy_version='1', rule_id='follow')])
        self.assertEqual([item.observation_id for item in self.candidates()], [parent.visit.visit_id])
        self.retention.purge_observation(self.candidates()[0], now=self.now)
        self.assertEqual(self.catalogue.trusted_connection.execute('SELECT count(*) FROM ingest.acquisition_reasons').fetchone()[0], 1)

    def test_prepared_materialization_cannot_restore_retired_rows(self):
        from periplus.materialization.batch import prepare_batch, commit_prepared_batch
        from periplus.materialization.registry import PROJECTIONS
        visit = self.visit()
        run = SimpleNamespace(id=uuid4(), generation_tables={spec.name: spec.name for spec in PROJECTIONS})
        batch = SimpleNamespace(id=uuid4(), visit_ids=(str(visit.visit.visit_id),), snapshot=self.catalogue.latest_snapshot())
        prepared = prepare_batch(self.catalogue, SimpleNamespace(store=None), run, batch)
        self.retention.purge_observation(self.candidates()[0], now=self.now)
        with self.assertRaises(EvidenceRetired): commit_prepared_batch(self.catalogue, run, batch, prepared)
        prepared = prepare_batch(self.catalogue, SimpleNamespace(store=None), run, batch)
        commit_prepared_batch(self.catalogue, run, batch, prepared)
        self.assertEqual(self.catalogue.trusted_connection.execute('SELECT count(*) FROM material.visit_readiness').fetchone()[0], 0)

    def test_disabled_sweep_never_opens_storage(self):
        sweep = RetentionSweep(RetentionSettings(), lambda: self.fail('control storage opened'))
        self.assertEqual(sweep.run(), {'mode':'disabled'})

    def test_keyset_plan_has_no_skips_when_prior_batch_is_deleted(self):
        for _ in range(3): self.visit()
        first = self.retention.plan(now=self.now, limit=2)
        for candidate in first.candidates: self.retention.purge_observation(candidate, now=self.now)
        last = first.candidates[-1]
        second = self.retention.plan(now=self.now, limit=2, after=(last.finished_at, last.observation_id))
        self.assertEqual(len(second.candidates), 1)

    def document_visit(self, content_hash='a'*64, key='documents/sha256/aa/object'):
        from periplus.platform.catalogue.records import DocumentRecord, AttemptRecord, attempt_id_for, document_id_for
        identity = uuid4()
        document = DocumentRecord(document_id=document_id_for(identity), visit_id=identity, attempt_id=attempt_id_for(identity, 0),
            observed_at=self.old, representation='response_body', detected_media_type='application/pdf',
            content_sha256=content_hash, content_bytes=3, object_key=key, storage_encoding='identity', stored_bytes=3)
        evidence = VisitEvidence(visit=VisitRecord(visit_id=identity, requested_url='https://example.com/',
            admitted_at=self.old, finished_at=self.old, outcome='succeeded', observed_at=self.old, document_id=document.document_id,
            capture_policy=capture_policy(), started_at=self.old), document=document, attempts=(AttemptRecord(attempt_id=attempt_id_for(identity, 0), visit_id=identity, attempt_index=0, started_at=self.old, finished_at=self.old, outcome="succeeded"),))
        self.service.record_visits([evidence])
        return evidence

    def test_shared_raw_bytes_wait_for_last_reference_and_snapshot_expiry(self):
        from io import BytesIO
        from periplus.ingestion.objects.store import FileObjectStore
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        objects = FileObjectStore(Path(tmp.name))
        first = self.document_visit()
        second = self.document_visit()
        key = first.document.object_key
        objects.put_if_absent(key, BytesIO(b'raw'))
        self.retention.purge_observation(next(item for item in self.candidates() if item.observation_id == first.visit.visit_id), now=self.old)
        self.assertEqual(self.object_count(), 0)
        self.retention.purge_observation(self.candidates()[0], now=self.old)
        kwargs = dict(now=self.now, grace_seconds=3600, content_hashes=('a'*64,), check_ownership=lambda:None)
        self.assertEqual(self.retention.reclaim_objects(objects, **kwargs), 0)
        self.assertTrue(objects.exists(key))
        # Simulate LakeDucktor, which is the sole production snapshot owner.
        self.catalogue.trusted_connection.execute("CALL ducklake_expire_snapshots('periplus', older_than => now())")
        self.assertEqual(self.retention.reclaim_objects(objects, **kwargs), 0)
        kwargs["now"] = self.now + timedelta(hours=2)
        self.assertEqual(self.retention.reclaim_objects(objects, **kwargs), 1)
        self.assertFalse(objects.exists(key))
        self.assertEqual(self.retention.reclaim_objects(objects, **kwargs), 0)

    def test_different_representations_of_shared_content_each_get_a_receipt(self):
        self.document_visit(key='raw/first')
        self.document_visit(key='raw/second')
        for item in self.candidates(): self.retention.purge_observation(item, now=self.old)
        self.assertEqual(self.object_count(), 2)

    def object_count(self):
        from sqlalchemy import select, func
        from periplus.retention.models import RetentionObjectRecord
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(RetentionObjectRecord))

    def test_materializer_reprepares_after_retirement_instead_of_failing_batch(self):
        from unittest.mock import patch
        from periplus.materialization.batch import prepare_batch
        from periplus.materialization.runtime import _commit_retained_batch
        from periplus.materialization.registry import PROJECTIONS
        visit = self.visit()
        run = SimpleNamespace(id=uuid4(), generation_tables={spec.name: spec.name for spec in PROJECTIONS})
        batch = SimpleNamespace(id=uuid4(), visit_ids=(str(visit.visit.visit_id),), snapshot=self.catalogue.latest_snapshot())
        calls = 0
        def prepare(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = prepare_batch(*args, **kwargs)
            if calls == 1:
                self.retention.purge_observation(self.candidates()[0], now=self.now)
            return result
        with patch('periplus.materialization.runtime.prepare_batch', side_effect=prepare):
            result = _commit_retained_batch(self.catalogue, SimpleNamespace(store=None), run, batch)
        self.assertEqual(calls, 2)
        self.assertEqual(result.source_items, 0)

    def test_content_projection_survives_until_last_document_in_all_generations(self):
        self.document_visit()
        self.document_visit()
        connection = self.catalogue.trusted_connection
        tables = ['html_nodes', '_periplus_rebuild_html_nodes_test', '_periplus_retired_html_nodes_test']
        for table in tables:
            if table != 'html_nodes':
                connection.execute(f'CREATE TABLE material.{table} AS SELECT * FROM material.html_nodes WHERE false')
            connection.execute(f'INSERT INTO material.{table} (content_sha256, node_index, subtree_end_index, sibling_index, node_type, depth) VALUES (?, 0, 1, 0, ?, 0)', ['a'*64, 'document'])
        connection.execute("INSERT INTO material.term VALUES ('monkeys', 1)")
        connection.execute("INSERT INTO material.content_posting VALUES (1, ?, 2)", ['a'*64])
        candidates = self.candidates()
        self.retention.purge_observation(candidates[0], now=self.now)
        self.assertEqual(connection.execute('SELECT count(*) FROM material.content_posting').fetchone()[0], 1)
        for table in tables:
            self.assertEqual(connection.execute(f'SELECT count(*) FROM material.{table}').fetchone()[0], 1)
        self.retention.purge_observation(candidates[1], now=self.now)
        self.assertEqual(connection.execute('SELECT count(*) FROM material.content_posting').fetchone()[0], 0)
        self.assertEqual(connection.execute('SELECT * FROM material.term').fetchall(), [('monkeys', 1)])
        for table in tables:
            self.assertEqual(connection.execute(f'SELECT count(*) FROM material.{table}').fetchone()[0], 0)

    def test_dry_run_reports_current_roots_without_retiring_anything(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        visit = self.visit()
        with patch('periplus.retention.runtime.catalogue_from_env', return_value=nullcontext(self.catalogue)), patch(
                'periplus.retention.runtime.current_roots', return_value=({visit.visit.visit_id}, set())):
            report = RetentionSweep(RetentionSettings(mode='dry_run'), None).run(now=self.now)
        self.assertEqual(report['blocked_current_observations'], 1)
        self.assertEqual(report['candidates'], [])
        self.assertEqual(len(self.candidates()), 1)
        with patch('periplus.retention.runtime.catalogue_from_env', return_value=nullcontext(self.catalogue)), patch(
                'periplus.retention.runtime.current_roots', return_value=(set(), set())):
            report = RetentionSweep(RetentionSettings(mode='dry_run'), None).run(now=self.now)
        self.assertEqual(report['candidates'], [str(visit.visit.visit_id)])
        self.assertFalse(retired('observation', str(visit.visit.visit_id)))

    def test_abandoned_publication_is_retired_before_claim_release(self):
        import asyncio
        import os
        from contextlib import asynccontextmanager, nullcontext
        from io import BytesIO
        from unittest.mock import patch
        from periplus.ingestion.objects.store import FileObjectStore
        from periplus.ingestion.objects.html import html_object_key
        from periplus.ingestion.objects.publication import claim, claim_key
        from periplus.retention.publications import cleanup_publications
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        objects = FileObjectStore(Path(tmp.name))
        identity, content_hash = uuid4(), 'b'*64
        claim(objects, content_hash, identity)
        objects.put_if_absent(html_object_key(content_hash), BytesIO(b'raw'))
        timestamp = (self.now-timedelta(days=400)).timestamp()
        os.utime(objects.root/claim_key(content_hash, identity), (timestamp, timestamp))
        @asynccontextmanager
        async def lease(*args, **kwargs):
            self.assertEqual(kwargs['phase'], 'ingestion')
            self.assertIn(f'observation:{identity}', args[1])
            yield SimpleNamespace(lost=False)
        settings = RetentionSettings(mode='purge')
        with patch('periplus.retention.publications.catalogue_from_env', return_value=nullcontext(self.catalogue)), patch(
                'periplus.retention.publications.current_roots', return_value=({identity}, set())), patch(
                'periplus.retention.publications.operation_leases', lease):
            asyncio.run(cleanup_publications(settings, None, objects, None))
        self.assertTrue(objects.exists(claim_key(content_hash, identity)))
        with patch('periplus.retention.publications.catalogue_from_env', return_value=nullcontext(self.catalogue)), patch(
                'periplus.retention.publications.current_roots', return_value=(set(), set())), patch(
                'periplus.retention.publications.operation_leases', lease):
            asyncio.run(cleanup_publications(settings, None, objects, None))
        self.assertTrue(retired('observation', str(identity)))
        self.assertFalse(objects.exists(claim_key(content_hash, identity)))
        self.assertTrue(objects.exists(html_object_key(content_hash)))
        self.assertEqual(self.object_count(), 1)
        with self.assertRaises(EvidenceRetired):
            self.service.record_visits([VisitEvidence(visit=VisitRecord(capture_policy=capture_policy(), visit_id=identity,
                requested_url='https://example.com/', admitted_at=self.old, finished_at=self.old, outcome='failed'), attempts=())])

    def test_new_publication_blocks_physical_deletion_after_snapshot_grace(self):
        from io import BytesIO
        from periplus.ingestion.objects.store import FileObjectStore
        from periplus.ingestion.objects.publication import claim, release
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        objects = FileObjectStore(Path(tmp.name))
        evidence = self.document_visit()
        key = evidence.document.object_key
        objects.put_if_absent(key, BytesIO(b'raw'))
        self.retention.purge_observation(self.candidates()[0], now=self.old)
        kwargs = dict(now=self.now, grace_seconds=3600, content_hashes=('a'*64,), check_ownership=lambda:None)
        self.retention.reclaim_objects(objects, **kwargs)
        self.catalogue.trusted_connection.execute("CALL ducklake_expire_snapshots('periplus', older_than => now())")
        self.retention.reclaim_objects(objects, **kwargs)
        kwargs['now'] = self.now + timedelta(hours=2)
        publisher = uuid4()
        claim(objects, 'a'*64, publisher)
        self.assertEqual(self.retention.reclaim_objects(objects, **kwargs), 0)
        self.assertTrue(objects.exists(key))
        release(objects, 'a'*64, publisher)
        self.assertEqual(self.retention.reclaim_objects(objects, **kwargs), 1)
