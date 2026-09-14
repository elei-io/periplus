"""Execute the public v1 views and helper against real DuckDB fixtures."""
import unittest
from periplus.query.service import QueryRequest
from periplus.query.validation import _bounded_query


class PublicV1CatalogueTests(unittest.TestCase):


    def test_default_version_and_names(self):
        self.assertIsNone(QueryRequest(sql='SELECT * FROM capture').schema_version)
        for sql in ('SELECT * FROM capture', 'SELECT * FROM public_v1.html_element',
                    'WITH chosen AS (SELECT * FROM capture) SELECT * FROM chosen',
                    "SELECT text FROM html_element"):
            _bounded_query(sql)
        for sql in ('SELECT * FROM ingest.visits', 'SELECT * FROM web.observation',
                    'SELECT * FROM html_term', 'SELECT * FROM html_heading', 'SELECT * FROM html_table_cell',
                    'SELECT * FROM collection_capture', 'SELECT * FROM collection', 'SELECT * FROM fulfillment',
                    'SELECT * FROM acquisition_reason', 'SELECT * FROM object',
                    'SELECT * FROM duckdb_tables()', 'SELECT * FROM visits'):
            with self.assertRaises(ValueError):
                _bounded_query(sql)
        with self.assertRaises(ValueError):
            QueryRequest(sql='SELECT 1', schema_version='public_v2')
