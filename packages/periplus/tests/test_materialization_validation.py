import unittest
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import duckdb
import pyarrow as pa
from periplus.materialization.registry import PROJECTIONS
from periplus.materialization.validation import at_snapshot, validation_statements

class SnapshotValidationTests(unittest.TestCase):
    def test_aliases_and_all_registry_checks_bind_at_snapshot(self):
        from sqlglot import parse_one, exp
        tree=parse_one(at_snapshot("SELECT count(*) FROM material.link_occurrences p LEFT JOIN ingest.visits v USING (visit_id)",123),read='duckdb')
        self.assertEqual([t.alias for t in tree.find_all(exp.Subquery)],['p','v'])
        for spec in PROJECTIONS:
            for sql in spec.validation_queries:duckdb.extract_statements(at_snapshot(sql,123))

    def test_snapshot_validation_ignores_later_mutation_without_staging(self):
        with duckdb.connect() as db, TemporaryDirectory() as root:
            db.execute('LOAD ducklake')
            db.execute(f"ATTACH 'ducklake:{root}/metadata.duckdb' AS lake (DATA_PATH '{root}/files')")
            db.execute('USE lake');db.execute('CREATE SCHEMA material')
            spec=next(s for s in PROJECTIONS if s.name=='html_elements')
            db.register('empty',pa.Table.from_pylist([],schema=spec.arrow_schema))
            db.execute('CREATE TABLE material.html_elements AS SELECT * FROM empty')
            snapshot=db.execute("SELECT max(snapshot_id) FROM ducklake_snapshots('lake')").fetchone()[0]
            db.execute("INSERT INTO material.html_elements(node_index,subtree_end_index,text,text_start,text_end) VALUES (5,4,'bad',0,3)")
            self.assertEqual(db.execute(spec.validation_queries[0]).fetchone()[0],1)
            with validation_statements(SimpleNamespace(),spec,snapshot=snapshot) as checks:
                for _,_,sql in checks:self.assertEqual(db.execute(sql).fetchone()[0],0)

    def test_content_ranges_cover_every_row_once_including_null_and_boundaries(self):
        from dataclasses import replace
        with duckdb.connect() as db:
            db.execute('CREATE SCHEMA material')
            db.execute('CREATE TABLE material.html_elements(content_sha256 VARCHAR, node_index INTEGER, subtree_end_index INTEGER, text VARCHAR, text_start BIGINT, text_end BIGINT)')
            keys = [None, '', '00', '03ff', '04', 'fc', 'zz', '😀']
            db.executemany("INSERT INTO material.html_elements VALUES (?,5,4,'bad',0,3)", [(key,) for key in keys])
            spec = next(s for s in PROJECTIONS if s.name == 'html_elements')
            with validation_statements(SimpleNamespace(), spec) as generated:
                checks = list(generated)
            self.assertEqual(len(checks), 64)
            self.assertEqual(sum(db.execute(sql).fetchone()[0] for _,_,sql in checks), len(keys))
            nonlocal_check = replace(spec, validation_queries=(
                'SELECT count(*) FROM material.html_elements GROUP BY content_sha256',))
            with validation_statements(SimpleNamespace(), nonlocal_check) as generated:
                self.assertEqual(len(list(generated)), 1)

    def test_validation_memory_ceiling_preserves_smaller_operator_limits(self):
        from periplus.materialization.validation import bound_validation_memory
        with duckdb.connect() as db:
            catalogue = SimpleNamespace(
                trusted_remote_rows=lambda sql: db.execute(sql).fetchall(),
                trusted_remote_execute=lambda sql: db.execute(sql))
            db.execute("SET memory_limit='2GB'")
            ceiling = db.execute("SELECT current_setting('memory_limit')").fetchone()[0]
            db.execute("SET memory_limit='8GB'")
            bound_validation_memory(catalogue)
            self.assertEqual(db.execute("SELECT current_setting('memory_limit')").fetchone()[0], ceiling)
            db.execute("SET memory_limit='512MB'")
            smaller = db.execute("SELECT current_setting('memory_limit')").fetchone()[0]
            bound_validation_memory(catalogue)
            self.assertEqual(db.execute("SELECT current_setting('memory_limit')").fetchone()[0], smaller)

    def test_partitioned_identity_counts_match_global_duplicates(self):
        from periplus.materialization.validation import identity_statements
        with duckdb.connect() as db:
            db.execute('CREATE SCHEMA material')
            db.execute('CREATE TABLE material.html_terms(term VARCHAR, content_sha256 VARCHAR)')
            rows = [(term, content) for term in (None, '', '0', 'a', 'z', '日本語', '😀') for content in ('first', 'second', 'first')]
            db.executemany('INSERT INTO material.html_terms VALUES (?,?)', rows)
            spec = next(s for s in PROJECTIONS if s.name == 'html_terms')
            expected = db.execute('SELECT count(*)-count(DISTINCT(term,content_sha256)) FROM material.html_terms').fetchone()[0]
            queries = list(identity_statements(spec, None))
            self.assertGreater(len(queries), 1)
            self.assertEqual(sum(db.execute(sql).fetchone()[0] for _,sql in queries), expected)
