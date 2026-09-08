"""Heading extraction preserves source identity and descendant text."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlHeadingTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)
        self.db.execute('CREATE SCHEMA public_v1')
        self.db.execute('''CREATE TABLE public_v1.html_node (
            content_id VARCHAR, node_index INTEGER, parent_index INTEGER,
            subtree_end_index INTEGER, sibling_index INTEGER, node_type VARCHAR,
            name VARCHAR, namespace VARCHAR, value VARCHAR, depth INTEGER)''')
        self.db.execute('''CREATE TABLE public_v1.html_element (
            content_id VARCHAR, node_index INTEGER, parent_index INTEGER,
            subtree_end_index INTEGER, tag VARCHAR, namespace VARCHAR)''')
        sql = files('periplus.platform.catalogue').joinpath('sql/public_v1/views/html_heading.sql').read_text()
        self.db.execute(sql)

    def load(self, html, content='fixture'):
        nodes, elements = parse_document(html)
        self.db.executemany('INSERT INTO public_v1.html_node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           [(content, *astuple(n)) for n in nodes])
        self.db.executemany('INSERT INTO public_v1.html_element VALUES (?, ?, ?, ?, ?, ?)',
                           [(content, e.element_index, e.parent_index, e.subtree_end_index,
                             e.tag, e.namespace_uri) for e in elements])
        return elements

    def test_levels_inline_text_empty_and_duplicate_headings(self):
        elements = self.load('<h1> A <em>B</em>&amp;C<!--omit--><br>D </h1>'
                             '<h2></h2><h3>same</h3><h4>same</h4><h5>five</h5><h6>six</h6>')
        result = self.db.execute('SELECT * FROM public_v1.html_heading ORDER BY node_index').fetchall()
        self.assertEqual([(r[2], r[3]) for r in result],
                         [(1,' A B&CD '),(2,''),(3,'same'),(4,'same'),(5,'five'),(6,'six')])
        self.assertEqual([r[1] for r in result], [e.element_index for e in elements if e.tag in ('h1','h2','h3','h4','h5','h6')])
        self.assertEqual([r[1] for r in self.db.execute('DESCRIBE public_v1.html_heading').fetchall()],
                         ['VARCHAR', 'INTEGER', 'INTEGER', 'VARCHAR'])

    def test_declared_html_only_and_no_visibility_inference(self):
        self.load('<div role="heading" aria-level="2">role</div><h7>other</h7>'
                  '<h2 hidden><img alt="not text"><script>x</script><style>y</style>z</h2>')
        self.db.execute("INSERT INTO public_v1.html_element VALUES ('fixture',999,NULL,1001,'h1','http://www.w3.org/2000/svg')")
        self.assertEqual(self.db.execute('SELECT level,text FROM public_v1.html_heading').fetchall(), [(2,'xyz')])

    def test_content_and_node_filters_preserve_complete_text(self):
        elements = self.load('<h2>one <b>two</b> three</h2><h2>other</h2>')
        self.load('<h2>unrelated</h2>', 'other')
        node = next(e.element_index for e in elements if e.tag == 'h2')
        self.assertEqual(self.db.execute('''SELECT text FROM public_v1.html_heading
            WHERE content_id=? AND node_index=? AND level=2''', ['fixture',node]).fetchall(), [('one two three',)])
