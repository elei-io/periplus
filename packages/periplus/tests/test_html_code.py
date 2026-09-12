"""Code extraction preserves syntax-highlighted text and structural block identity."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlCodeTests(unittest.TestCase):
    def setUp(self):
        from element_fixture import catalogue
        self.catalogue = catalogue()
        self.db = self.catalogue.connection
        self.addCleanup(self.db.close)

    def load(self, html, content='fixture'):
        from element_fixture import seed
        return seed(self.db, html, content)[1]

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
        self.db.execute("INSERT INTO material.html_elements VALUES ('fixture',999,NULL,1001,0,0,'code','http://www.w3.org/2000/svg',MAP {},'', '',0,0)")
        node=next(e.element_index for e in elements if e.tag=='code')
        self.assertEqual(self.db.execute('SELECT node_index,block,text FROM public_v1.html_code WHERE content_id=? AND node_index=?',
                                         ['fixture',node]).fetchall(),[(node,False,'ABC')])
        self.assertEqual(self.db.execute("SELECT count(*) FROM public_v1.html_code WHERE content_id='fixture'").fetchone(),(1,))
