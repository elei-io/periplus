"""No-false-negative candidate restriction on real ICU page words."""
import unittest
from unittest.mock import patch

from element_fixture import catalogue
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME
from periplus.query.text_index import exact_text_anchor, text_index_rewrite
from periplus.query.validation import _one_statement


class TextIndexTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = catalogue()
        self.addCleanup(self.catalogue.connection.close)
        self.db = self.catalogue.connection
        self.db.execute("SET schema='experimental'")
        pages = {
            'a': '<p>The Requiem Red</p><p>The Requiem Red</p>',
            'b': '<p>prefix<span>The Requiem Red</span>suffix</p>',
            'c': '<p>the requiem red</p><p>The Requiem Green</p>',
            'd': '<p>cat<span>fish</span></p>',
            "quote'content": '<p>The Requiem Red</p>',
            'unicode': '<p>é<span>The REQUIEM Red</span>字</p><p> Straße </p><p>😀 Requiem 字</p>',
        }
        parsed = {key: parse_document(html) for key, html in pages.items()}
        context = VisitBatchContext((), (), (), {k: v[1] for k,v in parsed.items()},
                                    {k: v[0] for k,v in parsed.items()}, {}, frozenset(parsed))
        for name in ('html_elements', 'html_terms'):
            self.db.register('projection_rows', BY_NAME[name].rows(context))
            self.db.execute(f'INSERT INTO material.{name} SELECT * FROM projection_rows')
            self.db.unregister('projection_rows')

    def rewrite(self, sql):
        return text_index_rewrite(self.db, _one_statement(sql), 'memory')

    def test_exact_results_multiplicity_and_types_are_preserved(self):
        for sql in (
            "SELECT content_id,node_index,text FROM html_element WHERE text='The Requiem Red' ORDER BY content_id,node_index",
            "SELECT content_id,node_index FROM html_element WHERE text='😀 Requiem 字'",
            "SELECT content_id,node_index FROM html_element WHERE text='The Requiem Red' AND content_id<'c' ORDER BY content_id,node_index",
            "SELECT count(*) FROM experimental.html_element AS e WHERE 'The Requiem Red'=e.text",
            "SELECT e.* FROM html_element e WHERE e.tag='p' AND e.text='The Requiem Red' ORDER BY e.content_id,e.node_index LIMIT 2",
            "SELECT content_id,node_index FROM html_element WHERE text='The MISSINGWORD Red' ORDER BY content_id,node_index",
            'SELECT "my alias".content_id FROM html_element AS "my alias" WHERE "my alias".text=\'The REQUIEM Red\'',
        ):
            with self.subTest(sql=sql):
                expected = self.db.execute(sql).fetchall()
                types = self.db.description
                rewritten = self.rewrite(sql)
                self.assertIsNotNone(rewritten)
                self.assertEqual(self.db.execute(rewritten.sql).fetchall(), expected)
                self.assertEqual(self.db.description, types)
                self.assertIn('text', rewritten.sql.lower())
        self.assertGreater(len(self.db.execute("SELECT * FROM html_element WHERE text='The Requiem Red'").fetchall()), 3)

    def test_boundary_words_and_unsupported_shapes_are_not_rewritten(self):
        queries = [
            "SELECT * FROM html_element WHERE content_id='a' AND text='The Requiem Red'",
            "SELECT * FROM html_element WHERE content_id IN ('a','b') AND text='The Requiem Red'",
            "SELECT experimental.html_element.content_id FROM experimental.html_element WHERE text='The Requiem Red'",
            "SELECT * FROM html_element WHERE text='fish'",
            "SELECT * FROM html_element WHERE text='catfish'",
            "SELECT * FROM html_element WHERE text=''",
            "SELECT * FROM html_element WHERE text=' hello'",
            "SELECT * FROM html_element WHERE text='hello world'",
            "SELECT * FROM html_element WHERE text='---'",
            "SELECT * FROM html_element WHERE text LIKE '% requiem %'",
            "SELECT * FROM html_element WHERE text ILIKE '% requiem %'",
            "SELECT * FROM html_element WHERE text='The Requiem Red' OR tag='span'",
            "SELECT * FROM html_element WHERE NOT(text='The Requiem Red')",
            "SELECT * FROM html_element WHERE text COLLATE nocase='The Requiem Red'",
            "SELECT * FROM html_element WHERE text IS NULL",
            "SELECT * FROM html_element WHERE text=?",
            "WITH html_element AS (SELECT 'The Requiem Red' AS text) SELECT * FROM html_element WHERE text='The Requiem Red'",
            "SELECT * FROM html_element e JOIN html_element f USING(content_id) WHERE e.text='The Requiem Red'",
            "SELECT * FROM (SELECT * FROM html_element) e WHERE text='The Requiem Red'",
            "SELECT * FROM html_element WHERE text='The Requiem Red' UNION ALL SELECT * FROM html_element",
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                self.assertIsNone(exact_text_anchor(_one_statement(sql)))
        # This span exists, but page-once tokenization deliberately has no fish posting.
        self.assertTrue(self.db.execute("SELECT * FROM html_element WHERE text='fish'").fetchall())
        self.assertEqual(self.db.execute("SELECT * FROM material.html_terms WHERE term='fish'").fetchall(), [])

    def test_candidate_keys_remain_a_required_scan_filter(self):
        rewrite = self.rewrite("SELECT content_id FROM html_element WHERE text='The Requiem Red'")
        self.assertGreaterEqual(rewrite.candidate_count, 5)
        plan = self.db.execute('EXPLAIN ' + rewrite.sql).fetchone()[1]
        self.assertNotIn('HASH_JOIN', plan)
        self.assertIn('Filters:', plan)
        self.assertIn('NULL', rewrite.sql)
        self.assertIn('rowid', rewrite.sql)
        with patch('periplus.query.text_index.MAX_CANDIDATE_NODES', 1):
            self.assertIsNone(self.rewrite("SELECT * FROM html_element WHERE text='The Requiem Red'"))
        with patch('periplus.query.text_index.MAX_NODES_PER_CONTENT', 1):
            self.assertIsNone(self.rewrite("SELECT * FROM html_element WHERE text='The Requiem Red'"))

    def test_overflow_declines_without_losing_matches(self):
        sql = "SELECT * FROM html_element WHERE text='The Requiem Red'"
        with patch('periplus.query.text_index.MAX_CANDIDATE_CONTENTS', 1):
            self.assertIsNone(self.rewrite(sql))

    def test_changed_catalogue_and_collation_decline(self):
        sql = "SELECT * FROM html_element WHERE text='The Requiem Red'"
        self.db.execute("SET default_collation='nocase'")
        self.assertIsNone(self.rewrite(sql))
        self.db.execute("SET default_collation=''")
        self.db.execute('CREATE OR REPLACE VIEW experimental.html_element AS SELECT content_sha256 AS content_id,node_index,text FROM material.html_elements WHERE false')
        self.assertIsNone(self.rewrite(sql))
