"""Unified DOM and shared occurrence semantics."""
from collections import Counter
from importlib.resources import files
import unittest
from unittest.mock import patch
import duckdb
from periplus.materialization.registry import BY_NAME
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.search_text import build_search_text
from periplus.materialization.tokenization import term_counts


class NodeLayoutTests(unittest.TestCase):
    def test_provenance_and_existing_token_semantics(self):
        fixtures = ['<p>mon<strong>key</strong> monkey</p>', '<p>caf<span>e</span>\u0301</p>',
                    '<p>😀 StraßE 日本語 中文 한글</p>', '<p>foo</p><p>bar</p><script>hidden</script>',
                    '<p>one  \n two <span>three</span> four</p>', '<p>ᄀ<span>ᅡ</span> ẞ İ ﬃ</p>']
        for source in fixtures:
            with self.subTest(source=source):
                nodes, _ = parse_document(source)
                result = build_search_text(nodes)
                self.assertEqual(result.content_counts, term_counts(result.prose))
                for (term, index), count in result.node_counts.items():
                    self.assertEqual(nodes[index].node_type, 'text')
                    self.assertGreater(count, 0)
                    self.assertLessEqual(count, result.content_counts[term])
        split = build_search_text(parse_document(fixtures[0])[0])
        self.assertEqual(split.content_counts, Counter(monkey=2))
        self.assertEqual(sum(split.node_counts.values()), 3)
        self.assertEqual(len(split.node_counts), 3)
        accent = build_search_text(parse_document(fixtures[1])[0])
        self.assertEqual(accent.content_counts, Counter({'café': 1}))
        self.assertEqual(len(accent.node_counts), 3)

    def test_shared_compute_and_public_element_matches(self):
        nodes, elements = parse_document('<p id="price">mon<strong>key</strong> monkey</p>')
        context = VisitBatchContext((), (), (), {'a': elements}, {'a': nodes}, {}, frozenset({'a'}))
        with patch('periplus.materialization.document_projection.build_search_text', wraps=build_search_text) as build:
            terms = BY_NAME['term'].rows(context).to_pylist()
            context.dictionary_ids['term'] = {row['text']: i for i, row in enumerate(terms, 1)}
            tables = {name: BY_NAME[name].rows(context) for name in ('html_nodes', 'prose', 'content_posting', 'node_posting')}
            self.assertEqual(build.call_count, 1)
        self.assertNotIn('html_elements', BY_NAME)
        with duckdb.connect() as db:
            db.execute('CREATE SCHEMA material; CREATE SCHEMA public_v1')
            for name, table in tables.items():
                db.register('rows', table)
                db.execute(f'CREATE TABLE material.{name} AS SELECT * FROM rows')
            base = files('periplus.platform.catalogue').joinpath('sql/public_v1/views')
            db.execute(base.joinpath('html_element.sql').read_text())
            actual = db.execute('SELECT node_index, attributes, text_direct FROM public_v1.html_element ORDER BY node_index').fetchall()
            self.assertEqual(actual, [(e.element_index, e.attributes, e.text_direct) for e in elements])
            matches = db.execute('''SELECT e.tag, sum(p.frequency) FROM material.node_posting p
                JOIN material.html_nodes n USING(content_sha256, node_index)
                JOIN public_v1.html_element e ON e.content_id=n.content_sha256 AND e.node_index=n.parent_index
                GROUP BY e.tag ORDER BY e.tag''').fetchall()
            self.assertEqual(matches, [('p', 2), ('strong', 1)])
