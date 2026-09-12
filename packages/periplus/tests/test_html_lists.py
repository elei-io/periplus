"""List relations preserve source ownership, text, and explicit numbering resets."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlListTests(unittest.TestCase):
    def setUp(self):
        from element_fixture import catalogue
        self.catalogue = catalogue()
        self.db = self.catalogue.connection
        self.addCleanup(self.db.close)

    def load(self, html, content='fixture'):
        from element_fixture import seed
        nodes, elements = seed(self.db, html, content)
        return elements

    def items(self):
        return self.db.execute('SELECT item_index,ordinal,text FROM public_v1.html_list_item ORDER BY node_index').fetchall()

    def test_start_value_reset_signed_prefix_and_invalid(self):
        self.load('<ol start=" -2tail"><li>A<li value=" +5tail">B<li>C<li value="bad">D<li value="0">E</ol>')
        self.assertEqual(self.items(), [(0,-2,'A'),(1,5,'B'),(2,6,'C'),(3,7,'D'),(4,0,'E')])
        self.assertEqual(self.db.execute('SELECT ordered,start_number,reversed FROM public_v1.html_list').fetchall(), [(True,-2,False)])

    def test_reversed_default_and_explicit_start(self):
        self.load('<ol reversed="false"><li>A<li value=9>B<li>C</ol><ol reversed start=0><li>D<li>E</ol>')
        self.assertEqual(self.items(), [(0,3,'A'),(1,9,'B'),(2,8,'C'),(0,0,'D'),(1,-1,'E')])

    def test_nested_lists_text_empty_unordered_and_ownership(self):
        self.load('<ul reversed start=8><li> A <b>B</b><!--omit--><ol><li>inner<ul><li>deep</li></ul></li></ol> C<li value=9></ul><ol reversed></ol><li>orphan</li><dl><dt>term</dt><dd>meaning</dd></dl>')
        self.assertEqual(self.items(), [(0,None,' A B C'),(0,1,'inner'),(0,None,'deep'),(1,None,'')])
        self.assertEqual(self.db.execute('SELECT ordered,start_number,reversed FROM public_v1.html_list ORDER BY node_index').fetchall(),
                         [(False,None,False),(True,1,False),(False,None,False),(True,0,True)])

    def test_filters_keep_previous_resets_and_ignore_other_content(self):
        self.load('<ol><li value=20>A<li>B<li>C</ol>')
        self.load('<ol><li value=90>other</ol>', 'other')
        self.assertEqual(self.db.execute("SELECT ordinal,text FROM public_v1.html_list_item WHERE content_id='fixture' AND item_index=2").fetchall(), [(22,'C')])
        for name, types in [('html_list',['VARCHAR','INTEGER','BOOLEAN','BIGINT','BOOLEAN']),
                            ('html_list_item',['VARCHAR','INTEGER','INTEGER','INTEGER','BIGINT','VARCHAR'])]:
            self.assertEqual([r[1] for r in self.db.execute('DESCRIBE public_v1.'+name).fetchall()],types)

    def test_bigint_overflow_is_not_silently_wrapped(self):
        self.load('<ol start="9223372036854775807"><li>A<li>B</ol>')
        with self.assertRaises(duckdb.ConversionException):
            self.items()
