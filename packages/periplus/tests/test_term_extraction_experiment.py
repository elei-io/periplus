"""Semantic guard for experimental literal/key/term extraction comparisons."""
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

_EXPERIMENTS = Path(__file__).resolve().parents[3] / "benchmarks/query/experiments"
sys.path.insert(0, str(_EXPERIMENTS))
try:
    import term_extraction
finally:
    sys.path.remove(str(_EXPERIMENTS))


class TermExtractionTests(unittest.TestCase):
    def test_fixed_multikey_matches_survive_unrelated_appends(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            lake = term_extraction.Experiment(root, postings_buckets=0)
            try:
                c = lake.connection
                term_extraction.install_surface(c)
                counts = (2, 4)
                fixed = {}
                for start, end in ((0, 6), (6, 10)):
                    sources = [term_extraction.scoped_source(i, counts) for i in range(start, end)]
                    lake.commit(term_extraction.prepare(root / 'batch.parquet', sources))
                    term_extraction.add_dom(c, sources, create=start == 0)
                    for count in counts:
                        keys = [hashlib.sha256(term_extraction.scoped_source(i, counts).encode()).hexdigest() for i in range(count)]
                        term = f'matchscope{count}end'
                        self.assertEqual(c.execute('SELECT content_id FROM public_v1.term WHERE text=? ORDER BY content_id', [term]).fetchall(), [(k,) for k in sorted(keys)])
                        selected = term_extraction.variants('html_heading', keys, term)
                        baseline = c.execute(selected['literal']).fetchall()
                        self.assertEqual(len(baseline), count * 14)
                        self.assertEqual(fixed.setdefault(count, baseline), baseline)
                        for name in ('term_exists', 'scoped_barrier', 'api_prose_native', 'api_prose_barrier'):
                            self.assertEqual(c.execute(selected[name]).fetchall(), baseline, name)
            finally:
                lake.close()

    def test_variants_preserve_empty_headings_and_whole_content_membership(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            lake = term_extraction.Experiment(root, postings_buckets=0)
            try:
                sources = [term_extraction.source(i) for i in range(3)]
                keys = [hashlib.sha256(s.encode()).hexdigest() for s in sources]
                lake.commit(term_extraction.prepare(root / "batch.parquet", sources))
                c = lake.connection
                term_extraction.install_surface(c)
                term_extraction.add_dom(c, sources, create=True)
                candidate = term_extraction.variants('html_heading', keys[:1], 'uniquematch')['api_prose_barrier']
                profile = json.loads(c.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + candidate).fetchone()[1])
                scanned = {}
                def visit(node):
                    table = node.get('extra_info', {}).get('Table')
                    if table in ('html_nodes', 'html_elements'):
                        scanned[table] = node['operator_cardinality']
                    for child in node.get('children', []):
                        visit(child)
                visit(profile)
                for table in ('html_nodes', 'html_elements'):
                    expected = c.execute(f'SELECT count(*) FROM material.{table} WHERE content_sha256=?', [keys[0]]).fetchone()[0]
                    self.assertEqual(scanned[table], expected)
                for selected, term in ((keys[:1], "uniquematch"), (keys[:2], "monkeys"), (["f" * 64], "absent")):
                    for relation in ("prose", "html_heading"):
                        expected = None
                        for name, sql in term_extraction.variants(relation, selected, term).items():
                            with self.subTest(term=term, relation=relation, variant=name):
                                rows = c.execute(sql).fetchall()
                                if expected is None:
                                    expected = rows
                                self.assertEqual(rows, expected)
                                count = 0 if term == "absent" else len(selected)
                                self.assertEqual(len(rows), count * (14 if relation == "html_heading" else 1))
                                if relation == "html_heading":
                                    self.assertEqual(sum(row[-1] == "" for row in rows), count)
                                    self.assertTrue(all("monkeys" not in row[-1] for row in rows))
            finally:
                lake.close()
