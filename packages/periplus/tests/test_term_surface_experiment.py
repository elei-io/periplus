"""Public term semantics over real disposable DuckLake projections."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from concurrent.futures import ThreadPoolExecutor

_EXPERIMENTS = Path(__file__).resolve().parents[3] / "benchmarks/query/experiments"
sys.path.insert(0, str(_EXPERIMENTS))
try:
    import term_surface
finally:
    sys.path.remove(str(_EXPERIMENTS))


class TermSurfaceTests(unittest.TestCase):
    def test_icu_dictionary_segmentation_and_utf16_boundaries(self):
        tokenize = term_surface.term_tokens
        self.assertEqual(tokenize("👩‍💻Monkeys 𐐀test zoo"), ["monkeys", "𐐨test", "zoo"])
        samples = ["私は東京で働いています。", "我喜欢在动物园看猴子。", "ภาษาไทยไม่มีเว้นวรรค"]
        expected = [tokenize(s) for s in samples]
        self.assertIn("東京", expected[0])
        self.assertIn("猴子", expected[1])
        self.assertIn("ภาษา", expected[2])
        self.assertTrue(all(len(tokens) > 1 for tokens in expected))
        with ThreadPoolExecutor(max_workers=3) as workers:
            self.assertEqual(list(workers.map(tokenize, samples * 4)), expected * 4)
        self.assertEqual(tokenize(" \n—…👩‍💻"), [])

    def test_public_columns_tokens_frequencies_and_heading_membership(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            lake = term_surface.Experiment(root, postings_buckets=0)
            try:
                sources = [
                    "<h1>Monkeys</h1><p>MONKEYS monkey monkeypox zoo can't 3.14 foo_bar "
                    "café cafe\u0301 Straße STRASSE हिंदी 日本語 👩‍💻</p><script>excluded</script>",
                    "<h1>Elephants</h1><p>monkeys zoo</p>",
                    "<head><title>excluded</title></head><body></body>",
                ]
                batch = term_surface.prepare(root / "batch.parquet", sources + sources[:1])
                lake.commit(batch)
                c = lake.connection
                term_surface.install_surface(c)
                term_surface.add_dom(c, sources, create=True)
                self.assertEqual([r[0] for r in c.execute("DESCRIBE public_v1.term").fetchall()],
                                 ["content_id", "text", "frequency"])
                actual = dict(c.execute("SELECT text,sum(frequency) FROM public_v1.term GROUP BY text").fetchall())
                self.assertEqual(actual, {
                    "monkeys": 3, "monkey": 1, "monkeypox": 1, "zoo": 2,
                    "can't": 1, "3.14": 1, "foo_bar": 1,
                    "café": 2, "strasse": 2, "हिंदी": 1, "日本語": 1, "elephants": 1,
                })
                for query in term_surface.queries("monkeys", "zoo", "monkey").values():
                    self.assertEqual(c.execute(query.format(relation="public_v1.term")).fetchall(),
                                     c.execute(query.format(relation="reference_term")).fetchall())
                self.assertEqual(c.execute("""SELECT h.text FROM public_v1.html_heading h
                    WHERE EXISTS (SELECT 1 FROM public_v1.term w
                    WHERE w.content_id=h.content_id AND w.text='monkeys') ORDER BY h.text""").fetchall(),
                    [("Elephants",), ("Monkeys",)])
                self.assertEqual(c.execute("SELECT * FROM public_v1.term EXCEPT ALL SELECT * FROM reference_term").fetchall(), [])
                self.assertEqual(c.execute("SELECT * FROM reference_term EXCEPT ALL SELECT * FROM public_v1.term").fetchall(), [])
            finally:
                lake.close()
