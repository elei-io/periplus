import unittest
from unittest.mock import patch
import duckdb
from periplus.query.selected_content import selected_content
from periplus.query.content_scope import _source

class SelectedContentTests(unittest.TestCase):
    def setUp(self):
        self.d = duckdb.connect()
        self.addCleanup(self.d.close)
        self.d.execute('CREATE SCHEMA public_v1')
        self.d.execute('SET schema=public_v1')
        self.d.execute('CREATE TABLE capture(capture_id VARCHAR,content_id VARCHAR,effective_url VARCHAR)')
        self.d.execute("INSERT INTO capture VALUES ('a','one','yes'),('b','one','yes'),('c','two','yes'),('d',NULL,'yes'),('e','other','no')")
        self.d.execute('CREATE TABLE html_element(content_id VARCHAR,node_index INTEGER,subtree_end_index INTEGER,tag VARCHAR,namespace VARCHAR,attributes MAP(VARCHAR,VARCHAR),text_direct VARCHAR)')
        self.d.execute("INSERT INTO html_element VALUES ('one',1,3,'h1','http://www.w3.org/1999/xhtml',map(),'Heading'),('one',4,6,'h2','http://www.w3.org/1999/xhtml',map(),'Child'),('one',7,8,'title','http://www.w3.org/1999/xhtml',map(),'Title'),('other',1,3,'h1','http://www.w3.org/1999/xhtml',map(),'Other')")
        self.d.execute('CREATE TABLE html_node(content_id VARCHAR,node_index INTEGER,subtree_end_index INTEGER,node_type VARCHAR,value VARCHAR)')
        self.d.execute("INSERT INTO html_node VALUES ('one',0,9,'document',NULL),('one',2,3,'text','Heading'),('one',5,6,'text','Child'),('other',0,4,'document',NULL),('other',2,3,'text','Other')")
        self.d.execute('CREATE TABLE prose(content_id VARCHAR,text VARCHAR)')
        self.d.execute("INSERT INTO prose VALUES ('one','Body'),('two',NULL),('other','Unselected')")
        for name in ['html_heading','html_metadata','html_section']:
            self.d.execute(_source('views/'+name+'.sql'))

    def query(self, relation='html_heading'):
        index='heading_node_index' if relation=='html_section' else 'node_index'
        return f'''WITH scoped AS (SELECT capture_id,content_id,effective_url FROM public_v1.capture WHERE effective_url = ?)
        SELECT s.capture_id,h.* FROM scoped s LEFT JOIN public_v1.{relation} h USING(content_id)
        ORDER BY s.capture_id,h.{index}'''

    def compare(self, sql, parameters):
        selected = selected_content(sql, parameters)
        self.assertIsNotNone(selected)
        expected = self.d.execute(sql, parameters).fetchall()
        types = self.d.description
        keys = selected.select(self.d)
        actual = self.d.execute(selected.sql, {**selected.parameters, selected.key_parameter: keys}).fetchall()
        self.assertEqual(actual, expected)
        self.assertEqual(self.d.description, types)
        return selected, keys

    def test_families_empty_duplicates_nulls_and_left_joins(self):
        for relation in ['html_heading','html_metadata','html_section']:
            for value in ['yes','no','missing']:
                with self.subTest(relation=relation,value=value):
                    self.compare(self.query(relation), [value])

    def test_heading_aggregate_cte_and_prose(self):
        sql='''WITH scoped AS (SELECT capture_id,content_id FROM capture WHERE effective_url = ?),
        first_heading AS (SELECT s.capture_id,min(h.node_index) AS node_index FROM scoped s JOIN html_heading h USING(content_id) WHERE trim(h.text) <> '' GROUP BY s.capture_id)
        SELECT s.capture_id,h.text,left(p.text,?) AS prefix FROM scoped s
        JOIN prose p USING(content_id) LEFT JOIN first_heading f USING(capture_id)
        LEFT JOIN html_heading h ON h.content_id=s.content_id AND h.node_index=f.node_index ORDER BY s.capture_id'''
        selected, keys = self.compare(sql,['yes',2])
        self.assertEqual(selected.selection_parameters, {'1':'yes'})
        self.assertEqual(set(keys), {'one','two'})

    def test_collection_bound_and_definition_mismatch(self):
        selected=selected_content(self.query(), ['yes'])
        with patch('periplus.query.selected_content.MAX_KEYS',1):
            self.assertIsNone(selected.select(self.d))
        with patch('periplus.query.selected_content.MAX_KEY_BYTES',1):
            self.assertIsNone(selected.select(self.d))
        self.assertFalse(selected.matches(self.d, {}))

    def test_unsafe_shapes_stay_native(self):
        sql=self.query()
        for bad in [sql.replace('LEFT JOIN','FULL JOIN'),sql.replace('USING(content_id)','ON TRUE'),
                    sql.replace('FROM scoped s','FROM html_heading s'),
                    sql.replace('capture_id,content_id,effective_url','capture_id,lower(content_id) AS content_id,effective_url'),
                    sql.replace('SELECT s.capture_id,h.*','SELECT random(),h.*'),
                    sql.replace('SELECT s.capture_id,h.*','SELECT CAST(h.text AS INTEGER)'),
                    sql.replace('WITH scoped','WITH RECURSIVE scoped'),
                    sql.replace('effective_url = ?', 'effective_url = $1')]:
            with self.subTest(sql=bad):
                self.assertIsNone(selected_content(bad,['yes']))

    def test_request_uuid_parameter_and_aliases(self):
        self.d.execute('ALTER TABLE capture ADD COLUMN request_ids UUID[]')
        self.d.execute("UPDATE capture SET request_ids=['00000000-0000-0000-0000-000000000001'::UUID] WHERE effective_url='yes'")
        sql=self.query().replace('effective_url = ?', 'list_contains(request_ids, ?::UUID)')
        self.compare(sql,['00000000-0000-0000-0000-000000000001'])
