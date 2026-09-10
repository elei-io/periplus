from pathlib import Path
import unittest

import duckdb

from periplus.query.prose_scalar import prose_scalar

SQL = (Path(__file__).resolve().parents[3] / 'benchmarks/query/cases/prose-mention-counts/query.sql').read_text().replace('public_v1.', '')


class ProseScalarTests(unittest.TestCase):
    def setUp(self):
        self.d = duckdb.connect()
        self.addCleanup(self.d.close)
        self.d.execute('CREATE SCHEMA public_v1; SET schema=public_v1')
        self.d.execute('CREATE TABLE capture(requested_url VARCHAR, content_id VARCHAR, captured_at INTEGER, capture_id INTEGER)')
        self.d.execute('CREATE TABLE prose(content_id VARCHAR, text VARCHAR)')
        self.d.execute("""INSERT INTO prose VALUES
            ('old','AI AI'), ('zero','nothing'), ('shared','AI'),
            ('phrase','artificial intelligence'), ('null',NULL),
            ('boundary','chair artificial intelligences'), ('unused','AI AI AI')""")
        self.d.execute("""INSERT INTO capture VALUES
            ('https://a.test/new-zero','old',1,1),('https://a.test/new-zero','zero',2,2),
            ('https://a.test/shared-1','shared',1,3),('https://a.test/shared-2','shared',1,4),
            ('https://b.test/missing','old',1,5),('https://b.test/missing','missing',2,6),
            ('https://c.test/tie','old',1,7),('https://c.test/tie','phrase',1,8),
            ('https://d.test/null','old',1,9),('https://d.test/null','null',2,10),
            ('https://e.test/boundary','boundary',1,11),
            ('https://a.test/shared-1','shared',1,3)""")

    def compare(self, sql):
        candidate = prose_scalar(sql)
        self.assertIsNotNone(candidate)
        before = self.d.execute(sql)
        columns = before.description
        rows = before.fetchall()
        after = self.d.execute(candidate.sql)
        self.assertEqual(after.description, columns)
        self.assertEqual(after.fetchall(), rows)
        return rows

    def test_latest_eligible_ties_nulls_reused_content_duplicates_and_boundaries(self):
        self.assertEqual(self.compare(SQL), [('a.test',2,2),('b.test',2,1),('c.test',1,1)])
        self.d.execute('DELETE FROM capture')
        self.assertEqual(self.compare(SQL), [])

    def test_aliases_join_direction_qualification_and_patterns(self):
        for sql in (
            SQL.replace('FROM capture JOIN prose', 'FROM public_v1.capture JOIN public_v1.prose'),
            SQL.replace('FROM capture JOIN prose', 'FROM prose JOIN capture'),
            SQL.replace('FROM capture JOIN prose', 'FROM capture c JOIN prose p').replace('regexp_extract_all(text,', 'regexp_extract_all(p.text,'),
            SQL.replace('AI|artificial intelligence', 'robot|machine learning'),
            SQL.replace('len(', 'length('),
            SQL.replace('captured_at DESC', 'captured_at DESC NULLS FIRST'),
        ):
            with self.subTest(sql=sql): self.compare(sql)

    def test_unsupported_forms_stay_unchanged(self):
        for sql in (
            SQL.replace('JOIN prose', 'LEFT JOIN prose'),
            SQL.replace('JOIN prose', 'SEMI JOIN prose'),
            SQL.replace('ORDER BY requested_url,', "WHERE requested_url LIKE '%a.test%' ORDER BY requested_url,"),
            SQL.replace(' AS mentions\n FROM', ' AS mentions, text AS raw\n FROM'),
            SQL.replace('len(regexp_extract_all(text,', 'len(regexp_extract_all(lower(text),'),
            SQL.replace('len(regexp_extract_all(text,', 'len(regexp_extract_all(text || text,'),
            SQL.replace('AS domain', 'AS requested_url'),
            SQL.replace('AS mentions\n FROM', 'AS domain\n FROM'),
            SQL.replace('pages AS', 'prose AS').replace('FROM pages', 'FROM prose'),
            SQL.replace('captured_at DESC, capture_id DESC', 'captured_at DESC'),
            SQL.replace('capture_id DESC', 'capture_id ASC'),
            SQL.replace('pages AS', '__periplus_prose_scalar AS').replace('FROM pages', 'FROM __periplus_prose_scalar'),
            SQL.replace('SELECT DISTINCT ON (requested_url)', 'SELECT'),
            SQL.replace('AS mentions\n FROM', 'AS mentions, random() AS r\n FROM'),
            SQL.replace('pages AS (', 'pages AS MATERIALIZED ('),
            'EXPLAIN ' + SQL,
        ):
            with self.subTest(sql=sql): self.assertIsNone(prose_scalar(sql))
        self.assertIsNone(prose_scalar(SQL, ['unused']))

    def test_materialized_producer_contains_scalar_and_no_join(self):
        import json
        plan = json.loads(self.d.execute('EXPLAIN (FORMAT JSON) ' + prose_scalar(SQL).sql).fetchone()[1])
        cte = plan[0]
        self.assertEqual(cte['name'], 'CTE')
        producer = cte['children'][0]
        self.assertEqual(producer['name'], 'PROJECTION')
        self.assertEqual(producer['extra_info']['Projections'], ['content_id', '__periplus_prose_scalar_value'])
        self.assertNotIn('JOIN', json.dumps(producer))
