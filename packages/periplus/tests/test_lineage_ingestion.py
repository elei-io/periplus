"""Real DuckLake append/replay checks for late collection fulfillment."""
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from periplus.platform.catalogue.client import Catalogue, _column_type
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.exceptions import CatalogueConflictError
from periplus.platform.catalogue.lineage import CollectionDefinition, CollectionOutcome, FulfillmentRecord
from periplus.platform.catalogue.physical.lineage import TABLE_COLUMNS
from periplus.platform.catalogue.service import CatalogueService
from periplus.ingestion.queue import IngestionJob, lineage_ingestion_job


class LineageIngestionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.catalogue = Catalogue(CatalogueConfig(
            "periplus", str(root / "metadata.duckdb"), str(root / "data"), "ducklake", "",
        ))
        self.addCleanup(self.catalogue.close)
        self.catalogue.trusted_remote_execute("CREATE SCHEMA ingest")
        for relation, columns in TABLE_COLUMNS.items():
            definitions = ", ".join(f'"{name}" {_column_type(column)}' for name, column in columns.items())
            self.catalogue.trusted_remote_execute(f"CREATE TABLE {relation.qualified} ({definitions})")
        self.service = CatalogueService(self.catalogue)
        self.identity = uuid4()
        self.now = datetime.now(UTC)
        self.definition = CollectionDefinition(
            record_id=self.identity, collection_id=self.identity, visibility="public",
            recorded_at=self.now, specification={"page_limit": 2, "follow_sql": "SELECT url"},
        )

    def test_definition_and_terminal_outcome_have_independent_stable_jobs(self):
        terminal = CollectionOutcome(
            record_id=self.identity, collection_id=self.identity, visibility="public",
            recorded_at=self.now, outcome="eligible_links_exhausted",
            consumed_pages=1, supplied_pages=1, failed_pages=0,
            seed_provenance={"source_snapshot": "17", "source_query_id": "query-1",
                             "selected_at": self.now, "candidates_sha256": "a" * 64,
                             "discovery": {"model": "model-1", "queries": ["Robots"]}},
        )
        jobs = [lineage_ingestion_job(value) for value in (self.definition, terminal)]
        self.assertNotEqual(jobs[0].request_id, jobs[1].request_id)
        self.assertEqual(IngestionJob.model_validate_json(jobs[0].model_dump_json()), jobs[0])
        results = self.service.record_lineage([self.definition, terminal])
        self.assertTrue(all(result.created for result in results))
        self.assertTrue(all(not result.created for result in self.service.record_lineage([self.definition, terminal])))
        self.assertEqual(self.service.get_lineage("collection", self.identity), self.definition)
        self.assertEqual(self.service.get_lineage("collection_outcome", self.identity), terminal)

    def test_late_fulfillments_share_observation_without_reinserting_it(self):
        observation = uuid4()
        records = [FulfillmentRecord(
            record_id=uuid4(), collection_id=uuid4(), observation_id=observation,
            requested_url="https://example.com/", rule_id="seed", depth=0,
            mode=mode, visibility="public", recorded_at=self.now,
        ) for mode in ("acquired", "reused")]
        self.service.record_lineage([records[0]])
        self.service.record_lineage([records[1]])
        rows = self.catalogue.trusted_remote_rows(
            "SELECT count(*), count(DISTINCT observation_id) FROM ingest.fulfillments")
        self.assertEqual(rows, [(2, 1)])
        conflicting = records[1].model_copy(update={"mode": "shared"})
        with self.assertRaises(CatalogueConflictError):
            self.service.record_lineage([conflicting])
        self.assertEqual(self.service.get_lineage("fulfillment", records[1].record_id), records[1])

    def test_public_lineage_hides_private_rows(self):
        from importlib.resources import files
        records = [FulfillmentRecord(
            record_id=uuid4(), collection_id=uuid4(), observation_id=uuid4(),
            requested_url=f"https://{visibility}.example/", rule_id="seed", depth=0,
            mode="acquired", visibility=visibility, recorded_at=self.now,
        ) for visibility in ("public", "private")]
        self.service.record_lineage(records)
        self.catalogue.trusted_remote_execute("CREATE SCHEMA web")
        sql = files("periplus.platform.catalogue").joinpath("sql/web/views/004_fulfillment.sql").read_text()
        self.catalogue.trusted_remote_execute(sql)
        rows = self.catalogue.trusted_remote_rows("SELECT requested_url FROM web.fulfillment")
        self.assertEqual(rows, [("https://public.example/",)])

    def test_attempt_usage_survives_lake_commit_and_exact_replay(self):
        from periplus.platform.catalogue.physical.ingest import TABLE_COLUMNS as INGEST_COLUMNS
        from periplus.platform.catalogue.records import (
            AttemptRecord, AttemptUsage, VisitEvidence, VisitRecord, attempt_id_for,
        )
        for relation, columns in INGEST_COLUMNS.items():
            definitions = ", ".join(f'"{name}" {_column_type(column)}' for name, column in columns.items())
            self.catalogue.trusted_remote_execute(f"CREATE TABLE {relation.qualified} ({definitions})")
        for index, measured in enumerate((None, 731)):
            identity = uuid4()
            attempt = AttemptRecord(
                attempt_id=attempt_id_for(identity, 0), visit_id=identity, attempt_index=0,
                started_at=self.now, finished_at=None if measured is None else self.now,
                outcome="uncertain" if measured is None else "succeeded",
                resource_usage=AttemptUsage(policy_version=3, reserved_ms=125000, measured_ms=measured,
                    exclusion_policy_version=4, domain_policy={"id": str(uuid4()), "slug": "domain", "host_match": "*",
                    "maximum_concurrency": 2, "minimum_request_interval_seconds": 1.5, "version": 7, "updated_by": "admin"}),
            )
            evidence = VisitEvidence(visit=VisitRecord(
                visit_id=identity, visibility="private", requested_url=f"https://example.com/{index}",
                admitted_at=self.now, started_at=self.now, finished_at=self.now,
                outcome="failed" if measured is None else "succeeded",
            ), attempts=(attempt,))
            self.assertTrue(self.service.record_visits([evidence])[0].created)
            self.assertFalse(self.service.record_visits([evidence])[0].created)
            restored = self.service.get_visit_evidence([identity])[identity]
            self.assertEqual(restored, evidence)
            self.assertEqual(restored.attempts[0].resource_usage.charged_ms, 125000 if measured is None else measured)

    def test_background_reason_retains_historical_selection_provenance(self):
        from periplus.platform.catalogue.lineage import AcquisitionReason
        reason = AcquisitionReason(
            record_id=uuid4(), observation_id=uuid4(), parent_observation_id=uuid4(),
            recorded_at=self.now, visibility="public", reason="background", policy_version="2",
            rule_id="background-unseen-v1", selection_provenance={
                "policy_version": 1, "source_snapshot": 7, "source_query_id": "historical-query",
            },
        )
        self.assertTrue(self.service.record_lineage([reason])[0].created)
        self.assertFalse(self.service.record_lineage([reason])[0].created)
        self.assertEqual(self.service.get_lineage("acquisition_reason", reason.record_id), reason)
        with self.assertRaises(CatalogueConflictError):
            self.service.record_lineage([reason.model_copy(update={"rule_id": "different-rule"})])

    def test_historical_collection_read_preserves_private_boundary_and_partial_arrival(self):
        from periplus.crawl.control.collections.history import read_collection
        from periplus.crawl.control.collections.schemas import CollectionSpec
        self.definition = self.definition.model_copy(update={"specification": CollectionSpec(seed_urls=("https://example.com/",)).model_dump(mode="json")})
        self.service.record_lineage([self.definition])
        partial = read_collection(self.catalogue, self.identity, public_only=True)
        self.assertEqual(partial.source, "history")
        self.assertIsNone(partial.outcome)
        self.assertIsNone(partial.consumed_pages)
        terminal = CollectionOutcome(record_id=self.identity, collection_id=self.identity,
            visibility="public", recorded_at=self.now, outcome="eligible_links_exhausted",
            consumed_pages=2, supplied_pages=1, failed_pages=1)
        self.service.record_lineage([terminal])
        historical = read_collection(self.catalogue, self.identity, public_only=True)
        self.assertEqual((historical.consumed_pages, historical.supplied_pages, historical.failed_pages), (2, 1, 1))
        self.assertIsNone(historical.query_ready)
        private = uuid4()
        self.service.record_lineage([CollectionDefinition(record_id=private, collection_id=private,
            visibility="private", recorded_at=self.now,
            specification=CollectionSpec(seed_urls=("https://private.example/",), visibility="private").model_dump(mode="json"))])
        self.assertIsNone(read_collection(self.catalogue, private, public_only=True))
        self.assertEqual(read_collection(self.catalogue, private, public_only=False).specification.visibility, "private")
        self.assertIsNone(read_collection(self.catalogue, uuid4(), public_only=False))

    def test_history_does_not_trust_specification_to_override_private_lineage_visibility(self):
        from periplus.crawl.control.collections.history import read_collection
        from periplus.crawl.control.collections.schemas import CollectionSpec
        self.service.record_lineage([self.definition.model_copy(update={
            "visibility": "private", "specification": CollectionSpec(seed_urls=("https://private.example/",)).model_dump(mode="json"),
        })])
        self.assertIsNone(read_collection(self.catalogue, self.identity, public_only=True))
        with self.assertRaisesRegex(ValueError, "visibility"):
            read_collection(self.catalogue, self.identity, public_only=False)

    def test_history_pages_filter_before_limit_and_use_stable_tie_order(self):
        from uuid import UUID
        from periplus.crawl.control.collections.history import decode_cursor, read_history_page
        records = [CollectionDefinition(record_id=UUID(int=i), collection_id=UUID(int=i),
            visibility="private" if i == 4 else "public", recorded_at=self.now,
            specification={"seed_description": f"Collection {i}", "visibility": "private" if i == 4 else "public"})
            for i in range(1, 5)]
        self.service.record_lineage(records)
        first = read_history_page(self.catalogue, public_only=True, limit=2, cursor=None)
        self.assertEqual([item.id.int for item in first.items], [3, 2])
        self.assertEqual(first.items[0].summary, "Collection 3")
        self.assertIsNone(first.items[0].consumed_pages)
        second = read_history_page(self.catalogue, public_only=True, limit=2, cursor=decode_cursor(first.next_cursor))
        self.assertEqual([item.id.int for item in second.items], [1])
        self.assertIsNone(second.next_cursor)
        admin = read_history_page(self.catalogue, public_only=False, limit=2, cursor=None)
        self.assertEqual([item.id.int for item in admin.items], [4, 3])
        self.assertEqual(admin.items[0].visibility, "private")

    def test_history_page_rejects_inconsistent_visibility(self):
        from periplus.crawl.control.collections.history import read_history_page
        self.service.record_lineage([self.definition.model_copy(update={
            "specification": {"visibility": "private", "seed_description": "Private intent"},
        })])
        with self.assertRaisesRegex(ValueError, "visibility"):
            read_history_page(self.catalogue, public_only=True, limit=20, cursor=None)
    def test_history_page_rejects_oversized_intent(self):
        from periplus.crawl.control.collections.history import read_history_page
        private = uuid4()
        self.service.record_lineage([CollectionDefinition(record_id=private, collection_id=private,
            visibility="private", recorded_at=self.now,
            specification={"seed_description": "x" * 1048577})])
        with self.assertRaisesRegex(ValueError, "oversized"):
            read_history_page(self.catalogue, public_only=False, limit=20, cursor=None)

    def test_arrivals_survive_without_operational_state_and_observe_late_base_commits(self):
        from periplus.crawl.control.collections.arrivals import read_arrivals, decode_arrival_cursor
        self.catalogue.trusted_remote_execute("""CREATE TABLE ingest.visits (
            visit_id UUID, effective_url VARCHAR, observed_at TIMESTAMPTZ,
            outcome VARCHAR, status_code INTEGER, visibility VARCHAR)""")
        self.service.record_lineage([self.definition])
        observations = [uuid4() for _ in range(3)]
        records = [FulfillmentRecord(record_id=uuid4(), collection_id=self.identity,
            observation_id=observation, requested_url=f"https://example.com/{index}",
            rule_id="seed", depth=0, mode="reused", visibility="public", recorded_at=self.now)
            for index, observation in enumerate(observations)]
        self.service.record_lineage(records)
        first = read_arrivals(self.catalogue, self.identity, public_only=True, limit=2, cursor=None)
        self.assertTrue(first.definition_committed)
        self.assertEqual(len(first.items), 2)
        self.assertTrue(all(not item.observation_committed for item in first.items))
        second = read_arrivals(self.catalogue, self.identity, public_only=True, limit=2,
                              cursor=decode_arrival_cursor(first.next_cursor, self.identity))
        self.assertEqual(len(second.items), 1)
        self.assertIsNone(second.next_cursor)
        self.assertEqual(len({item.fulfillment_id for item in first.items + second.items}), 3)
        observation = first.items[0].observation_id
        self.catalogue.trusted_connection.execute("""INSERT INTO ingest.visits VALUES
            (?, 'https://example.com/final', ?, 'succeeded', 200, 'public')""", [observation, self.now])
        refreshed = read_arrivals(self.catalogue, self.identity, public_only=True, limit=2, cursor=None)
        self.assertTrue(refreshed.items[0].observation_committed)
        self.assertEqual(refreshed.items[0].http_status_code, 200)
        self.assertIsNone(refreshed.items[0].query_ready)
        self.assertEqual(refreshed.items[0].query_readiness_reason, "readiness_projection_not_installed")

    def test_arrival_visibility_applies_before_pagination_and_private_observation_join(self):
        from periplus.crawl.control.collections.arrivals import read_arrivals
        self.catalogue.trusted_remote_execute("""CREATE TABLE ingest.visits (
            visit_id UUID, effective_url VARCHAR, observed_at TIMESTAMPTZ,
            outcome VARCHAR, status_code INTEGER, visibility VARCHAR)""")
        self.service.record_lineage([self.definition])
        public_observation, private_observation = uuid4(), uuid4()
        records = [FulfillmentRecord(record_id=uuid4(), collection_id=self.identity,
            observation_id=observation, requested_url=f"https://{visibility}.example/",
            rule_id="seed", depth=0, mode="acquired", visibility=visibility, recorded_at=self.now)
            for observation, visibility in [(public_observation, "public"), (private_observation, "private")]]
        self.service.record_lineage(records)
        page = read_arrivals(self.catalogue, self.identity, public_only=True, limit=1, cursor=None)
        self.assertEqual(page.items[0].requested_url, "https://public.example/")
        self.assertIsNone(page.next_cursor)
        # A conflicting private base observation must not leak through a public fulfillment.
        self.catalogue.trusted_connection.execute("""INSERT INTO ingest.visits VALUES
            (?, 'https://secret.example/', ?, 'succeeded', 200, 'private')""", [public_observation, self.now])
        self.assertEqual(read_arrivals(self.catalogue, self.identity, public_only=True, limit=1, cursor=None).items, [])
        private = uuid4()
        self.service.record_lineage([CollectionDefinition(record_id=private, collection_id=private,
            visibility="private", recorded_at=self.now, specification={})])
        self.assertIsNone(read_arrivals(self.catalogue, private, public_only=True, limit=1, cursor=None))
        self.assertIsNotNone(read_arrivals(self.catalogue, private, public_only=False, limit=1, cursor=None))
        inconsistent = uuid4()
        self.service.record_lineage([CollectionDefinition(record_id=inconsistent, collection_id=inconsistent,
            visibility="public", recorded_at=self.now, specification={"visibility": "private"})])
        with self.assertRaises(ValueError):
            read_arrivals(self.catalogue, inconsistent, public_only=True, limit=1, cursor=None)


    def test_arrival_reader_rejects_oversized_evidence(self):
        from periplus.crawl.control.collections.arrivals import read_arrivals
        self.catalogue.trusted_remote_execute("""CREATE TABLE ingest.visits (
            visit_id UUID, effective_url VARCHAR, observed_at TIMESTAMPTZ,
            outcome VARCHAR, status_code INTEGER, visibility VARCHAR)""")
        self.service.record_lineage([self.definition, FulfillmentRecord(record_id=uuid4(),
            collection_id=self.identity, observation_id=uuid4(), requested_url="https://example.com/" + "x" * 8192,
            rule_id="seed", depth=0, mode="acquired", visibility="public", recorded_at=self.now)])
        with self.assertRaises(ValueError):
            read_arrivals(self.catalogue, self.identity, public_only=True, limit=1, cursor=None)

    def test_reverse_lineage_pages_keep_capture_cause_separate_from_later_reuse(self):
        from periplus.crawl.control.collections.lineage import read_observation_lineage, decode_lineage_cursor
        from periplus.platform.catalogue.lineage import AcquisitionReason
        self.catalogue.trusted_remote_execute("CREATE TABLE ingest.visits (visit_id UUID, requested_url VARCHAR, visibility VARCHAR)")
        observation, parent, reused = uuid4(), uuid4(), uuid4()
        self.catalogue.trusted_connection.execute("INSERT INTO ingest.visits VALUES (?, 'https://example.com/', 'public'), (?, 'https://example.com/parent', 'public')", [observation, parent])
        self.service.record_lineage([self.definition, self.definition.model_copy(update={"record_id": reused, "collection_id": reused}),
            AcquisitionReason(record_id=uuid4(), observation_id=observation, parent_observation_id=parent,
                recorded_at=self.now, visibility="public", reason="background", policy_version="2", rule_id="background-unseen-v1",
                selection_provenance={"policy_version": 1, "source_snapshot": 7, "source_query_id": "historical-query"}),
            *[FulfillmentRecord(record_id=uuid4(), collection_id=collection, observation_id=observation,
                requested_url="https://example.com/", rule_id="seed", depth=0, mode=mode,
                visibility="public", recorded_at=self.now) for collection, mode in [(self.identity, "shared"), (reused, "reused")]]])
        first = read_observation_lineage(self.catalogue, observation, public_only=True, limit=2, cursor=None)
        self.assertEqual(first.requested_url, "https://example.com/")
        self.assertEqual(len(first.items), 2)
        second = read_observation_lineage(self.catalogue, observation, public_only=True, limit=2,
            cursor=decode_lineage_cursor(first.next_cursor, observation, public_only=True))
        self.assertIsNone(second.next_cursor)
        items = first.items + second.items
        self.assertEqual(len({(item.record_id, item.kind) for item in items}), 3)
        reasons = [item for item in items if item.kind == "reason"]
        self.assertEqual(len(reasons), 1)
        self.assertEqual((reasons[0].reason, reasons[0].collection_id, reasons[0].parent_observation_id), ("background", None, parent))
        self.assertEqual({item.mode for item in items if item.kind == "fulfillment"}, {"shared", "reused"})
        # No control Postgres exists in this fixture: all provenance is historical.
        self.assertEqual(first.completeness, "committed_visible_evidence_only_ingestion_may_lag")

    def test_reverse_lineage_filters_private_evidence_and_waits_for_definition_commit(self):
        from periplus.crawl.control.collections.lineage import read_observation_lineage
        self.catalogue.trusted_remote_execute("CREATE TABLE ingest.visits (visit_id UUID, requested_url VARCHAR, visibility VARCHAR)")
        observation, private, parent = uuid4(), uuid4(), uuid4()
        self.catalogue.trusted_connection.execute("INSERT INTO ingest.visits VALUES (?, 'https://example.com/', 'public'), (?, 'https://secret.example/', 'private'), (?, 'https://parent.secret/', 'private')", [observation, private, parent])
        fulfillment = FulfillmentRecord(record_id=uuid4(), collection_id=self.identity, observation_id=observation,
            parent_observation_id=parent, requested_url="https://example.com/", rule_id="follow", depth=1,
            mode="acquired", visibility="public", recorded_at=self.now)
        self.service.record_lineage([fulfillment, fulfillment.model_copy(update={"record_id": uuid4(), "visibility": "private"})])
        read = lambda identity, public=True: read_observation_lineage(self.catalogue, identity, public_only=public, limit=1, cursor=None)
        self.assertEqual(read(observation).items, [])
        self.service.record_lineage([self.definition])
        page = read(observation)
        self.assertEqual(len(page.items), 1)
        self.assertIsNone(page.items[0].parent_observation_id)
        self.assertIsNone(page.next_cursor)
        self.assertIsNone(read(private))
        self.assertEqual(read(private, False).requested_url, "https://secret.example/")
        self.assertIsNone(read(uuid4()))
