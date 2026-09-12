"""Source declarations, form ownership, and option indexing remain distinguishable."""
from dataclasses import astuple
from importlib.resources import files
import unittest

import duckdb

from periplus.materialization.dom.nodes import parse_document


class HtmlFormTests(unittest.TestCase):
    def setUp(self):
        from element_fixture import catalogue
        self.catalogue = catalogue()
        self.db = self.catalogue.connection
        self.addCleanup(self.db.close)

    def load(self, html, content='fixture'):
        from element_fixture import seed
        nodes, elements = seed(self.db, html, content)
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
