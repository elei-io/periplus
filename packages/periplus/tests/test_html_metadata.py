"""Metadata views preserve declarations rather than choosing preferred values."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlMetadataTests(unittest.TestCase):
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
            subtree_end_index INTEGER, tag VARCHAR, namespace VARCHAR,
            attributes MAP(VARCHAR, VARCHAR), text_direct VARCHAR)''')
        self.db.execute(files('periplus.platform.catalogue').joinpath('sql/public_v1/views/html_metadata.sql').read_text())

    def load(self, html, content='fixture'):
        nodes, elements = parse_document(html)
        self.db.executemany('INSERT INTO public_v1.html_node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           [(content, *astuple(n)) for n in nodes])
        self.db.executemany('INSERT INTO public_v1.html_element VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                           [(content, e.element_index, e.parent_index, e.subtree_end_index,
                             e.tag, e.namespace_uri, e.attributes, e.text_direct) for e in elements])

    def rows(self):
        return self.db.execute('SELECT kind,name,value FROM public_v1.html_metadata ORDER BY node_index,kind,name').fetchall()

    def test_declarations_and_original_values(self):
        self.load('<html lang="en-GB"><head><title> A &amp; B </title>'
                  '<meta name="description" content=" First "><meta name="description" content="Second">'
                  '<meta property="og:title" content="Book"><meta name="twitter:card" content="summary">'
                  '<meta charset="UTF-8"><meta http-equiv="refresh" content="5; url=/next">'
                  '<link rel="canonical alternate canonical" href="../book"></head></html>')
        self.assertEqual(self.rows(), [('html_attribute','lang','en-GB'),('title','title',' A & B '),
            ('meta_name','description',' First '),('meta_name','description','Second'),
            ('meta_property','og:title','Book'),('meta_name','twitter:card','summary'),
            ('meta_charset','charset','UTF-8'),('meta_http_equiv','refresh','5; url=/next'),
            ('link_rel','alternate','../book'),('link_rel','canonical','../book')])

    def test_missing_empty_and_multiple_attributes(self):
        self.load('<title></title><meta name="missing"><meta name="empty" content="">'
                  '<meta name="Name" property="Property" content="shared">'
                  '<link rel="canonical"><link rel=" \t "><meta content="unlabelled">')
        self.assertEqual(self.rows(), [('title','title',''),('meta_name','missing',None),
            ('meta_name','empty',''),('meta_name','Name','shared'),('meta_property','Property','shared'),
            ('link_rel','canonical',None)])

    def test_foreign_title_excluded_and_content_filter(self):
        self.load('<title>HTML</title><body><svg><title>SVG</title></svg><meta name="body" content="yes">')
        self.load('<title>Other</title>', 'other')
        self.assertEqual(self.db.execute("SELECT kind,value FROM public_v1.html_metadata WHERE content_id='fixture' ORDER BY node_index").fetchall(),
                         [('title','HTML'),('meta_name','yes')])
        self.assertEqual([r[1] for r in self.db.execute('DESCRIBE public_v1.html_metadata').fetchall()],
                         ['VARCHAR','INTEGER','VARCHAR','VARCHAR','VARCHAR'])

    def test_title_rcdata_matches_descendant_text(self):
        sources = (
            '<title>A <em>B</em> &amp; 雪</title>',
            '<body><title>A <b>B</b></title>',
            '<svg><title>SVG <tspan>nested</tspan></title></svg>',
            '<svg><foreignObject><title>A <b>B</b></title></foreignObject></svg>',
            '<template><title>A <b>B</b></title></template>',
            '<title></title><title>second</title>',
            '<select><title>A <b>B</b></title></select>',
        )
        for index, source in enumerate(sources):
            self.load(source, str(index))
        rows = self.db.execute("""SELECT e.text_direct,
            coalesce(string_agg(n.value, '' ORDER BY n.node_index), '')
            FROM public_v1.html_element e LEFT JOIN public_v1.html_node n
             ON n.content_id=e.content_id AND n.node_index>e.node_index
             AND n.node_index<e.subtree_end_index AND n.node_type='text'
            WHERE e.tag='title' AND e.namespace='http://www.w3.org/1999/xhtml'
            GROUP BY e.content_id,e.node_index,e.text_direct""").fetchall()
        # Lexbor retains the title inside select; identities follow its tree.
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(direct == descendant for direct, descendant in rows))
        self.assertEqual(self.db.execute("SELECT value FROM public_v1.html_metadata WHERE content_id='0'").fetchall(),
                         [('A <em>B</em> & 雪',)])

    def test_title_name_keeps_meta_declarations_without_node_dependency(self):
        self.load('<title>Page</title><meta name="title" content="Declared">')
        self.db.execute('DROP TABLE public_v1.html_node')
        self.assertEqual(self.db.execute("SELECT kind,value FROM public_v1.html_metadata WHERE name='title' ORDER BY node_index").fetchall(),
                         [('title','Page'),('meta_name','Declared')])
