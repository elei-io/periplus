"""Exercise real DuckLake dictionary transactions in a disposable local lake."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import pyarrow.parquet as pq


_PATH = Path(__file__).resolve().parents[3] / "benchmarks/query/experiments/vocabulary_materialization.py"
_SPEC = importlib.util.spec_from_file_location("vocabulary_materialization_experiment", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
experiment = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = experiment
_SPEC.loader.exec_module(experiment)


class VocabularyMaterializationTests(unittest.TestCase):
    def test_unpartitioned_sorted_small_groups_preserve_postings(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            batch = experiment.prepare(root / "batch.parquet", [
                f"<p>{' '.join(f'word{j}' for j in range(100))} document{i}</p>"
                for i in range(200)
            ])
            outputs = []
            for buckets in (8, 0):
                path = root / str(buckets)
                path.mkdir()
                lake = experiment.Experiment(path, postings_buckets=buckets, row_group_size=8192)
                try:
                    lake.commit(batch)
                    self.assertEqual(lake.validate()["term_stats"], 20200)
                    outputs.append(lake.logical_rows())
                    if buckets == 0:
                        files = lake.connection.execute(
                            "SELECT data_file FROM ducklake_list_files('periplus','term_stat',schema=>'material')"
                        ).fetchall()
                        self.assertEqual(len(files), 1)
                        parquet = pq.ParquetFile(files[0][0])
                        self.assertGreater(parquet.metadata.num_row_groups, 1)
                        terms = parquet.read(columns=["term_id"]).column(0).to_pylist()
                        self.assertEqual(terms, sorted(terms))
                finally:
                    lake.close()
            self.assertEqual(outputs[0], outputs[1])

    def test_parallel_admission_replay_rollback_and_reordered_rebuild(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            batches = [experiment.prepare(root / f"{i}.parquet", sources) for i, sources in enumerate([
                ["<p>Monkeys monkeys in the zoo.</p>", "<p>Zoo gardens.</p>"],
                ["<p>Monkeys in forests.</p>", "<p>Zoo gardens.</p>"],
                ["<p>!!!</p>"],
            ])]
            first_root = root / "first"
            first_root.mkdir()
            lake = experiment.Experiment(first_root)
            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    list(pool.map(lake.commit, batches))
                counts = lake.validate()
                self.assertEqual(counts, {"contents": 4, "vocabulary": 6, "term_stats": 9})
                rows = lake.logical_rows()
                ids = lake.connection.execute("SELECT * FROM material.vocabulary ORDER BY term").fetchall()
                snapshot = lake.connection.execute("SELECT id FROM ducklake_current_snapshot('periplus')").fetchone()[0]
                lake.commit(batches[0])  # Successful lake commit, lost receipt, replay.
                self.assertEqual(lake.logical_rows(), rows)
                self.assertEqual(lake.connection.execute("SELECT * FROM material.vocabulary ORDER BY term").fetchall(), ids)
                failed = experiment.prepare(root / "failed.parquet", ["<p>Previouslyunseen</p>"])
                with self.assertRaisesRegex(RuntimeError, "injected failure"):
                    lake.commit(failed, fail_after_vocabulary=True)
                self.assertEqual(lake.validate(), counts)
                self.assertEqual(lake.connection.execute("SELECT * FROM material.vocabulary ORDER BY term").fetchall(), ids)
                lake.commit(failed)
                self.assertEqual(lake.validate()["contents"], 5)
                self.assertEqual(lake.connection.execute(
                    f"SELECT term, term_id FROM material.vocabulary AT (VERSION => {snapshot}) ORDER BY term"
                ).fetchall(), ids)
            finally:
                lake.close()
            second_root = root / "second"
            second_root.mkdir()
            rebuilt = experiment.Experiment(second_root)
            try:
                for batch in reversed(batches):
                    rebuilt.commit(batch)
                self.assertEqual(rebuilt.logical_rows(), rows)
                self.assertEqual(rebuilt.validate(), counts)
            finally:
                rebuilt.close()

    def test_prose_token_boundaries_normalization_and_exclusions(self):
        with TemporaryDirectory() as directory:
            prepared = experiment.prepare(Path(directory) / "terms.parquet", [
                "<head><title>Ignoretitle</title></head><body>"
                "<p>mon<strong>keys</strong> MONKEYS café cafe\u0301 Straße STRASSE</p>"
                "<p>zoo</p><script>ignorescript</script><template>ignoretemplate</template></body>"
            ])
            text = prepared.documents.column("text")[0].as_py()
            self.assertEqual(experiment.term_counts(text), {
                "monkeys": 2, "café": 2, "strasse": 2, "zoo": 1,
            })
