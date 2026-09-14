"""Canonical text semantics and public structured views on persisted elements."""
import unittest
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.registry import BY_NAME
from periplus.platform.catalogue.public import install_public_catalogue
from test_public_catalogue import _LocalCatalogue

class CanonicalElementsTests(unittest.TestCase):
    def setUp(self):
        self.c=_LocalCatalogue();self.addCleanup(self.c.connection.close)
        from periplus.platform.catalogue.schema import expected_columns
        from periplus.platform.catalogue.client import _column_type
        for schema in ('ingest','material'):self.c.connection.execute(f'CREATE SCHEMA {schema}')
        for relation,columns in expected_columns().items():
            definitions=', '.join(f'"{name}" {_column_type(column)}' for name,column in columns.items())
            self.c.connection.execute(f'CREATE TABLE {relation.qualified} ({definitions})')
        install_public_catalogue(self.c)

    def seed(self,html):
        nodes,elements=parse_document(html)
        context=VisitBatchContext((),(),(),{'fixture':elements},{'fixture':nodes},{},frozenset({'fixture'}))
        rows=BY_NAME['html_elements'].rows(context)
        self.c.connection.register('prepared_elements',rows)
        self.c.connection.execute('INSERT INTO material.html_elements SELECT * FROM prepared_elements')
        self.c.connection.unregister('prepared_elements')
        return nodes,elements,rows.to_pylist()

    def test_every_element_matches_parser_and_structural_references(self):
        nodes,elements,rows=self.seed('<!doctype html><!--before--><title>title</title><p> mon<b>key</b>\n café 😀</p><template>hidden<i>inside</i></template><script>x < 2</script><style>a>b{}</style><svg><clipPath><text>Hi</text></clipPath></svg><select><option label="UK">United Kingdom</option></select>')
        by_index={r['node_index']:r for r in rows}
        for e,r in zip(elements,rows):
            expected=''.join(n.value or '' for n in nodes[e.element_index:nodes[e.element_index].subtree_end_index] if n.node_type=='text')
            self.assertEqual(r['text'],expected);self.assertEqual(r['text_direct'],e.text_direct)
            self.assertEqual(r['text_end']-r['text_start'],len(expected))
            if r['parent_index'] is not None:self.assertEqual(r['depth'],by_index[r['parent_index']]['depth']+1)
        self.assertEqual(self.c.connection.execute("SELECT text,text_direct FROM public_v1.html_element WHERE tag='p'").fetchone(),(' monkey\n café 😀',' mon\n café 😀'))
        self.assertEqual(self.c.connection.execute("SELECT text FROM public_v1.html_element WHERE tag='option'").fetchone(),('United Kingdom',))
        self.assertIn('clipPath',{r['tag'] for r in rows})

    def test_nested_list_text_preserves_all_descendants(self):
        self.seed('<ol><li>A<b>B</b>C<ul><li>hidden<ol><li>deeper</li></ol></li></ul>D<i>E</i>F</li><li></li></ol>')
        rows=self.c.connection.execute("SELECT text FROM public_v1.html_element WHERE tag='li' ORDER BY node_index").fetchall()
        self.assertEqual(rows,[('ABChiddendeeperDEF',),('hiddendeeper',),('deeper',),('',)])

    def test_nested_table_text_and_empty_cells(self):
        self.seed('<table><caption>Caption<b> bold</b></caption><tr><td>before<table><tr><td>inner</td></tr></table>after</td><td></td></tr></table>')
        self.assertEqual(self.c.connection.execute("SELECT text FROM public_v1.html_element WHERE tag='caption' ORDER BY node_index").fetchall(),[('Caption bold',)])
        self.assertEqual(self.c.connection.execute("SELECT text FROM public_v1.html_element WHERE tag='td' ORDER BY node_index").fetchall(),[('beforeinnerafter',),('inner',),('',)])

    def test_heading_code_and_textarea_text(self):
        self.seed('<h1>A<b>B</b></h1><pre><code> x\n<b>y</b></code></pre><form><textarea>A&amp;B</textarea></form><h2>last</h2><p>end</p>')
        self.assertEqual(self.c.connection.execute("SELECT text FROM public_v1.html_element WHERE tag IN ('h1','h2') ORDER BY node_index").fetchall(),[('AB',),('last',)])
        self.assertEqual(self.c.connection.execute("SELECT text FROM public_v1.html_element WHERE tag='code'").fetchone(),(' x\ny',))
        self.assertEqual(self.c.connection.execute("SELECT text FROM public_v1.html_element WHERE tag='textarea'").fetchone(),('A&B',))

    def test_removed_surfaces_and_internal_text(self):
        self.seed('<p>one</p>')
        self.assertNotIn('posting',BY_NAME);self.assertNotIn('html_nodes',BY_NAME)
        for sql in ['SELECT * FROM public_v1.html_node',"SELECT * FROM public_v1.search('one')","SELECT * FROM public_v1.subtree_text('fixture',1)"]:
            with self.assertRaises(Exception):self.c.connection.execute(sql)
