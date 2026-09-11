from collections import Counter
import unittest
from unittest.mock import patch

import duckdb

from periplus.query.prose_matches import prose_matches

SQL = """SELECT c.effective_url, left(p.text, 1500) AS preview
FROM public_v1.capture c JOIN public_v1.prose p USING(content_id)
WHERE regexp_matches(lower(p.text), ?)"""


class ProseMatchesTests(unittest.TestCase):
    def setUp(self):
        self.d = duckdb.connect()
        self.addCleanup(self.d.close)
        self.d.execute('CREATE SCHEMA public_v1; SET schema=public_v1')
        self.d.execute('CREATE TABLE capture(content_id VARCHAR,effective_url VARCHAR)')
        self.d.execute("INSERT INTO capture VALUES ('a','one'),('a','two'),('b',NULL),(NULL,'null'),('c','third')")
        self.d.execute('CREATE TABLE prose(content_id VARCHAR,text VARCHAR)')
        self.d.executemany('INSERT INTO prose VALUES (?,?)', [
            ('a','Acquisition criteria café 🐈'), ('a','Acquisition criteria café 🐈'),
            ('b','seeking acquisitions'), ('c',None), ('c',''),
            (None,'acquisition criteria'), ('unmatched','acquisition criteria'),
        ])

    def compare(self, sql=SQL, parameters=('acquisition',)):
        candidate = prose_matches(sql, parameters)
        self.assertIsNotNone(candidate)
        expected = self.d.execute(sql, parameters).fetchall()
        description = self.d.description
        bindings = candidate.select(self.d)
        self.assertIsNotNone(bindings)
        actual = self.d.execute(candidate.sql, bindings).fetchall()
        self.assertEqual(self.d.description, description)
        self.assertEqual(Counter(actual), Counter(expected))

    def test_full_empty_null_unicode_and_duplicate_domains(self):
        for pattern in ('acquisition','missing','.*','café|🐈','^$'):
            for length in (0,1,20,1500,10000):
                with self.subTest(pattern=pattern,length=length):
                    self.compare(SQL.replace('1500',str(length)), [pattern])

    def test_join_direction_aliases_and_literal_predicate(self):
        self.compare(SQL.replace('public_v1.capture c JOIN public_v1.prose p', 'prose p INNER JOIN capture c'))
        self.compare(SQL.replace('c.effective_url', 'c.content_id AS key, c.effective_url AS url'))
        self.compare(SQL.replace('?', "'acquisition'"), [])
        self.compare(SQL.replace('lower(p.text)', 'p.text'))
        self.compare(SQL.replace(' c ', ' "C" ').replace('c.', '"C".'))

    def test_collection_bounds_never_return_partial_keys(self):
        for name, bound in [('MAX_MATCHES',1), ('MAX_MATCH_BYTES',1)]:
            with self.subTest(bound=name), patch('periplus.query.prose_matches.' + name, bound):
                candidate = prose_matches(SQL,['acquisition'])
                self.assertIsNone(candidate.select(self.d))
        candidate = prose_matches(SQL,['acquisition'])
        self.assertFalse(candidate.matches(self.d, {}))

    def test_unproven_sql_remains_native(self):
        cases = [SQL + ' LIMIT 5', SQL + ' ORDER BY preview',
            SQL.replace('SELECT ', 'SELECT DISTINCT '), SQL.replace(' JOIN ', ' LEFT JOIN '),
            SQL.replace('USING(content_id)','ON c.content_id=p.content_id'),
            SQL.replace('left(p.text, 1500)', 'p.text'),
            SQL.replace('left(p.text, 1500)', 'left(p.text, -1)'),
            SQL.replace('left(p.text, 1500)', 'left(p.text, 10001)'),
            SQL.replace('c.effective_url','random()'),
            SQL.replace('c.effective_url','CAST(c.effective_url AS INTEGER)'),
            SQL.replace('c.effective_url','c.*'), SQL.replace('lower(p.text)','upper(p.text)'),
            SQL.replace('lower(p.text)','text'), SQL.replace('?)', "?, 'i')"),
            SQL + " AND c.effective_url='one'", SQL.replace('?', 'p.text'),
            SQL.replace('?', '$1'), SQL.replace('?', '(SELECT ?)'),
            SQL.replace(' AS preview',''), SQL.replace('prose p','prose p(content_id,text)'),
            SQL.replace('capture c','capture c TABLESAMPLE 10%')]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertIsNone(prose_matches(sql,['acquisition']))
