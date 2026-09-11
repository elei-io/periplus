"""Correctness checks for the disposable layout experiment."""

import importlib.util
import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import pyarrow as pa
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.search_text import build_search_text

PATH = (
    Path(__file__).resolve().parents[3]
    / "benchmarks/query/experiments/posting_summary.py"
)
spec = importlib.util.spec_from_file_location("posting_summary", PATH)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class PostingSummaryTest(unittest.TestCase):
    def test_token_provenance_and_split_occurrence(self):
        nodes, _ = parse_document(
            "<body><p>mon<b>key</b> monkey e<i>\u0301</i> 日本語</p><script>monkey</script></body>"
        )
        tokens = experiment.positional_tokens(nodes)
        reference = build_search_text(nodes)
        self.assertEqual(Counter(t for t, _ in tokens), reference.content_counts)
        self.assertEqual(
            Counter((t, n) for t, ns in tokens for n in ns), reference.node_counts
        )
        self.assertEqual(len(tokens[0][1]), 2)
        self.assertEqual(
            sum(v for (t, _), v in reference.node_counts.items() if t == "monkey"), 3
        )
        self.assertEqual(reference.content_counts["monkey"], 2)

    def test_layouts_have_equal_discovery_and_frequencies(self):
        db = duckdb.connect()
        data = pa.table(
            {
                "term_id": [1, 1, 2, 1],
                "content_id": ["a", "a", "a", "b"],
                "position": [0, 1, 2, 0],
                "node_indexes": [[1, 2], [3], [4], [1]],
            }
        )
        db.register("source", data)
        with TemporaryDirectory() as root:
            files = experiment.write_batch(
                db, Path(root), "fixture", "SELECT * FROM source"
            )
            for name, p in files.items():
                db.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{p}')")
            db.execute("CREATE VIEW packed_frequency AS SELECT * FROM packed")
            for ids in [[1], [1, 2], [3]]:
                for ranked in [False, True]:
                    results = [
                        db.execute(experiment.query(name, name, ids, ranked)).fetchall()
                        for name in [
                            "summary",
                            "flat",
                            "packed_node",
                            "packed",
                            "packed_frequency",
                        ]
                    ]
                    self.assertTrue(all(r == results[0] for r in results))
        db.close()
