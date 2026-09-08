"""Differential checks over real public views and materialization parsers."""
from collections import Counter
import json
import unittest

import duckdb

from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import PROJECTIONS
from periplus.materialization.projections.html_elements import project as elements_project
from periplus.materialization.projections.html_nodes import project as nodes_project
from periplus.materialization.projections.prose import project as prose_project
from periplus.platform.catalogue.client import _column_type
from periplus.platform.catalogue.public import public_objects
from periplus.platform.catalogue.schema import expected_columns
from periplus.query.content_scope import content_scope, _source
from periplus.query.validation import _bounded_query


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
        for name, project in [('html_nodes', nodes_project), ('html_elements', elements_project), ('prose', prose_project)]:
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

    def test_every_reviewed_view_selective_empty_and_full_domains(self):
        for item in public_objects():
            if not item.content_local:
                continue
            for pattern in ('%robot%', '%absent%', '%'):
                with self.subTest(view=item.name, pattern=pattern):
                    _, rows = self.assertEquivalent(
                        f'SELECT e.* FROM prose p JOIN {item.name} e USING (content_id) WHERE p.text ILIKE ?', [pattern])
                    if pattern == '%robot%':
                        self.assertTrue(rows, item.name)

    def test_complete_partitions_multiple_targets_and_duplicate_drivers(self):
        self.assertEquivalent("""SELECT c.effective_url AS url, m.value AS title, h.*
          FROM prose p JOIN capture c USING (content_id)
          JOIN html_metadata m USING (content_id) JOIN html_heading h USING (content_id)
          WHERE p.text ILIKE '%robot%' AND m.name = 'title'""")
        self.assertEquivalent("""SELECT s.* FROM html_node n JOIN html_section s ON n.content_id = s.content_id
          WHERE n.value = 'robot'""")
        self.assertEquivalent("""SELECT * FROM capture c JOIN html_metadata m USING (content_id)
          WHERE c.effective_url LIKE '%/a'""")
        self.assertEquivalent("""SELECT s.* FROM html_element e JOIN html_section s USING (content_id)
          WHERE e.tag = 'p' AND e.text_direct = 'robot'""")

    def test_parameters_identifiers_and_output_contract(self):
        self.assertEquivalent("""SELECT ? AS marker, m.value AS title, '?' AS literal
          FROM public_v1.prose AS "P" JOIN public_v1.html_metadata AS "m" USING (content_id)
          WHERE "P".text ILIKE ? AND m.name = ? /* ? stays a comment */
          ORDER BY title""", ['marker', '%robot%', 'title'])
        self.assertEquivalent("""SELECT DISTINCT m.value AS __periplus_scope_selected
          FROM prose p JOIN html_metadata m USING (content_id)
          WHERE (p.text ILIKE '%robot%' OR p.text = '') AND m.name = 'title'""")
        self.assertEquivalent("""SELECT lower(m.value) AS title FROM prose p
          JOIN html_metadata m USING (content_id) WHERE lower(p.text) LIKE '%robot%'""")

    def test_unsupported_queries_stay_unchanged(self):
        base = "SELECT m.* FROM prose p JOIN html_metadata m USING (content_id) WHERE p.text ILIKE '%robot%'"
        cases = [
            base + ' LIMIT 10', base + ' OFFSET 1', base.replace(' JOIN ', ' LEFT JOIN '),
            base.replace(' JOIN ', ' FULL JOIN '), base.replace(' USING (content_id)', ' ON true'),
            base.replace('m.*', 'count(*)'), base.replace('m.*', 'lower(m.value)'),
            base.replace('m.*', 'row_number() OVER ()'), base.replace('m.*', 'random() AS r'),
            base.replace("p.text ILIKE '%robot%'", "p.text::INTEGER > 0"),
            base.replace("p.text ILIKE '%robot%'", "m.name = 'title'"),
            base.replace("p.text ILIKE '%robot%'", "p.text ILIKE '%robot%' OR m.name = 'title'"),
            base.replace('prose p', '(SELECT * FROM prose) p'),
            'WITH p AS (SELECT * FROM prose) ' + base,
            base.replace("'%robot%'", '$1'),
            base.replace('html_metadata', 'html_table_cell'),
            base.replace('html_metadata', 'html_list_item'),
            base.replace('USING (content_id)', 'ON p.content_id = m.content_id AND m.name = \'title\''),
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.assertIsNone(content_scope(sql))

    def test_changed_or_missing_installed_definitions_disable_optimization(self):
        scoped = content_scope("SELECT m.* FROM prose p JOIN html_metadata m USING (content_id) WHERE p.text = 'robot'")
        for name in scoped.definitions:
            changed = dict(self.installed)
            changed[name] = 'CREATE VIEW x AS SELECT 42'
            self.assertFalse(scoped.matches(self.db, changed))
            del changed[name]
            self.assertFalse(scoped.matches(self.db, changed))
