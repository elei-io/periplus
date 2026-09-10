"""Differential projection checks against the existing DuckDB JSON-LD contract."""
from dataclasses import replace
from pathlib import Path
import unittest
import duckdb
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME

PROJECTION = BY_NAME["html_jsonld"]

class JsonldProjectionTests(unittest.TestCase):
    def test_preserves_complete_parser_results_and_owned_content(self):
        values = ['null', '', ' \t\n', '{bad', '{"x":1,"x":2}',
                  '{"n":123456789012345678901234567890,"s":"&amp; 雪"}',
                  '[1,2,]', 'NaN', 'Infinity', '1e999', '{"@graph":[]}',
                  '\u00a0', '"scalar"', '{}', '{}']
        html = ''.join('<script type=" Application/LD+JSON ; charset=UTF-8">' + v + '</script>' for v in values)
        html += '<script type="application/json">{}</script><script>{}</script>'
        html += '<svg><script type="application/ld+json">{}</script></svg>'
        nodes, elements = parse_document(html)
        context = VisitBatchContext((), (), (), {'a': elements, 'b': elements},
                                    {'a': nodes, 'b': nodes}, {}, frozenset({'a'}))
        output = PROJECTION.rows(context)
        self.assertEqual(output.num_rows, len(values))
        with duckdb.connect() as db:
            db.execute('CREATE SCHEMA public_v1')
            db.execute('CREATE TABLE public_v1.html_element(content_id VARCHAR, node_index INTEGER, tag VARCHAR, namespace VARCHAR, attributes MAP(VARCHAR,VARCHAR), text_direct VARCHAR)')
            db.executemany('INSERT INTO public_v1.html_element VALUES (?,?,?,?,?,?)',
                           [('a', e.element_index, e.tag, e.namespace_uri, e.attributes, e.text_direct) for e in elements])
            db.execute((Path(__file__).resolve().parents[3] / 'docs/query-investigations/jsonld-layout/baseline.sql').read_text())
            db.register('projected', output)
            expected = db.execute('SELECT * FROM public_v1.html_jsonld ORDER BY node_index').fetchall()
            actual = db.execute('SELECT content_sha256,node_index,value::JSON,parse_error FROM projected ORDER BY node_index').fetchall()
            self.assertEqual(actual, expected)
        self.assertEqual(PROJECTION.rows(replace(context, content_output_hashes=frozenset())).num_rows, 0)
        self.assertEqual(PROJECTION.rows(replace(context, parsed_elements_by_content={})).num_rows, 0)
