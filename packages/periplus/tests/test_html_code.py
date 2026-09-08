"""Code extraction preserves syntax-highlighted text and structural block identity."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlCodeTests(unittest.TestCase):
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
        self.db.execute(files('periplus.platform.catalogue').joinpath('sql/public_v1/views/html_code.sql').read_text())

    def load(self, html, content='fixture'):
        nodes, elements = parse_document(html)
        self.db.executemany('INSERT INTO public_v1.html_node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           [(content, *astuple(n)) for n in nodes])
        self.db.executemany('INSERT INTO public_v1.html_element VALUES (?, ?, ?, ?, ?, ?)',
                           [(content,e.element_index,e.parent_index,e.subtree_end_index,e.tag,e.namespace_uri) for e in elements])
        return elements

    def test_inline_block_highlighting_and_empty(self):
        self.load('<p><code>pip install x</code></p><pre><span><code>  <span>if</span> x &lt; 2:\n\tgo()<!--omit--></code></span></pre><code></code><pre>plain text</pre>')
        self.assertEqual(self.db.execute('SELECT block,text FROM public_v1.html_code ORDER BY node_index').fetchall(),
                         [(False,'pip install x'),(True,'  if x < 2:\n\tgo()'),(False,'')])
        self.assertEqual([r[1] for r in self.db.execute('DESCRIBE public_v1.html_code').fetchall()],
                         ['VARCHAR','INTEGER','BOOLEAN','VARCHAR'])

    def test_nested_code_and_pre_do_not_duplicate_text(self):
        self.load('<pre><pre><code>A<code>B</code>C</code></pre></pre>')
        self.assertEqual(self.db.execute('SELECT block,text FROM public_v1.html_code ORDER BY node_index').fetchall(),
                         [(True,'ABC'),(True,'B')])

    def test_content_node_identity_and_namespace(self):
        elements=self.load('<code>A<b>B</b>C</code>')
        self.load('<pre><code>other</code></pre>', 'other')
        self.db.execute("INSERT INTO public_v1.html_element VALUES ('fixture',999,NULL,1001,'code','http://www.w3.org/2000/svg')")
        node=next(e.element_index for e in elements if e.tag=='code')
        self.assertEqual(self.db.execute('SELECT node_index,block,text FROM public_v1.html_code WHERE content_id=? AND node_index=?',
                                         ['fixture',node]).fetchall(),[(node,False,'ABC')])
        self.assertEqual(self.db.execute("SELECT count(*) FROM public_v1.html_code WHERE content_id='fixture'").fetchone(),(1,))
