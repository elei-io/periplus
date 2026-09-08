"""Image metadata preserves declared attributes and source identity."""
import unittest
from importlib.resources import files

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlImageTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)
        self.db.execute('CREATE SCHEMA public_v1')
        self.db.execute('''CREATE TABLE public_v1.html_element (
            content_id VARCHAR, node_index INTEGER, tag VARCHAR,
            namespace VARCHAR, attributes MAP(VARCHAR, VARCHAR))''')
        self.db.execute(files('periplus.platform.catalogue').joinpath('sql/public_v1/views/html_image.sql').read_text())

    def load(self, html, content='fixture'):
        _, elements = parse_document(html)
        self.db.executemany('INSERT INTO public_v1.html_element VALUES (?, ?, ?, ?, ?)',
                           [(content, e.element_index, e.tag, e.namespace_uri, e.attributes) for e in elements])
        return elements

    def test_original_attributes_and_identity(self):
        elements = self.load('<img src="../cover?a=1&amp;b=2" srcset="small.jpg 1x, large.jpg 2x" '
                             'sizes=" (max-width: 600px) 100vw, 600px " alt=" A &amp; B " width="0100" height="auto">')
        node = next(e.element_index for e in elements if e.tag == 'img')
        self.assertEqual(self.db.execute('SELECT * FROM public_v1.html_image').fetchall(),
                         [('fixture',node,'../cover?a=1&b=2','small.jpg 1x, large.jpg 2x',
                           ' (max-width: 600px) 100vw, 600px ',' A & B ','0100','auto')])
        self.assertEqual([r[1] for r in self.db.execute('DESCRIBE public_v1.html_image').fetchall()],
                         ['VARCHAR','INTEGER'] + ['VARCHAR']*6)

    def test_empty_missing_and_lazy_loading(self):
        self.load('<img alt="" src=""><img><img data-src="lazy.jpg">')
        self.assertEqual(self.db.execute('SELECT src,alt FROM public_v1.html_image ORDER BY node_index').fetchall(),
                         [('', ''), (None,None), (None,None)])

    def test_picture_duplicates_foreign_namespace_and_content_filter(self):
        self.load('<picture><source srcset="large.jpg"><img src="cover.jpg"></picture>'
                  '<img src="cover.jpg"><svg><image href="vector.svg"/></svg><input type="image" src="input.jpg">')
        self.load('<img src="other.jpg">', 'other')
        self.db.execute("INSERT INTO public_v1.html_element VALUES ('fixture',999,'img','http://www.w3.org/2000/svg',MAP {'src':'foreign.jpg'})")
        self.assertEqual(self.db.execute("SELECT src FROM public_v1.html_image WHERE content_id='fixture' ORDER BY node_index").fetchall(),
                         [('cover.jpg',),('cover.jpg',)])
