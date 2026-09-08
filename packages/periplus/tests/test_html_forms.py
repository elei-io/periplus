"""Source declarations, form ownership, and option indexing remain distinguishable."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlFormTests(unittest.TestCase):
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
            attributes MAP(VARCHAR, VARCHAR))''')
        for name in ('html_form','html_form_control','html_select_option'):
            self.db.execute(files('periplus.platform.catalogue').joinpath('sql/public_v1/views/'+name+'.sql').read_text())

    def load(self, html, content='fixture'):
        nodes, elements = parse_document(html)
        self.db.executemany('INSERT INTO public_v1.html_node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           [(content, *astuple(n)) for n in nodes])
        self.db.executemany('INSERT INTO public_v1.html_element VALUES (?, ?, ?, ?, ?, ?, ?)',
                           [(content,e.element_index,e.parent_index,e.subtree_end_index,e.tag,e.namespace_uri,e.attributes) for e in elements])
        return elements

    def test_form_attributes_and_explicit_ownership(self):
        elements=self.load('<input name="before" form="f"><form id="f" action="../send" method="POST" enctype="" target="_blank">'
                           '<input name="inside"><input name="missing" form="absent"><input name="empty" form="">'
                           '<input name="other" form="g"></form><form id="g"></form><input name="orphan">')
        forms=[e.element_index for e in elements if e.tag=='form']
        self.assertEqual(self.db.execute('SELECT name,form_node_index FROM public_v1.html_form_control ORDER BY node_index').fetchall(),
                         [('before',forms[0]),('inside',forms[0]),('missing',None),('empty',None),('other',forms[1]),('orphan',None)])
        self.assertEqual(self.db.execute('SELECT action,method,enctype,target FROM public_v1.html_form ORDER BY node_index').fetchall(),
                         [('../send','POST','','_blank'),(None,None,None,None)])

    def test_first_matching_id_must_be_a_form(self):
        elements=self.load('<div id="duplicate"></div><form id="duplicate"><input name="blocked" form="duplicate"></form>'
                           '<form id="twice"></form><form id="twice"></form><input name="first" form="twice">')
        first=next(e.element_index for e in elements if e.tag=='form' and e.attributes.get('id')=='twice')
        self.assertEqual(self.db.execute('SELECT name,form_node_index FROM public_v1.html_form_control ORDER BY node_index').fetchall(),
                         [('blocked',None),('first',first)])

    def test_declared_flags_and_textarea_default_text(self):
        self.load('<form><fieldset disabled><input name="child" required="false" value="">'
                  '</fieldset><textarea name="note" readonly> A &amp; B </textarea>'
                  '<select name="choice" multiple></select><button name="submit">Send</button>'
                  '<output name="out">result</output><object name="obj"></object></form>')
        self.assertEqual(self.db.execute("SELECT type,value,required,disabled FROM public_v1.html_form_control WHERE name='child'").fetchall(),[(None,'',True,False)])
        self.assertEqual(self.db.execute("SELECT value,readonly FROM public_v1.html_form_control WHERE name='note'").fetchall(),[(' A & B ',True)])
        self.assertEqual(self.db.execute("SELECT value,multiple FROM public_v1.html_form_control WHERE name='choice'").fetchall(),[(None,True)])
        self.assertEqual(self.db.execute('SELECT count(*) FROM public_v1.html_form_control').fetchone(),(7,))

    def test_options_groups_flags_empty_and_missing_values(self):
        self.load('<select><option> First <optgroup disabled><option selected="false" value="">Second'
                  '<option disabled value="x"> Third </optgroup><option></select>'
                  '<datalist><option value="excluded"></datalist><option>orphan</option>')
        self.assertEqual(self.db.execute('SELECT option_index,value,text,selected,disabled FROM public_v1.html_select_option ORDER BY node_index').fetchall(),
                         [(0,None,' First ',False,False),(1,'','Second',True,False),(2,'x',' Third ',False,True),(3,None,'',False,False)])
        self.assertEqual(self.db.execute('SELECT option_index FROM public_v1.html_select_option WHERE option_index=2').fetchall(),[(2,)])

    def test_content_isolation_and_types(self):
        self.load('<form id="f"><input form="f"><select><option>A</select></form>')
        self.load('<form id="f"><input form="f"></form>', 'other')
        self.assertEqual(self.db.execute("SELECT count(*) FROM public_v1.html_form_control WHERE content_id='fixture'").fetchone(),(2,))
        for name,types in [('html_form',['VARCHAR','INTEGER']+['VARCHAR']*6),
                           ('html_form_control',['VARCHAR','INTEGER','INTEGER']+['VARCHAR']*4+['BOOLEAN']*4),
                           ('html_select_option',['VARCHAR','INTEGER','INTEGER','INTEGER','VARCHAR','VARCHAR','BOOLEAN','BOOLEAN'])]:
            self.assertEqual([r[1] for r in self.db.execute('DESCRIBE public_v1.'+name).fetchall()],types)
