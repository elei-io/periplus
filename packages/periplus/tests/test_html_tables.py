"""Public table relations execute directly over HTML primitives, without projections."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlTableTests(unittest.TestCase):
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
            subtree_end_index INTEGER, sibling_index INTEGER, tag VARCHAR,
            namespace VARCHAR, attributes MAP(VARCHAR, VARCHAR), text_direct VARCHAR)''')
        root = files('periplus.platform.catalogue').joinpath('sql/public_v1/views')
        for name in ('html_table', 'html_table_cell'):
            self.db.execute(root.joinpath(name + '.sql').read_text())

    def load(self, html, content='fixture'):
        nodes, elements = parse_document(html)
        self.db.executemany('INSERT INTO public_v1.html_node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            [(content, *astuple(n)) for n in nodes])
        self.db.executemany('INSERT INTO public_v1.html_element VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            [(content, e.element_index, e.parent_index, e.subtree_end_index,
                              e.child_index, e.tag, e.namespace_uri, e.attributes, e.text_direct) for e in elements])
        return elements

    def cells(self):
        return self.db.execute('''SELECT row_index, column_index, row_span, column_span,
            is_header, text FROM public_v1.html_table_cell ORDER BY node_index''').fetchall()

    def test_simple_table_and_source_identity(self):
        self.load('<table><caption>Annual <b>tuition</b></caption><tr><th>Programme</th><th>Price</th>'
                  '</tr><tr><td>CS</td><td>€12,000</td></tr><tr></tr><tr><td></td></tr></table>')
        self.assertEqual(self.cells(), [(0,0,1,1,True,'Programme'), (0,1,1,1,True,'Price'),
            (1,0,1,1,False,'CS'), (1,1,1,1,False,'€12,000'), (3,0,1,1,False,'')])
        self.assertEqual(self.db.execute('SELECT caption FROM public_v1.html_table').fetchall(), [('Annual tuition',)])
        self.assertEqual(self.db.execute('''SELECT count(*) FROM public_v1.html_table_cell c
            JOIN public_v1.html_element e USING(content_id, node_index)
            JOIN public_v1.html_table t ON t.content_id=c.content_id AND t.node_index=c.table_node_index
            WHERE e.tag IN ('td','th')''').fetchone(), (5,))

    def test_row_and_column_spans(self):
        self.load('<table><tr><th rowspan=2>A</th><th colspan=2>B</th></tr>'
                  '<tr><td>C</td><td>D</td></tr><tr><td colspan=3>E</td></tr></table>')
        self.assertEqual(self.cells(), [(0,0,2,1,True,'A'), (0,1,1,2,True,'B'),
            (1,1,1,1,False,'C'), (1,2,1,1,False,'D'), (2,0,1,3,False,'E')])

    def test_zero_rowspan_groups_and_invalid_attributes(self):
        self.load('<table><tbody><tr><td rowspan=0>A</td><td colspan=0>B</td></tr>'
                  '<tr><td>C</td></tr></tbody><tbody><tr><td rowspan=99>D</td>'
                  '<td colspan="-2">E</td><td rowspan="bad">F</td></tr></tbody></table>')
        self.assertEqual(self.cells(), [(0,0,2,1,False,'A'), (0,1,1,1,False,'B'),
            (1,1,1,1,False,'C'), (2,0,1,1,False,'D'), (2,1,1,1,False,'E'), (2,2,1,1,False,'F')])

    def test_nested_tables_and_exact_text(self):
        self.load('<table><tr><td>before <b>B</b><!--omit--><table><caption></caption>'
                  '<tr><td>inner</td></tr></table> after<script>x</script></td></tr></table><table></table>')
        self.assertEqual([r[-1] for r in self.cells()], ['before B afterx', 'inner'])
        self.assertEqual(self.db.execute('SELECT caption FROM public_v1.html_table ORDER BY node_index').fetchall(),
                         [(None,), ('',), (None,)])

    def test_overlap_fails_explicitly(self):
        self.load('<table><tr><td>A</td><td rowspan=2>B</td></tr>'
                  '<tr><td colspan=2>C</td></tr></table>')
        with self.assertRaisesRegex(duckdb.InvalidInputException, 'overlapping'):
            self.cells()

    def test_content_filter_isolates_invalid_other_content(self):
        self.load('<table><tr><td>good</td></tr></table>', 'good')
        self.load('<table><tr><td>A</td><td rowspan=2>B</td></tr>'
                  '<tr><td colspan=2>C</td></tr></table>', 'bad')
        self.assertEqual(self.db.execute("SELECT text FROM public_v1.html_table_cell WHERE content_id='good'").fetchall(), [('good',)])

    def test_table_filter_isolates_invalid_sibling_table(self):
        elements = self.load('<table><tr><td>good</td></tr></table>'
                  '<table><tr><td>A</td><td rowspan=2>B</td></tr>'
                  '<tr><td colspan=2>C</td></tr></table>')
        table = next(e.element_index for e in elements if e.tag == 'table')
        self.assertEqual(self.db.execute('''SELECT text FROM public_v1.html_table_cell
            WHERE content_id='fixture' AND table_node_index=?''', [table]).fetchall(), [('good',)])

    def test_column_bound_and_span_clamping(self):
        self.load('<table><tr><td colspan=1001>A</td><td colspan=1001>B</td>'
                  '<td colspan=1001>C</td></tr></table>')
        with self.assertRaisesRegex(duckdb.InvalidInputException, '2048 columns'):
            self.cells()

    def test_span_integer_prefix_and_large_value(self):
        self.load('<table><tr><td colspan=" +2tail">A</td><td colspan="'+ '9'*100 +'">B</td></tr></table>')
        self.assertEqual(self.cells(), [(0,0,1,2,False,'A'), (0,2,1,1000,False,'B')])

    def test_cell_limit_is_explicit(self):
        self.load('<table><tr>' + '<td>x</td>'*10001 + '</tr></table>')
        with self.assertRaisesRegex(duckdb.InvalidInputException, '10000 cells'):
            self.cells()

    def test_caption_chooses_first_and_preserves_empty(self):
        self.load('<table><caption>first</caption><caption>second</caption></table>')
        self.assertEqual(self.db.execute('SELECT caption FROM public_v1.html_table').fetchall(), [('first',)])

    def test_footer_remains_in_source_order_and_row_groups_reset(self):
        self.load('<table><tfoot><tr><td rowspan=3>foot</td></tr></tfoot>'
                  '<tbody><tr><td>body</td></tr></tbody></table>')
        self.assertEqual(self.cells(), [(0,0,1,1,False,'foot'), (1,0,1,1,False,'body')])

    def test_output_row_filter_keeps_previous_rowspan(self):
        self.load('<table><tr><td rowspan=2>A</td><td>B</td></tr><tr><td>C</td></tr></table>')
        self.assertEqual(self.db.execute('''SELECT column_index, text
            FROM public_v1.html_table_cell WHERE row_index=1''').fetchall(), [(1, 'C')])
