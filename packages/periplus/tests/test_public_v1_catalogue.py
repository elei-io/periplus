"""Execute the public v1 views and helper against real DuckDB fixtures."""
import unittest
from importlib.resources import files

import duckdb

from periplus.materialization.dom.nodes import parse_document
from periplus.query.service import QueryRequest
from periplus.query.validation import _bounded_query


class PublicV1CatalogueTests(unittest.TestCase):


    def test_capture_request_ids_preserve_capture_grain(self):
        from uuid import UUID

        db = duckdb.connect()
        self.addCleanup(db.close)
        db.execute("CREATE SCHEMA public_v1; CREATE SCHEMA ingest")
        db.execute("""CREATE TABLE ingest.visits (
            visit_id UUID, document_id UUID, requested_url VARCHAR,
            effective_url VARCHAR, observed_at TIMESTAMPTZ, status_code INTEGER)""")
        db.execute("""CREATE TABLE ingest.documents (
            document_id UUID, visit_id UUID, content_sha256 VARCHAR,
            content_bytes BIGINT, representation VARCHAR,
            detected_media_type VARCHAR, charset VARCHAR)""")
        db.execute("""CREATE TABLE ingest.fulfillments (
            collection_id UUID, observation_id UUID, requested_url VARCHAR)""")
        capture, pending, missing, first, second = [UUID(int=i) for i in range(1, 6)]
        for identity in (capture, pending, missing):
            db.execute("INSERT INTO ingest.visits VALUES (?, ?, 'https://example.com/', NULL, NULL, 200)",
                       [identity, identity])
        for identity in (capture, pending):
            db.execute("INSERT INTO ingest.documents VALUES (?, ?, 'hash', 10, 'html', 'text/html', 'utf-8')",
                       [identity, identity])
        db.executemany("INSERT INTO ingest.fulfillments VALUES (?, ?, ?)", [
            (second, capture, "https://example.com/"),
            (first, capture, "https://example.com/"),
            (first, capture, "https://example.com/alias"),
            (first, missing, "https://example.com/missing"),
        ])
        resource = files("periplus.platform.catalogue").joinpath(
            "sql/public_v1/views/capture.sql")
        db.execute(resource.read_text())
        self.assertEqual(db.execute(
            "SELECT capture_id, request_ids FROM public_v1.capture ORDER BY capture_id"
        ).fetchall(), [(capture, [first, second]), (pending, [])])
        self.assertEqual(db.execute(
            "SELECT capture_id FROM public_v1.capture WHERE list_contains(request_ids, ?::UUID)",
            [first],
        ).fetchall(), [(capture,)])
        self.assertEqual(db.execute("DESCRIBE public_v1.capture").fetchall()[-1][1], "UUID[]")
        # A later reuse updates membership without adding another capture row.
        db.execute("INSERT INTO ingest.fulfillments VALUES (?, ?, 'https://example.com/')", [second, pending])
        self.assertEqual(db.execute(
            "SELECT capture_id FROM public_v1.capture WHERE list_contains(request_ids, ?::UUID) ORDER BY capture_id",
            [second],
        ).fetchall(), [(capture,), (pending,)])

    def test_default_version_and_names(self):
        self.assertEqual(QueryRequest(sql='SELECT * FROM capture').schema_version, 'public_v1')
        for sql in ('SELECT * FROM capture', 'SELECT * FROM public_v1.html_element',
                    'WITH chosen AS (SELECT * FROM capture) SELECT * FROM chosen',
                    "SELECT text FROM html_element"):
            _bounded_query(sql)
        for sql in ('SELECT * FROM ingest.visits', 'SELECT * FROM web.observation',
                    'SELECT * FROM collection_capture', 'SELECT * FROM collection', 'SELECT * FROM fulfillment',
                    'SELECT * FROM acquisition_reason', 'SELECT * FROM object',
                    'SELECT * FROM duckdb_tables()', 'SELECT * FROM visits'):
            with self.assertRaises(ValueError):
                _bounded_query(sql)
        with self.assertRaises(ValueError):
            QueryRequest(sql='SELECT 1', schema_version='public_v2')
