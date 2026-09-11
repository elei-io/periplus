"""Readable text semantics and public SQL discovery over real projections."""
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import PROJECTIONS
from periplus.materialization.projections.prose import _body_text, project
from periplus.materialization.projections.html_nodes import project as elements_project
from periplus.query.validation import _bounded_query


class ProseTests(unittest.TestCase):
    def test_body_exclusions_and_inline_boundaries(self):
        nodes, _ = parse_document('''<head><title>Not body</title></head><body>
        <nav>Home</nav><p>Start <strong>free</strong> trial &amp; go.</p>
        <p>inter<em>national</em></p><script>bad()</script><style>bad{}</style>
        <template><p>Not instantiated</p></template><noscript>Fallback</noscript>
        <!--comment--><footer>Contact</footer></body>''')
        self.assertEqual(_body_text(nodes), 'Home Start free trial & go. international Contact')

    def test_blocks_unicode_and_no_visibility_inference(self):
        nodes, _ = parse_document('''<div>One<div>Two</div>Three</div>Four<br>Five
        <table><tr><td>Six</td><td>Seven</td></tr></table>
        <pre> Eight\n  Nine </pre><p hidden>Ten</p><p style="display:none">Eleven</p>
        <p>日本語&nbsp; café</p><img alt="Not substituted">''')
        self.assertEqual(_body_text(nodes),
                         'One Two Three Four Five Six Seven Eight Nine Ten Eleven 日本語 café')

    def test_root_attributes_with_colliding_local_names(self):
        nodes, elements = parse_document(
            '<html lang="en" xml:lang="fi"><body><p>Hello</p></body></html>')
        root = next(e for e in elements if e.tag == 'html')
        self.assertEqual(root.attributes, {'lang': 'en', 'xml:lang': 'fi'})
        self.assertEqual(_body_text(nodes), 'Hello')
        self.assertEqual(nodes[0].subtree_end_index, len(nodes))

    def test_empty_and_missing_body(self):
        for html in ('', '<body><script>only script</script></body>',
                     '<html><frameset><frame src="x"></frameset></html>'):
            with self.subTest(html=html):
                self.assertEqual(_body_text(parse_document(html)[0]), '')

    def test_owned_content_only_and_public_join(self):
        nodes, elements = parse_document('<h1>Careers</h1><p>Visa <b>sponsorship</b></p>')
        context = VisitBatchContext((), (), (), {'a': elements, 'b': elements},
                                    {'a': nodes, 'b': nodes}, {}, frozenset({'a'}))
        self.assertIn("prose", {spec.name for spec in PROJECTIONS})
        prose = project(context)
        self.assertEqual(prose.to_pylist(), [{'content_sha256': 'a', 'text': 'Careers Visa sponsorship'}])
        db = duckdb.connect()
        self.addCleanup(db.close)
        db.execute('CREATE SCHEMA material; CREATE SCHEMA public_v1')
        db.register('prose_rows', prose)
        db.register('element_rows', elements_project(context))
        db.execute('CREATE TABLE material.prose AS SELECT * FROM prose_rows')
        db.execute('CREATE TABLE material.html_nodes AS SELECT * FROM element_rows')
        root = files('periplus.platform.catalogue').joinpath('sql/public_v1/views')
        for name in ('prose', 'html_element'):
            db.execute(root.joinpath(f'{name}.sql').read_text())
        sql = """SELECT e.content_id, e.tag FROM public_v1.html_element e
                 JOIN public_v1.prose p USING (content_id)
                 WHERE p.text ILIKE '%visa sponsorship%' AND e.tag = 'h1'"""
        _bounded_query(sql)
        self.assertEqual(db.execute(sql).fetchall(), [('a', 'h1')])
        empty = VisitBatchContext((), (), (), {}, {}, {}, frozenset())
        self.assertEqual(project(empty).num_rows, 0)
        self.assertEqual(project(empty).schema, prose.schema)
