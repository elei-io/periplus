"""Portable search over real page-once ICU projection output."""
import unittest

from element_fixture import catalogue
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME
from periplus.query.helpers import query_helpers, safe_helper_error


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = catalogue()
        self.addCleanup(self.catalogue.connection.close)
        self.db = self.catalogue.connection
        self.add_page('a', '<p> robot <b>science</b> robot </p>')
        self.add_page('b', '<p> science </p>')
        self.add_page('c', '<p>cat<span>fish</span> Straße</p>')

    def add_page(self, content, html):
        nodes, elements = parse_document(html)
        context = VisitBatchContext((), (), (), {content: elements}, {content: nodes}, {}, frozenset({content}))
        self.db.register('postings', BY_NAME['html_terms'].rows(context))
        try:
            self.db.execute('INSERT INTO material.html_terms SELECT * FROM postings')
        finally:
            self.db.unregister('postings')

    def search(self, terms):
        return self.db.execute('SELECT * FROM public_v1.search(?) ORDER BY score DESC,content_id', [terms]).fetchall()

    def test_any_word_score_and_union_are_exact(self):
        rows = self.search(['robot', 'science', 'robot', None, ''])
        self.assertEqual([(r[0], r[2]) for r in rows], [('a', 2.0), ('b', 1.0)])
        for content, nodes, _ in rows:
            postings = self.db.execute("SELECT node_indexes FROM public_v1.html_term WHERE content_id=? AND term IN ('robot','science')", [content]).fetchall()
            expected = sorted({index for (indexes,) in postings for index in indexes})
            self.assertEqual(nodes, expected)
            self.assertEqual(nodes, sorted(set(nodes)))

    def test_empty_null_unknown_and_input_limit(self):
        for terms in [[], None, [None, ''], ['not-in-corpus']]:
            self.assertEqual(self.search(terms), [])
        self.assertTrue(self.search(['robot'] * 32))
        with self.assertRaises(Exception) as raised:
            self.search(['robot'] * 33)
        self.assertEqual(safe_helper_error(str(raised.exception)), 'search accepts at most 32 term keys')

    def test_page_word_boundaries_and_unicode_keys(self):
        self.assertEqual(self.search(['fish']), [])
        self.assertEqual(self.search(['STRASSE']), [])
        self.assertEqual([(r[0], r[2]) for r in self.search(['catfish', 'strasse'])], [('c', 2.0)])

    def test_discovery_and_return_types(self):
        helper = next(h for h in query_helpers().helpers if h.name == 'public_v1.search')
        self.assertEqual([c.name for c in helper.columns], ['content_id', 'node_indexes', 'score'])
        self.db.execute("SELECT * FROM public_v1.search(['robot'])")
        self.assertEqual([str(c[1]) for c in self.db.description], ['VARCHAR', 'INTEGER[]', 'DOUBLE'])
        plan = self.db.execute("EXPLAIN SELECT * FROM public_v1.search(['robot'])").fetchone()[1]
        self.assertIn('Filters:', plan)
        self.assertIn('EMPTY_RESULT', plan)
        self.assertEqual(plan.count('SEQ_SCAN'), 1)
        self.assertNotIn('HASH_JOIN', plan)
        full = ['robot', *[str(i) for i in range(31)]]
        plan = self.db.execute('EXPLAIN SELECT * FROM public_v1.search(?)', [full]).fetchone()[1]
        self.assertNotIn('HASH_JOIN', plan)
