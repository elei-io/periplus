"""The same six graph/content primitives in both independently installed schemas."""
import unittest
from uuid import UUID

from element_fixture import catalogue
from periplus.platform.catalogue.public import install_public_catalogue, validate_public_catalogue
from periplus.query.validation import _bounded_query


class PageGraphTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = catalogue()
        self.db = self.catalogue.connection
        self.addCleanup(self.db.close)
        for number, url, effective, media, status in (
            (1, 'https://a.test/', 'https://b.test/', 'text/html', 200),
            (2, 'https://a.test/', None, 'TEXT/HTML', 404),
            (3, 'https://mirror.test/', 'https://mirror.test/', 'text/html', 200),
            (4, 'https://pdf.test/', None, 'application/pdf', 200),
            (5, 'https://failed.test/', None, None, None),
        ):
            self.db.execute('''INSERT INTO ingest.visits
                (visit_id, document_id, requested_url, effective_url, status_code)
                VALUES (?, ?, ?, ?, ?)''', [UUID(int=number), UUID(int=number), url, effective, status])
            if media:
                self.db.execute('''INSERT INTO ingest.documents
                    (document_id, visit_id, content_sha256, content_bytes, detected_media_type, charset)
                    VALUES (?, ?, 'shared', 100, ?, 'utf-8')''', [UUID(int=number), UUID(int=number), media])
        for visit, node, target in ((1, 10, 'https://uncaptured.test/'), (1, 11, 'https://uncaptured.test/'),
                                    (2, 10, 'https://a.test/next'), (4, 1, 'https://excluded.test/')):
            self.db.execute('''INSERT INTO material.link_occurrences
                (visit_id, element_index, target_url, raw_href) VALUES (?, ?, ?, 'next')''',
                [UUID(int=visit), node, target])

    def test_identity_and_graph_traversal_in_both_schemas(self):
        for schema in ('public_v1', 'experimental'):
            with self.subTest(schema=schema):
                self.assertEqual(self.db.execute(f'SELECT url FROM {schema}.page ORDER BY url').fetchall(),
                    [(url,) for url in ('https://a.test/', 'https://a.test/next', 'https://b.test/',
                                       'https://mirror.test/', 'https://uncaptured.test/')])
                self.assertEqual(self.db.execute(f'''SELECT page_url, count(*), count(DISTINCT content_id)
                    FROM {schema}.capture GROUP BY page_url ORDER BY page_url''').fetchall(),
                    [('https://a.test/', 2, 1), ('https://mirror.test/', 1, 1)])
                rows = self.db.execute(f'''SELECT c.page_url, c.effective_url, p.url, count(destination.capture_id)
                    FROM {schema}.capture c JOIN {schema}.link l USING (capture_id)
                    JOIN {schema}.page p ON p.url=l.target_url
                    LEFT JOIN {schema}.capture destination ON destination.page_url=p.url
                    WHERE c.capture_id=? GROUP BY c.page_url,c.effective_url,p.url''', [UUID(int=1)]).fetchall()
                self.assertEqual(rows, [('https://a.test/', 'https://b.test/', 'https://uncaptured.test/', 0)])
                self.assertEqual(self.db.execute(f'SELECT count(*) FROM {schema}.link WHERE capture_id=?', [UUID(int=1)]).fetchone(), (2,))
                self.assertEqual(self.db.execute(f'SELECT http_status_code FROM {schema}.capture WHERE capture_id=?', [UUID(int=2)]).fetchone(), (404,))
                columns = [r[0] for r in self.db.execute(f'DESCRIBE {schema}.capture').fetchall()]
                self.assertEqual(columns, ['capture_id', 'page_url', 'effective_url', 'captured_at',
                                          'http_status_code', 'content_id', 'byte_length', 'encoding'])

    def test_replacement_removes_old_surface_and_keeps_private_postings(self):
        removed = ('html_term', 'html_heading', 'html_section', 'html_table', 'html_table_cell',
                   'html_image', 'html_code', 'html_list', 'html_list_item', 'html_form',
                   'html_form_control', 'html_select_option')
        self.db.execute("CREATE MACRO material.element_text_excluding(a,b,c) AS ''")
        for schema in ('public_v1', 'experimental'):
            for name in removed:
                self.db.execute(f'CREATE VIEW {schema}.{name} AS SELECT 1 AS obsolete')
        install_public_catalogue(self.catalogue)
        validate_public_catalogue(self.catalogue)
        for schema in ('public_v1', 'experimental'):
            names = {r[0] for r in self.db.execute('SELECT view_name FROM duckdb_views() WHERE schema_name=?', [schema]).fetchall()}
            self.assertEqual(names, {'page', 'capture', 'link', 'html_element', 'html_metadata', 'html_jsonld'})
            for name in (*removed, 'material.html_terms'):
                with self.assertRaises(ValueError):
                    _bounded_query(f'SELECT * FROM {name}', schema=schema)
            _bounded_query(f"SELECT * FROM {schema}.search(['robot'])", schema=schema)
        self.db.execute('SELECT * FROM material.html_terms LIMIT 0')
        self.assertEqual(self.db.execute("SELECT count(*) FROM duckdb_functions() WHERE schema_name='material' AND function_name='element_text_excluding'").fetchone(), (0,))
