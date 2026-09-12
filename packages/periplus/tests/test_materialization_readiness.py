from operational_state_fixture import operational_state
"""Real DuckLake proof membership and atomic materialization commit checks."""
from capture_policy_fixture import capture_policy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from periplus.materialization.batch import prepare_batch, commit_prepared_batch
from periplus.materialization.readiness import observation_readiness
from periplus.materialization.registry import PROJECTIONS, REGISTRY_DIGEST
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.records import VisitEvidence, VisitRecord
from periplus.platform.catalogue.service import CatalogueService


class MaterializationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.catalogue = Catalogue(CatalogueConfig('periplus', str(root/'metadata.duckdb'), str(root/'data'), 'ducklake'))
        self.addCleanup(self.catalogue.close)
        self.catalogue.bootstrap()
        self.now = datetime.now(UTC)
        self.identity, self.generation = uuid4(), uuid4()
        self.visit = VisitRecord(capture_policy=capture_policy(), visit_id=self.identity, requested_url='https://example.com/',
            admitted_at=self.now, finished_at=self.now, outcome='failed')
        CatalogueService(self.catalogue).record_visits([VisitEvidence(visit=self.visit, attempts=(), steps=())])
        self.connection = self.catalogue.trusted_connection

    def activate(self, digest=REGISTRY_DIGEST):
        from periplus.materialization.state import publish_generation
        publish_generation(SimpleNamespace(id=self.generation, batch_size=20, registry_digest=digest), self.catalogue.latest_snapshot())

    def proof(self, *, public_only=True):
        return observation_readiness(self.catalogue, [self.identity])[self.identity]

    def test_collection_proof_requires_outcome_fulfillments_and_atomic_materialization(self):
        from periplus.materialization.readiness import collection_readiness
        from periplus.platform.catalogue.lineage import CollectionDefinition, CollectionOutcome, FulfillmentRecord
        collection = uuid4()
        read = lambda: collection_readiness(self.catalogue, [collection])[collection]
        self.activate()
        service = CatalogueService(self.catalogue)
        service.record_lineage([CollectionDefinition(record_id=collection, collection_id=collection,
             recorded_at=self.now, specification={'request_class': 'public'})])
        self.assertEqual(read().reason, 'collection_outcome_not_verified')
        service.record_lineage([CollectionOutcome(record_id=collection, collection_id=collection,
             recorded_at=self.now, outcome='eligible_links_exhausted',
            consumed_pages=1, supplied_pages=0, failed_pages=1)])
        self.assertEqual(read().reason, 'collection_fulfillments_not_verified')
        service.record_lineage([FulfillmentRecord(record_id=uuid4(), collection_id=collection,
            observation_id=self.identity, requested_url='https://example.com/',
            recorded_at=self.now, depth=0, rule_id='seed', mode='acquired')])
        self.assertFalse(read().query_ready)
        self.assertEqual(read().reason, 'materialization_pending')
        run = SimpleNamespace(id=self.generation, generation_tables={spec.name: spec.name for spec in PROJECTIONS})
        batch = SimpleNamespace(id=uuid4(), visit_ids=(str(self.identity),), snapshot=self.catalogue.latest_snapshot())
        prepared = prepare_batch(self.catalogue, SimpleNamespace(store=None), run, batch)
        commit_prepared_batch(self.catalogue, run, batch, prepared, active_generation=True)
        self.assertTrue(read().query_ready)
        self.assertEqual(read().generation_id, self.generation)
        self.activate('other')
        self.assertIsNone(read().query_ready)
        self.assertEqual(read().reason, 'active_generation_registry_mismatch')

    def test_empty_admin_collection_readiness_is_shared(self):
        from periplus.materialization.readiness import collection_readiness
        from periplus.platform.catalogue.lineage import CollectionDefinition, CollectionOutcome
        collection = uuid4()
        self.activate()
        CatalogueService(self.catalogue).record_lineage([
            CollectionDefinition(record_id=collection, collection_id=collection,
                recorded_at=self.now, specification={'request_class': 'admin'}),
            CollectionOutcome(record_id=collection, collection_id=collection,
                recorded_at=self.now, outcome='cancelled', consumed_pages=0, supplied_pages=0, failed_pages=0)])
        hidden = collection_readiness(self.catalogue, [collection])[collection]
        self.assertTrue(hidden.query_ready)
        self.assertEqual(hidden.generation_id, self.generation)
        self.assertTrue(collection_readiness(self.catalogue, [collection])[collection].query_ready)
        with self.assertRaises(ValueError):
            collection_readiness(self.catalogue, [uuid4() for _ in range(101)])

    def test_proof_and_applied_marker_commit_together_even_for_a_visit_without_content(self):
        self.assertEqual(self.proof().reason, 'active_generation_unavailable')
        self.activate()
        self.assertFalse(self.proof().query_ready)
        run = SimpleNamespace(id=self.generation, generation_tables={spec.name: spec.name for spec in PROJECTIONS})
        batch = SimpleNamespace(id=uuid4(), visit_ids=(str(self.identity),), snapshot=self.catalogue.latest_snapshot())
        prepared = prepare_batch(self.catalogue, SimpleNamespace(store=None), run, batch)
        self.assertEqual(prepared.source_items, 1)
        self.assertEqual(prepared.output_rows, 1)
        from periplus.platform.catalogue.exceptions import CatalogueOutcomePending
        from periplus.retention.models import LakeWriteClaimRecord
        from sqlalchemy import delete
        with patch('periplus.materialization.state.record_applied', side_effect=RuntimeError('injected marker failure')):
            with self.assertRaises(CatalogueOutcomePending):
                commit_prepared_batch(self.catalogue, run, batch, prepared, active_generation=True)
        # The lake commit survived; a missing Postgres receipt cannot duplicate it.
        self.assertEqual(self.connection.execute('SELECT count(*) FROM material.visit_readiness').fetchone()[0], 1)
        with self.sessions.begin() as session:
            session.execute(delete(LakeWriteClaimRecord))  # simulate safe claim expiry
        commit_prepared_batch(self.catalogue, run, batch, prepared, active_generation=True)
        self.assertTrue(self.proof().query_ready)
        self.assertEqual(self.proof().generation_id, self.generation)
        self.assertEqual(self.proof().reason, 'active_generation_committed')
        from periplus.platform.catalogue.lineage import CollectionDefinition, FulfillmentRecord
        from periplus.crawl.control.collections.arrivals import read_arrivals
        collection = uuid4()
        CatalogueService(self.catalogue).record_lineage([
            CollectionDefinition(record_id=collection, collection_id=collection,
                recorded_at=self.now, specification={'request_class': 'public'}),
            FulfillmentRecord(record_id=uuid4(), collection_id=collection, observation_id=self.identity,
                requested_url='https://example.com/',  recorded_at=self.now,
                depth=0, rule_id='seed', mode='acquired'),
        ])
        arrival = read_arrivals(self.catalogue, collection,  limit=1, cursor=None).items[0]
        self.assertTrue(arrival.query_ready)
        self.assertEqual(arrival.query_readiness_reason, 'active_generation_committed')

        replay = commit_prepared_batch(self.catalogue, run, batch, prepared, active_generation=True)
        self.assertTrue(replay.already_applied)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM material.visit_readiness').fetchone()[0], 1)

    def test_shared_html_waits_for_the_content_owning_batch(self):
        from periplus.ingestion.objects.html import RawHtmlRepository
        from periplus.ingestion.objects.store import FileObjectStore
        from periplus.platform.catalogue.records import DocumentRecord, AttemptRecord, attempt_id_for, document_id_for
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = RawHtmlRepository(FileObjectStore(Path(temporary.name)))
        identities = sorted([uuid4(), uuid4()], key=lambda value: str(document_id_for(value)))
        for identity in identities:
            stored = repository.put('<html><body><a href="/next">Next</a></body></html>',
                source_url='https://example.com/', visit_id=identity,
                observed_at=self.now, content_type='text/html')
            document_id = document_id_for(identity)
            visit = VisitRecord(visit_id=identity, requested_url='https://example.com/',
                admitted_at=self.now, observed_at=self.now, finished_at=self.now, outcome='succeeded',
                document_id=document_id, capture_policy=capture_policy(), started_at=self.now)
            document = DocumentRecord(document_id=document_id, visit_id=identity, attempt_id=attempt_id_for(identity, 0), observed_at=self.now,
                representation='rendered_html', detected_media_type='text/html',
                content_sha256=stored.sha256, content_bytes=stored.size_bytes,
                object_key=stored.object_key, storage_encoding=stored.compression,
                stored_bytes=stored.compressed_size_bytes)
            CatalogueService(self.catalogue).record_visits([VisitEvidence(visit=visit, document=document, attempts=(AttemptRecord(attempt_id=attempt_id_for(identity, 0), visit_id=identity, attempt_index=0, started_at=self.now, finished_at=self.now, outcome="succeeded"),))])
        self.activate()
        run = SimpleNamespace(id=self.generation, generation_tables={spec.name: spec.name for spec in PROJECTIONS})
        snapshot = self.catalogue.latest_snapshot()
        batches = [SimpleNamespace(id=uuid4(), visit_ids=(str(identity),), snapshot=snapshot) for identity in identities]
        prepared = [prepare_batch(self.catalogue, repository, run, batch) for batch in batches]
        from periplus.materialization.readiness import collection_readiness
        from periplus.platform.catalogue.lineage import CollectionDefinition, CollectionOutcome, FulfillmentRecord
        collection = uuid4()
        CatalogueService(self.catalogue).record_lineage([
            CollectionDefinition(record_id=collection, collection_id=collection,
                recorded_at=self.now, specification={'request_class': 'public'}),
            CollectionOutcome(record_id=collection, collection_id=collection,
                recorded_at=self.now, outcome='budget_reached', consumed_pages=1, supplied_pages=1, failed_pages=0),
            FulfillmentRecord(record_id=uuid4(), collection_id=collection, observation_id=identities[1],
                requested_url='https://example.com/',  recorded_at=self.now,
                depth=0, rule_id='seed', mode='shared')])
        # Commit the non-owner first: its visit and links are complete, shared DOM is not.
        commit_prepared_batch(self.catalogue, run, batches[1], prepared[1], active_generation=True)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM material.visit_readiness').fetchone()[0], 1)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM material.html_elements').fetchone()[0], 0)
        proofs = observation_readiness(self.catalogue, identities)
        self.assertFalse(proofs[identities[1]].query_ready)
        self.assertFalse(collection_readiness(self.catalogue, [collection])[collection].query_ready)
        commit_prepared_batch(self.catalogue, run, batches[0], prepared[0], active_generation=True)
        self.assertTrue(all(value.query_ready for value in observation_readiness(self.catalogue, identities).values()))
        self.assertTrue(collection_readiness(self.catalogue, [collection])[collection].query_ready)

    def test_live_reports_verified_pending_and_unknown_readiness(self):
        from periplus.crawl.runtime.live import read_live_history
        identity = uuid4()
        visit = VisitRecord(capture_policy=capture_policy(), visit_id=identity, requested_url='https://example.com/live',
            admitted_at=self.now, finished_at=self.now, outcome='succeeded')
        CatalogueService(self.catalogue).record_visits([VisitEvidence(visit=visit, attempts=())])
        def recent():
            return read_live_history(self.catalogue, now=self.now).recent[0]
        self.assertIsNone(recent().query_ready)
        self.activate()
        self.assertFalse(recent().query_ready)
        run = SimpleNamespace(id=self.generation, generation_tables={spec.name: spec.name for spec in PROJECTIONS})
        batch = SimpleNamespace(id=uuid4(), visit_ids=(str(identity),), snapshot=self.catalogue.latest_snapshot())
        prepared = prepare_batch(self.catalogue, SimpleNamespace(store=None), run, batch)
        commit_prepared_batch(self.catalogue, run, batch, prepared, active_generation=True)
        self.assertTrue(recent().query_ready)
        self.assertEqual(recent().query_readiness_reason, 'active_generation_committed')

    def test_hidden_or_mismatched_generation_never_claims_readiness(self):
        self.activate()
        self.connection.execute('CREATE TABLE material._hidden_readiness AS SELECT ?::UUID visit_id, ?::TIMESTAMPTZ finished_at', [self.identity, self.now])
        self.assertFalse(self.proof().query_ready)
        self.connection.execute('INSERT INTO material.visit_readiness VALUES (?, ?)', [self.identity, self.now])
        self.assertTrue(self.proof().query_ready)
        self.activate('old-registry')
        self.assertIsNone(self.proof().query_ready)
        self.assertEqual(self.proof().reason, 'active_generation_registry_mismatch')
        self.activate()
        self.assertTrue(self.proof().query_ready)

    def test_duplicate_state_or_proof_and_oversized_requests_fail_closed(self):
        self.activate()
        self.connection.execute('INSERT INTO material.visit_readiness VALUES (?, ?), (?, ?)',
            [self.identity, self.now, self.identity, self.now])
        self.assertEqual(self.proof().reason, 'readiness_state_inconsistent')
        with self.assertRaises(ValueError):
            observation_readiness(self.catalogue, [uuid4() for _ in range(101)])

    def test_activation_recovers_a_lost_postgres_acknowledgement(self):
        from contextlib import nullcontext
        from sqlalchemy import delete
        from periplus.retention.models import LakeWriteClaimRecord
        from periplus.materialization import state
        from periplus.materialization.runtime import _activate_or_catch_up
        from periplus.materialization.runtime import generation_table
        run = SimpleNamespace(id=uuid4(), batch_size=50, registry_digest=REGISTRY_DIGEST)
        run.generation_tables = {spec.name: generation_table(spec.name, run.id) for spec in PROJECTIONS}
        for spec in PROJECTIONS:
            self.catalogue.create_materialization_generation(spec.relation, run.generation_tables[spec.name])
        run.covered_snapshot = self.catalogue.latest_snapshot()
        with patch('periplus.materialization.runtime.catalogue_from_env', return_value=nullcontext(self.catalogue)):
            with patch.object(state, 'publish_generation', side_effect=RuntimeError('lost ack')):
                with self.assertRaisesRegex(RuntimeError, 'lost ack'):
                    _activate_or_catch_up(run)
            self.assertIsNone(state.active_generation())
            with self.sessions.begin() as session:
                session.execute(delete(LakeWriteClaimRecord))  # simulate safe claim expiry
            _activate_or_catch_up(run)
        self.assertEqual(state.active_generation().id, run.id)
        names = {row[0] for row in self.connection.execute("SELECT table_name FROM duckdb_tables() WHERE schema_name='material'").fetchall()}
        self.assertTrue(all(name not in names for name in run.generation_tables.values()))
        self.assertFalse(any(name in names for name in ['_periplus_retention_identities', '_periplus_retention_objects',
            '_periplus_applied_batches', '_periplus_materialization_state']))
