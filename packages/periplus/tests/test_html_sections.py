"""Heading-delimited passages retain source boundaries and nesting rules."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlSectionTests(unittest.TestCase):
    def setUp(self):
        from element_fixture import catalogue
        self.catalogue = catalogue()
        self.db = self.catalogue.connection
        self.addCleanup(self.db.close)

    def load(self, html, content='fixture'):
        from element_fixture import seed
        nodes, elements = seed(self.db, html, content)
        return nodes, [e for e in elements if e.tag in ('h1','h2','h3','h4','h5','h6')]

    def test_parent_ranges_and_descendant_heading_text(self):
        nodes,heads=self.load('<p>preamble</p><h1>Guide</h1><p>intro</p><h2>Install</h2><p>steps</p>'
                             '<h3>Linux</h3><p>apt</p><h2>Usage</h2><p>run</p><h1>Other</h1><p>end</p>')
        ids=[h.element_index for h in heads]
        rows=self.db.execute('SELECT heading_node_index,parent_heading_node_index,start_node_index,end_node_index FROM public_v1.html_section ORDER BY heading_node_index').fetchall()
        self.assertEqual(rows,[(ids[0],None,heads[0].subtree_end_index,ids[4]),
                              (ids[1],ids[0],heads[1].subtree_end_index,ids[3]),
                              (ids[2],ids[1],heads[2].subtree_end_index,ids[3]),
                              (ids[3],ids[0],heads[3].subtree_end_index,ids[4]),
                              (ids[4],None,heads[4].subtree_end_index,nodes[0].subtree_end_index)])
        text=self.db.execute("""SELECT string_agg(e.text_direct,'' ORDER BY e.node_index)
            FROM public_v1.html_section s JOIN public_v1.html_element e
              ON e.content_id=s.content_id AND e.node_index>=s.start_node_index
             AND e.node_index<s.end_node_index
            WHERE s.heading_node_index=?""",[ids[1]]).fetchone()[0]
        self.assertEqual(text,'stepsLinuxapt')

    def test_all_ranks_skips_and_filtered_heading(self):
        nodes,heads=self.load(''.join(f'<h{r}>title</h{r}><p>body</p>' for r in (6,5,4,3,2,1,3,5,4,2,6,1)))
        for index,head in enumerate(heads):
            rank=int(head.tag[1]);prior=[h.element_index for h in heads[:index] if int(h.tag[1])<rank]
            following=[h.element_index for h in heads[index+1:] if int(h.tag[1])<=rank]
            row=self.db.execute("SELECT parent_heading_node_index,end_node_index FROM public_v1.html_section WHERE content_id='fixture' AND heading_node_index=?",[head.element_index]).fetchone()
            self.assertEqual(row,(prior[-1] if prior else None,following[0] if following else nodes[0].subtree_end_index))

    def test_empty_passage_inline_heading_and_no_headings(self):
        _,heads=self.load('<h2>A <em>B</em></h2><h2>C</h2>')
        self.load('<p>no headings</p>','none')
        row=self.db.execute("SELECT start_node_index,end_node_index FROM public_v1.html_section WHERE content_id='fixture' AND heading_node_index=?",[heads[0].element_index]).fetchone()
        self.assertEqual(row,(heads[0].subtree_end_index,heads[1].element_index))
        self.assertEqual(row[0],row[1])
        self.assertEqual(self.db.execute("SELECT * FROM public_v1.html_section WHERE content_id='none'").fetchall(),[])
        self.assertEqual([r[1] for r in self.db.execute('DESCRIBE public_v1.html_section').fetchall()],['VARCHAR']+['INTEGER']*4)

    def test_malformed_nested_headings_never_produce_negative_ranges(self):
        self.load('<h2>outer<div><h1>inner</h1></div></h2>')
        self.assertEqual(self.db.execute('SELECT count(*) FROM public_v1.html_section WHERE start_node_index>end_node_index').fetchone(),(0,))
