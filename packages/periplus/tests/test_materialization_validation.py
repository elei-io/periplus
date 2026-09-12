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
