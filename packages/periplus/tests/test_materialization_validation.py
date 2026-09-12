from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory

import duckdb

from periplus.materialization.registry import PROJECTIONS
from periplus.materialization.validation import validation_statements


class PartitionedValidationTests(unittest.TestCase):
    def test_complete_counts_and_cleanup_on_failure(self):
        spec = next(p for p in PROJECTIONS if p.name == 'posting')
        with duckdb.connect() as db, TemporaryDirectory() as root:
            db.execute('CREATE SCHEMA material')
            db.execute('CREATE TABLE material.posting(text VARCHAR, content_sha256 VARCHAR, frequency BIGINT, positions BIGINT[], node_indexes INTEGER[][])')
            db.execute('CREATE TABLE material.html_nodes(content_sha256 VARCHAR, node_index INTEGER, node_type VARCHAR)')
            db.execute("INSERT INTO material.html_nodes VALUES ('a',1,'text'),('b',2,'text'),('a',3,'element')")
            db.execute("INSERT INTO material.posting VALUES ('one','a',2,[0,1],[[1],[3]]),('two','b',2,[0,1],[[2],[99]]),('three','a',1,[2],[[1]]),('bad','c',1,[0],[[]]),('null','a',1,[0],[NULL])")
            catalogue = SimpleNamespace(trusted_remote_execute=db.execute)
            expected = [db.execute(sql).fetchone()[0] for sql in spec.validation_queries]
            with patch('periplus.materialization.validation.TemporaryDirectory', side_effect=lambda **kwargs: TemporaryDirectory(dir=root, **kwargs)):
                with validation_statements(catalogue, spec, partitions=7) as statements:
                    actual = [0] * len(expected)
                    for index, _, sql in statements:
                        actual[index] += db.execute(sql).fetchone()[0]
                    self.assertEqual(actual, expected)
                    self.assertTrue(list(Path(root).iterdir()))
                self.assertEqual(list(Path(root).iterdir()), [])
                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    with validation_statements(catalogue, spec, partitions=3):
                        raise RuntimeError('cancelled')
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_nonexpanding_queries_remain_unchanged(self):
        spec = next(p for p in PROJECTIONS if p.name == 'html_jsonld')
        with validation_statements(None, spec) as statements:
            self.assertEqual([sql for _, _, sql in statements], list(spec.validation_queries))

    def test_reject_self_join_and_unequal_reference_partitioning(self):
        spec = next(p for p in PROJECTIONS if p.name == 'posting')
        for sql in [
            'SELECT count(*) FROM (SELECT DISTINCT unnest(node_indexes) FROM material.posting) p',
            'SELECT count(*) FROM material.posting a JOIN material.posting b USING(content_sha256), unnest(a.positions)',
            'SELECT count(*) FROM (SELECT unnest(node_indexes) FROM material.posting) p JOIN material.html_nodes n ON true',
        ]:
            with self.assertRaises(ValueError):
                with validation_statements(None, replace(spec, validation_queries=(sql,))):
                    self.fail('unsafe partitioning accepted')

class SnapshotValidationTests(unittest.TestCase):
    def test_pins_both_sides_and_preserves_aliases(self):
        from sqlglot import exp, parse_one
        from periplus.materialization.validation import at_snapshot
        sql = "SELECT count(*) FROM material.link_occurrences p LEFT JOIN ingest.visits v USING (visit_id)"
        pinned = parse_one(at_snapshot(sql, 123), read='duckdb')
        tables = list(pinned.find_all(exp.Table))
        self.assertEqual([t.alias for t in pinned.find_all(exp.Subquery)], ['p','v'])
        self.assertEqual([t.args['when'].expression.this for t in tables], ['123','123'])

    def test_staging_copy_is_pinned_before_reading_files(self):
        from periplus.materialization.validation import validation_statements
        spec = next(p for p in PROJECTIONS if p.name == 'posting')
        recorded = []
        catalogue = SimpleNamespace(trusted_remote_execute=recorded.append)
        with validation_statements(catalogue, spec, snapshot=123) as statements:
            sqls = list(statements)
        self.assertEqual(len(recorded), 2)
        self.assertTrue(all('AT (VERSION => 123)' in sql for sql in recorded))
        self.assertTrue(all('AT (VERSION => 123)' in sql for index, _, sql in sqls if index < 2))

    def test_every_registry_validation_parses_in_duckdb(self):
        from periplus.materialization.validation import at_snapshot
        for spec in PROJECTIONS:
            for sql in spec.validation_queries:
                with self.subTest(projection=spec.name, sql=sql):
                    duckdb.extract_statements(at_snapshot(sql, 123))

    def test_registry_checks_execute_against_a_pinned_ducklake_snapshot(self):
        import pyarrow as pa
        with duckdb.connect() as db, TemporaryDirectory() as root:
            try:
                db.execute('LOAD ducklake')
            except duckdb.Error:
                self.skipTest('DuckLake extension is not installed')
            db.execute(f"ATTACH 'ducklake:{root}/metadata.duckdb' AS lake (DATA_PATH '{root}/files')")
            db.execute('USE lake')
            db.execute('CREATE SCHEMA material')
            db.execute('CREATE SCHEMA ingest')
            db.execute('CREATE TABLE ingest.visits(visit_id VARCHAR, finished_at TIMESTAMPTZ)')
            db.execute('CREATE TABLE ingest.documents(document_id VARCHAR, visit_id VARCHAR, content_sha256 VARCHAR)')
            for spec in PROJECTIONS:
                db.register('empty_rows', pa.Table.from_pylist([], schema=spec.arrow_schema))
                db.execute(f'CREATE TABLE material.{spec.name} AS SELECT * FROM empty_rows')
                db.unregister('empty_rows')
            db.execute("INSERT INTO material.html_nodes(content_sha256,node_index,node_type) VALUES ('a',99,'text')")
            db.execute("INSERT INTO material.posting VALUES ('word','a',1,[0],[[99]])")
            snapshot = db.execute("SELECT max(snapshot_id) FROM ducklake_snapshots('lake')").fetchone()[0]
            db.execute('DELETE FROM material.html_nodes')
            posting = next(p for p in PROJECTIONS if p.name == 'posting')
            self.assertEqual(db.execute(posting.validation_queries[-1]).fetchone()[0], 1)
            catalogue = SimpleNamespace(trusted_remote_execute=db.execute)
            for spec in PROJECTIONS:
                with validation_statements(catalogue, spec, snapshot=snapshot, partitions=3) as statements:
                    for index, partition, sql in statements:
                        with self.subTest(projection=spec.name, query=index, partition=partition):
                            self.assertEqual(db.execute(sql).fetchone()[0], 0)
