"""Differential checks over real public views and materialization parsers."""
from collections import Counter
from itertools import permutations
import json
import unittest

import duckdb

from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import PROJECTIONS
from periplus.platform.catalogue.client import _column_type
from periplus.platform.catalogue.public import public_objects
from periplus.platform.catalogue.schema import expected_columns
from periplus.query.content_scope import content_scope, capture_heading_scope, _source
from periplus.query.validation import _bounded_query
from periplus.query.scope_plan import shared_html_inputs


class ContentScopeTests(unittest.TestCase):

    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)
        for schema in ('ingest', 'material', 'public_v1'):
            self.db.execute(f'CREATE SCHEMA {schema}')
        for relation, columns in expected_columns().items():
            self.db.execute(f'CREATE TABLE {relation.qualified} (' + ', '.join(
                f'"{name}" {_column_type(column)}' for name, column in columns.items()) + ')')
        for item in public_objects():
            self.db.execute(_source(item.resource))
        html = '''<title>A mixed title</title><meta name="title" content="Other title">
          <body><h1>Robot <em>careers</em></h1><section><h2>Benefits</h2>
          <p>robot</p><p>robot</p><p>Full <b>medical</b> coverage</p></section>
          <h2>Contact</h2><p>Apply here</p><pre><code>print('robot')</code></pre>
          <form id="apply"><label for="name">Your name</label><input id="name" name="name">
          <select name="role"><optgroup label="Jobs"><option>A role</option></optgroup></select>
          <button>Apply</button></form><input form="apply" name="outside">
          <ul><li>One <b>item</b></li><li>Two</li></ul>
          <table><caption>Jobs</caption><tr><th>Role</th></tr><tr><td>Engineer</td></tr></table>
          <img src="photo.jpg" alt="Team"><script type="application/ld+json">{"name":"Robot"}</script>
          <script type="application/ld+json">invalid JSON</script></body>'''
        parsed = {key: parse_document(text) for key, text in {
            'a': html, 'b': html.replace('robot', 'flower').replace('Robot', 'Flower'),
            'empty': '<body></body>',
        }.items()}
        context = VisitBatchContext((), (), (), {k: v[1] for k, v in parsed.items()},
                                    {k: v[0] for k, v in parsed.items()}, {}, frozenset(parsed))
        for name, project in [(spec.name, spec.rows) for spec in PROJECTIONS
                              if spec.name not in {"term", "content_posting", "node_posting"}]:
            self.db.register('projection_rows', project(context))
            self.db.execute(f'INSERT INTO material.{name} SELECT * FROM projection_rows')
            self.db.unregister('projection_rows')
        for content in ('a', 'a', 'b', 'empty'):
            self.db.execute("INSERT INTO ingest.visits (visit_id, document_id, requested_url, effective_url, outcome) VALUES (uuid(), uuid(), ?, ?, 'success')", [f'https://example.com/{content}', f'https://example.com/{content}'])
        self.db.execute("INSERT INTO ingest.documents (document_id, visit_id, content_sha256, detected_media_type) SELECT document_id, visit_id, split_part(effective_url, '/', 4), 'text/html' FROM ingest.visits")
        self.db.execute('USE public_v1')
        self.installed = dict(self.db.execute("SELECT view_name, sql FROM duckdb_views() WHERE schema_name='public_v1'").fetchall())

    def assertEquivalent(self, sql, parameters=()):
        scoped = content_scope(sql)
        self.assertIsNotNone(scoped, sql)
        self.assertTrue(scoped.matches(self.db, self.installed))
        _bounded_query(scoped.sql)
        before = self.db.execute(sql, parameters)
        description = before.description
        rows = before.fetchall()
        after = self.db.execute(scoped.sql, parameters)
        self.assertEqual(description, after.description)
        encode = lambda rows: Counter(json.dumps(row, default=str, sort_keys=True) for row in rows)
        self.assertEqual(encode(rows), encode(after.fetchall()), sql)
        return scoped, rows

    def test_activation_requires_exact_capture_url_and_measured_relations(self):
        sql = "SELECT h.text FROM capture c JOIN html_heading h USING(content_id) WHERE c.effective_url = ?"
        self.assertIsNotNone(capture_heading_scope(sql, ["https://example.com/a"]))
        self.assertIsNone(capture_heading_scope(sql, [1]))
        self.assertIsNone(capture_heading_scope(sql + " AND c.content_id = 1", ["https://example.com/a"]))
        self.assertIsNone(capture_heading_scope(sql.replace("USING(content_id)", "ON h.content_id=c.content_id AND h.text=1"), ["https://example.com/a"]))
        self.assertIsNotNone(capture_heading_scope(sql.replace('c.effective_url = ?', "'https://example.com/a' = c.effective_url")))
        for other in (sql.replace(' = ?', ' LIKE ?'),
                      sql.replace('html_heading', 'html_section'),
                      sql.replace('c.effective_url = ?', 'c.effective_url = ? OR h.level = 1'),
                      sql + ' LIMIT 1', sql.replace('JOIN', 'LEFT JOIN')):
            self.assertIsNone(capture_heading_scope(other), other)

    def test_research_materialized_inputs_and_heading_discovery(self):
        for heading_driver in (False, True):
            for materialize_inputs in (False, True):
                for pattern in ('%Robot%', '%Benefits%', '%', '%absent%'):
                    sql = """SELECT c.capture_id, h.text AS heading, s.*
                      FROM capture c JOIN html_heading h USING(content_id)
                      JOIN html_section s ON s.content_id=h.content_id
                       AND s.heading_node_index=h.node_index
                      WHERE h.text LIKE ? AND c.effective_url LIKE ?
                      ORDER BY c.capture_id, h.node_index"""
                    params = [pattern, 'https://example.com/%']
                    candidate = content_scope(sql, heading_driver=heading_driver,
                                              materialize_inputs=materialize_inputs)
                    self.assertIsNotNone(candidate)
                    self.assertTrue(candidate.matches(self.db, self.installed))
                    before = self.db.execute(sql, params)
                    description, rows = before.description, before.fetchall()
                    after = self.db.execute(candidate.sql, params)
                    self.assertEqual(description, after.description)
                    self.assertEqual(rows, after.fetchall())

    def test_every_reviewed_view_selective_empty_and_full_domains(self):
        for item in public_objects():
            if not item.content_local:
                continue
            for pattern in ('%robot%', '%absent%', '%'):
                with self.subTest(view=item.name, pattern=pattern):
                    _, rows = self.assertEquivalent(
                        f'SELECT e.* FROM html_node p JOIN {item.name} e USING (content_id) WHERE p.text ILIKE ?', [pattern])
                    self.assertEquivalent(
                        f'SELECT e.* FROM {item.name} e JOIN html_node p USING (content_id) WHERE p.text ILIKE ?', [pattern])
                    if pattern == '%robot%':
                        self.assertTrue(rows, item.name)

    def test_complete_partitions_multiple_targets_and_duplicate_drivers(self):
        self.assertEquivalent("""SELECT c.effective_url AS url, m.value AS title, h.*
          FROM html_node p JOIN capture c USING (content_id)
          JOIN html_metadata m USING (content_id) JOIN html_heading h USING (content_id)
          WHERE p.text ILIKE '%robot%' AND m.name = 'title'""")
        self.assertEquivalent("""SELECT s.* FROM html_node n JOIN html_section s ON n.content_id = s.content_id
          WHERE n.value = 'robot'""")
        self.assertEquivalent("""SELECT * FROM capture c JOIN html_metadata m USING (content_id)
          WHERE c.effective_url LIKE '%/a'""")
        self.assertEquivalent("""SELECT s.* FROM html_element e JOIN html_section s USING (content_id)
          WHERE e.tag = 'p' AND e.text_direct = 'robot'""")

    def test_join_orders_preserve_rows_parameters_and_complete_partitions(self):
        for order in permutations(('html_heading h', 'capture c', 'html_node p')):
            relations = ' JOIN '.join([order[0], *(t + ' USING (content_id)' for t in order[1:])])
            with self.subTest(order=order):
                _, rows = self.assertEquivalent(
                    'SELECT ? AS marker, c.effective_url AS url, h.level, h.text AS heading FROM '
                    + relations + ' WHERE p.text ILIKE ? AND h.level = ?', ['marker', '%robot%', 1])
                self.assertEqual(len(rows), 10)  # Five matching text nodes across two captures.
                self.assertEquivalent('SELECT * FROM ' + relations + " WHERE p.text ILIKE '%robot%'")
        self.assertEquivalent("""SELECT h.*, s.* FROM html_heading h
          JOIN html_section s ON h.content_id = s.content_id
          JOIN capture c ON c.content_id = h.content_id
          JOIN html_node p ON p.content_id = s.content_id
          WHERE p.text ILIKE '%robot%' AND h.level = 1""")
        self.assertEquivalent("""SELECT s.* FROM html_section s
          JOIN html_node n ON s.content_id = n.content_id WHERE n.value = 'robot'""")
        self.assertEquivalent("""SELECT m.* FROM html_metadata m
          JOIN capture c USING (content_id) WHERE c.effective_url LIKE '%/a'""")
        self.assertEquivalent("""SELECT s.* FROM html_section s JOIN html_element e USING (content_id)
          WHERE e.tag = 'p' AND e.text_direct = 'robot'""")

    def test_joined_driver_does_not_relax_safety_rules(self):
        base = "SELECT h.* FROM html_heading h JOIN html_node p USING (content_id) WHERE p.text ILIKE '%robot%'"
        for sql in (base.replace(' JOIN ', ' LEFT JOIN '), base.replace(' JOIN ', ' FULL JOIN '),
                    base.replace('USING (content_id)', 'ON true'), base + ' LIMIT 1',
                    base.replace("p.text ILIKE '%robot%'", "p.text ILIKE '%robot%' OR h.level = 1")):
            with self.subTest(sql=sql):
                self.assertIsNone(content_scope(sql))

    def test_compound_joins_across_reviewed_views(self):
        for item in public_objects():
            if not item.content_local:
                continue
            for pattern in ('%robot%', '%absent%', '%'):
                for reverse in (False, True):
                    sources = (f'{item.name} e JOIN html_node p' if reverse
                               else f'html_node p JOIN {item.name} e')
                    with self.subTest(view=item.name, pattern=pattern, reverse=reverse):
                        self.assertEquivalent(
                            f'SELECT e.* FROM {sources} ON p.content_id = e.content_id '
                            'AND (e.content_id = ? OR e.content_id IS NULL) '
                            'WHERE p.text ILIKE ?', ['a', pattern])

    def test_compound_heading_sections_keep_complete_partitions_and_order(self):
        for condition in (
            's.content_id = h.content_id AND s.heading_node_index = h.node_index',
            '(s.heading_node_index = h.node_index AND (h.level = ? AND (h.content_id) = (s.content_id)))',
        ):
            sql = f'''SELECT c.effective_url, h.text AS heading, s.* FROM capture c
                JOIN html_heading h USING (content_id) JOIN html_section s ON {condition}
                WHERE c.effective_url LIKE ? AND lower(h.text) LIKE ?
                ORDER BY c.effective_url, s.heading_node_index'''
            parameters = ([2] if '?' in condition else []) + ['%/a', '%benefits%']
            scoped, rows = self.assertEquivalent(sql, parameters)
            self.assertEqual(len(rows), 2)  # Shared content, separate captures.
            self.assertEqual(rows, self.db.execute(scoped.sql, parameters).fetchall())
            contact = self.db.execute("SELECT node_index FROM html_heading WHERE content_id='a' AND text='Contact'").fetchone()[0]
            self.assertTrue(all(row[-1] == contact for row in rows))

    def test_compound_join_null_and_false_residuals(self):
        for residual in ('m.value IS NULL', 'm.value = NULL', 'false',
                         "(m.name = 'title' OR m.value IS NULL)"):
            self.assertEquivalent(f'''SELECT c.effective_url, m.* FROM capture c
                JOIN html_metadata m ON c.content_id = m.content_id AND {residual}
                WHERE c.effective_url LIKE '%/a' ''')

    def test_parameters_identifiers_and_output_contract(self):
        self.assertEquivalent("""SELECT ? AS marker, m.value AS title, '?' AS literal
          FROM public_v1.html_node AS "P" JOIN public_v1.html_metadata AS "m" USING (content_id)
          WHERE "P".text ILIKE ? AND m.name = ? /* ? stays a comment */
          ORDER BY title""", ['marker', '%robot%', 'title'])
        self.assertEquivalent("""SELECT DISTINCT m.value AS __periplus_scope_selected
          FROM html_node p JOIN html_metadata m USING (content_id)
          WHERE (p.text ILIKE '%robot%' OR p.text = '') AND m.name = 'title'""")
        self.assertEquivalent("""SELECT lower(m.value) AS title FROM html_node p
          JOIN html_metadata m USING (content_id) WHERE lower(p.text) LIKE '%robot%'""")

    def test_unsupported_queries_stay_unchanged(self):
        base = "SELECT m.* FROM html_node p JOIN html_metadata m USING (content_id) WHERE p.text ILIKE '%robot%'"
        cases = [
            base + ' LIMIT 10', base + ' OFFSET 1', base.replace(' JOIN ', ' LEFT JOIN '),
            base.replace(' JOIN ', ' FULL JOIN '), base.replace(' USING (content_id)', ' ON true'),
            base.replace('m.*', 'count(*)'), base.replace('m.*', 'lower(m.value)'),
            base.replace('m.*', 'row_number() OVER ()'), base.replace('m.*', 'random() AS r'),
            base.replace("p.text ILIKE '%robot%'", "p.text::INTEGER > 0"),
            base.replace("p.text ILIKE '%robot%'", "m.name = 'title'"),
            base.replace("p.text ILIKE '%robot%'", "p.text ILIKE '%robot%' OR m.name = 'title'"),
            base.replace('html_node p', '(SELECT * FROM html_node) p'),
            'WITH p AS (SELECT * FROM html_node) ' + base,
            base.replace("'%robot%'", '$1'),
            base.replace('html_metadata', 'html_table_cell'),
            base.replace('html_metadata', 'html_list_item'),
            base.replace('USING (content_id)', "ON p.content_id = m.content_id OR m.name = 'title'"),
            base.replace('USING (content_id)', "ON (p.content_id = m.content_id OR m.name = 'title') AND m.value IS NOT NULL"),
            base.replace('USING (content_id)', "ON p.content_id <> m.content_id AND m.name = 'title'"),
            base.replace('USING (content_id)', "ON m.content_id = m.content_id AND m.name = 'title'"),
            base.replace('USING (content_id)', "ON p.content_id = m.content_id AND random() > 0"),
            base.replace('USING (content_id)', "ON p.content_id = m.content_id AND CAST(m.value AS INTEGER) > 0"),
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertIsNone(content_scope(sql))

    def test_changed_or_missing_installed_definitions_disable_optimization(self):
        scoped = content_scope("SELECT m.* FROM html_node p JOIN html_metadata m USING (content_id) WHERE p.text = 'robot'")
        for name in scoped.definitions:
            changed = dict(self.installed)
            changed[name] = 'CREATE VIEW x AS SELECT 42'
            self.assertFalse(scoped.matches(self.db, changed))
            del changed[name]
            self.assertFalse(scoped.matches(self.db, changed))

    def test_shared_input_plan_regression_and_targeted_alternative(self):
        sql = """SELECT c.effective_url, h.text AS heading, s.* FROM capture c
            JOIN html_heading h USING(content_id) JOIN html_section s
            ON s.content_id=h.content_id AND s.heading_node_index=h.node_index
            WHERE c.effective_url LIKE '%/a' ORDER BY c.effective_url, s.heading_node_index"""
        scoped = content_scope(sql)
        expected = self.db.execute(sql).fetchall()

        def walk(node):
            yield node
            for child in node.get('children', []):
                yield from walk(child)

        for disabled in ('', 'common_subplan'):
            with self.subTest(disabled=disabled):
                self.db.execute('SET disabled_optimizers=?', [disabled])
                executable = _bounded_query(scoped.sql)
                raw = self.db.execute('EXPLAIN (FORMAT JSON) ' + executable).fetchone()[-1]
                self.assertEqual(shared_html_inputs(raw, key_cte=scoped.key_cte),
                                 ('html_nodes',) if not disabled else ())
                self.assertEqual(self.db.execute(executable).fetchall(), expected)
                profile = json.loads(self.db.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + executable).fetchone()[-1])
                nodes = list(walk(profile))
                shared = [n for n in nodes if str(n.get('extra_info', {}).get('CTE Name', '')).startswith('__common_subplan_')]
                # Stored normalized tags let the optimizer share a node producer;
                # disabling common_subplan removes that barrier.
                self.assertEqual(bool(shared), not bool(disabled))
                windows = [n['operator_cardinality'] for n in nodes if n.get('operator_name') == 'WINDOW']
                self.assertEqual(windows, [3])  # Complete selected-document partition.
        self.db.execute("SET disabled_optimizers=''")

    def test_optimizer_alternative_across_views_and_selectivity(self):
        for item in public_objects():
            if not item.content_local:
                continue
            for pattern in ('%robot%', '%absent%', '%'):
                with self.subTest(view=item.name, pattern=pattern):
                    sql = f'''SELECT c.effective_url, e.* FROM html_node p JOIN capture c USING(content_id)
                        JOIN {item.name} e ON e.content_id=c.content_id AND e.content_id IS NOT NULL
                        WHERE p.text ILIKE ?'''
                    scoped = content_scope(sql)
                    before = self.db.execute(scoped.sql, [pattern])
                    description = before.description
                    rows = Counter(map(repr, before.fetchall()))
                    self.db.execute("SET disabled_optimizers='common_subplan'")
                    try:
                        after = self.db.execute(scoped.sql, [pattern])
                        self.assertEqual(after.description, description)
                        self.assertEqual(Counter(map(repr, after.fetchall())), rows)
                    finally:
                        self.db.execute("SET disabled_optimizers=''")
