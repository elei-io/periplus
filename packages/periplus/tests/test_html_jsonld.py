"""JSON-LD declarations retain whole documents and visible parse failures."""
from dataclasses import astuple
from importlib.resources import files
import json
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.registry import BY_NAME


class HtmlJsonldTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)
        self.db.execute('CREATE SCHEMA public_v1')
        self.db.execute('CREATE SCHEMA material')
        self.db.execute('CREATE TABLE material.html_jsonld(content_sha256 VARCHAR, node_index INTEGER, value JSON, parse_error VARCHAR)')
        self.db.execute('''CREATE TABLE public_v1.html_node (
            content_id VARCHAR, node_index INTEGER, parent_index INTEGER,
            subtree_end_index INTEGER, sibling_index INTEGER, node_type VARCHAR,
            name VARCHAR, namespace VARCHAR, value VARCHAR, depth INTEGER)''')
        self.db.execute('''CREATE TABLE public_v1.html_element (
            content_id VARCHAR, node_index INTEGER, tag VARCHAR,
            namespace VARCHAR, attributes MAP(VARCHAR, VARCHAR), text_direct VARCHAR)''')
        self.db.execute(files('periplus.platform.catalogue').joinpath('sql/public_v1/views/html_jsonld.sql').read_text())

    def load(self, html, content='fixture'):
        nodes, elements = parse_document(html)
        context = VisitBatchContext((), (), (), {content: elements}, {content: nodes}, {}, frozenset({content}))
        self.db.register('projected_jsonld', BY_NAME['html_jsonld'].rows(context))
        self.db.execute('INSERT INTO material.html_jsonld SELECT content_sha256,node_index,value::JSON,parse_error FROM projected_jsonld')
        self.db.unregister('projected_jsonld')
        self.db.executemany('INSERT INTO public_v1.html_node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           [(content, *astuple(n)) for n in nodes])
        self.db.executemany('INSERT INTO public_v1.html_element VALUES (?, ?, ?, ?, ?, ?)',
                           [(content, e.element_index, e.tag, e.namespace_uri, e.attributes, e.text_direct) for e in elements])

    def test_complete_objects_arrays_graph_and_duplicates(self):
        documents = [{'@context':'https://schema.org','@graph':[{'@type':'Book','name':'A &amp; B'}]},
                     [{'@type':'Book'},{'@type':'Person'}], {'@type':'Book'}, {'@type':'Book'}]
        self.load(''.join('<script type="application/ld+json">'+json.dumps(d)+'</script>' for d in documents))
        rows = self.db.execute('SELECT node_index,value,parse_error FROM public_v1.html_jsonld ORDER BY node_index').fetchall()
        self.assertEqual([json.loads(r[1]) for r in rows], documents)
        self.assertEqual(len({r[0] for r in rows}),4)
        self.assertTrue(all(r[2] is None for r in rows))
        self.assertEqual([r[1] for r in self.db.execute('DESCRIBE public_v1.html_jsonld').fetchall()],
                         ['VARCHAR','INTEGER','JSON','VARCHAR'])

    def test_invalid_empty_and_json_null_are_distinct(self):
        self.load(''.join('<script type="application/ld+json">'+value+'</script>'
                         for value in ('{broken','', ' \n\t ', 'null', '42')))
        self.assertEqual(self.db.execute('SELECT value,parse_error FROM public_v1.html_jsonld ORDER BY node_index').fetchall(),
                         [(None,'Invalid JSON syntax'),(None,'Empty JSON-LD script'),
                          (None,'Empty JSON-LD script'),('null',None),('42',None)])

    def test_type_matching_and_content_isolation(self):
        self.load('<script type=" Application/LD+JSON ; charset=utf-8">{}</script>'
                  '<script type="application/json">{}</script><script>{}</script>'
                  '<script type="application/ld+json" src="https://example.org/data.json"></script>')
        self.load('<script type="application/ld+json">{broken</script>', 'other')
        self.db.execute("INSERT INTO public_v1.html_element VALUES ('fixture',999,'script','http://www.w3.org/2000/svg',MAP {'type':'application/ld+json'}, '{}')")
        self.assertEqual(self.db.execute("SELECT value,parse_error FROM public_v1.html_jsonld WHERE content_id='fixture' ORDER BY node_index").fetchall(),
                         [('{}',None),(None,'Empty JSON-LD script')])

    def test_direct_text_matches_ordered_node_reconstruction(self):
        self.load('<script type="application/ld+json">{"name":"雪 &amp; &lt;"}</script>'
                  '<script type="application/ld+json">\n {"x": [1, null]} \t</script>'
                  '<script type="application/ld+json"><!-- not JSON --></script>'
                  '<script type="application/ld+json"></script>')
        rows = self.db.execute("""SELECT e.text_direct,
            coalesce(string_agg(n.value, '' ORDER BY n.node_index), '')
            FROM public_v1.html_element e LEFT JOIN public_v1.html_node n
              ON n.content_id=e.content_id AND n.parent_index=e.node_index
             AND n.node_type='text'
            WHERE e.tag='script'
            GROUP BY e.content_id,e.node_index,e.text_direct""").fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(direct == reconstructed for direct, reconstructed in rows))

    def test_no_node_dependency(self):
        self.load('<script type="application/ld+json">{"name":"two parts"}</script>')
        before = self.db.execute('SELECT * FROM public_v1.html_jsonld').fetchall()
        # The public view must remain usable without reading the node relation.
        self.db.execute('DROP TABLE public_v1.html_node')
        self.db.execute('DROP TABLE public_v1.html_element')
        self.assertEqual(self.db.execute('SELECT * FROM public_v1.html_jsonld').fetchall(), before)
        self.assertEqual(json.loads(before[0][2]), {"name": "two parts"})
