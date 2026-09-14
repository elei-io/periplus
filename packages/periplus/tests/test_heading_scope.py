import unittest
from unittest.mock import patch

import duckdb
from sqlglot import parse_one

from periplus.query.heading_scope import heading_scope

SQL = """SELECT h.level,h.text,c.effective_url AS url
FROM html_heading h JOIN capture c USING(content_id)
WHERE requested_url ILIKE '%books.%' AND h.text ILIKE '%Light%'
ORDER BY url,h.level,h.text"""


class HeadingScopeTests(unittest.TestCase):
    def setUp(self):
        self.d = duckdb.connect()
        self.addCleanup(self.d.close)
        self.d.execute('CREATE SCHEMA experimental; SET schema=experimental')
        self.d.execute('CREATE TABLE capture(content_id VARCHAR,requested_url VARCHAR,effective_url VARCHAR)')
        self.d.execute('CREATE TABLE html_heading(content_id VARCHAR,node_index INTEGER,level INTEGER,text VARCHAR)')
        self.d.executemany('INSERT INTO capture VALUES (?,?,?)', [
            ('a', 'https://books.example/a', '/a'), ('a', 'https://books.example/a', '/a'),
            ('b', 'https://books.example/b', '/b'), (None, 'https://books.example/null', '/null'),
            ('c', 'https://other.example/', '/c'), ("x'[]", 'https://books.example/x', '/x'),
        ])
        self.d.executemany('INSERT INTO html_heading VALUES (?,?,?,?)', [
            ('a',1,1,'Light'), ('a',2,2,'Daylight'), ('b',1,1,'lighthouse'),
            ('b',2,2,None), ('c',1,1,'Light'), (None,1,1,'Light'), ("x'[]",1,1,'Light'),
        ])

    def test_complete_results_duplicates_nulls_quotes_and_substrings(self):
        for sql in (SQL, SQL.replace('USING(content_id)', 'ON h.content_id=c.content_id'),
                    SQL.replace('%books.%', '%missing.%'), SQL+' LIMIT 3',
                    SQL.replace('h.', '"select".').replace('html_heading h', 'html_heading "select"'),
                    SQL.replace('c.', '"where".').replace('capture c', 'capture "where"'),
                    SQL.replace('html_heading h JOIN capture c', 'capture c JOIN html_heading h')):
            with self.subTest(sql=sql):
                scope = heading_scope(parse_one(sql, read='duckdb'), [])
                self.assertIsNotNone(scope)
                rewritten = scope.resolve(self.d)
                expected = self.d.execute(sql).fetchall()
                expected_types = self.d.description
                self.assertEqual(self.d.execute(rewritten).fetchall(), expected)
                self.assertEqual(self.d.description, expected_types)
        self.assertEqual(len(self.d.execute(SQL).fetchall()), 6)

    def test_selection_limits_do_not_silently_truncate(self):
        scope = heading_scope(parse_one(SQL, read='duckdb'), [])
        with patch('periplus.query.heading_scope.MAX_CONTENTS', 1):
            self.assertIsNone(scope.resolve(self.d))
        with patch('periplus.query.heading_scope.MAX_KEY_BYTES', 1):
            self.assertIsNone(scope.resolve(self.d))

    def test_unsupported_shapes_remain_native(self):
        for sql, params in [
            (SQL.replace('JOIN capture', 'LEFT JOIN capture'), []),
            (SQL.replace('USING(content_id)', 'ON h.node_index=c.content_id'), []),
            (SQL.replace("'%books.%'", '?'), ['%books.%']),
            (SQL.replace(' AND ', ' OR '), []),
            (SQL.replace(" AND h.text ILIKE '%Light%'", ''), []),
            ('WITH x AS (SELECT * FROM capture) '+SQL, []),
            (SQL.replace('h.level,h.text,c.effective_url AS url','count(*)'), []),
            (SQL.replace('html_heading h','html_heading h TABLESAMPLE 10%'), []),
            (SQL.replace('capture c', 'capture c(a,b,c)'), []),
            (SQL.replace('h.level', 'experimental.h.level'), []),
        ]:
            with self.subTest(sql=sql):
                self.assertIsNone(heading_scope(parse_one(sql, read='duckdb'), params))
