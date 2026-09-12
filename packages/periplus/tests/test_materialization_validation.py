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
