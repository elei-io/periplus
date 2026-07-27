import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import httpx


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "test_corpus.py"
SPEC = importlib.util.spec_from_file_location("atlas_test_corpus", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
test_corpus = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = test_corpus
SPEC.loader.exec_module(test_corpus)


class TestCorpusTests(unittest.TestCase):
    def test_failure_rate_is_taken_from_noise_without_growing_total(self):
        targets = test_corpus.targets_for(known=1_000, noise=100_000, rate=0.02)

        self.assertEqual(targets.known, 1_000)
        self.assertEqual(targets.noise, 97_980)
        self.assertEqual(targets.failure, 2_020)
        self.assertEqual(targets.total, 101_000)

    def test_dataset_identity_pins_version_crawl_and_seed(self):
        self.assertEqual(
            test_corpus.dataset_name("CC-MAIN-2026-25", 42),
            "atlas-test-corpus/v1/CC-MAIN-2026-25/42",
        )

    def test_manifest_requires_contiguous_ordinals_per_tier(self):
        capture = test_corpus.Capture(
            tier="known",
            ordinal=1,
            url="https://example.com/",
            status=200,
            observed_at="2026-01-01T00:00:00+00:00",
            filename="crawl-data/example.warc.gz",
            offset=1,
            length=2,
            declared_media_type="text/html",
            charset="UTF-8",
        )

        with self.assertRaisesRegex(ValueError, "non-contiguous known"):
            test_corpus.validate_manifest([capture])

    def test_manifest_round_trip_preserves_capture(self):
        capture = test_corpus.Capture(
            tier="noise",
            ordinal=0,
            url="https://example.com/",
            status=200,
            observed_at="2026-01-01T00:00:00+00:00",
            filename="crawl-data/example.warc.gz",
            offset=1,
            length=2,
            declared_media_type="text/html",
            charset=None,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.jsonl"
            test_corpus.save_manifest(path, [capture])

            self.assertEqual(test_corpus.load_manifest(path), [capture])

    def test_excess_predicate_uses_each_tier_target(self):
        predicate = test_corpus.excess_predicate(
            test_corpus.Targets(known=10, noise=20, failure=3)
        )

        self.assertIn("= 'known'", predicate)
        self.assertIn(">= 10", predicate)
        self.assertIn("= 'noise'", predicate)
        self.assertIn(">= 20", predicate)
        self.assertIn("= 'failure'", predicate)
        self.assertIn(">= 3", predicate)

    def test_common_crawl_no_capture_404_is_an_empty_domain_result(self):
        response = httpx.Response(
            404,
            json={"message": "No Captures found for: example.invalid/*"},
        )

        self.assertTrue(test_corpus.is_empty_cdx_result(response))
        self.assertFalse(
            test_corpus.is_empty_cdx_result(
                httpx.Response(404, json={"message": "missing collection"})
            )
        )

    def test_candidate_tiers_do_not_reuse_the_same_capture(self):
        candidates = [
            {
                "url": f"https://example.com/{ordinal}",
                "status": "200",
                "timestamp": "20260101000000",
                "filename": "crawl-data/example.warc.gz",
                "offset": str(ordinal),
                "length": "10",
                "mime": "text/html",
            }
            for ordinal in range(3)
        ]
        known = []
        noise = []
        excluded = set()

        test_corpus.extend_from_candidates(
            known,
            tier="known",
            target=2,
            candidates=candidates,
            excluded=excluded,
        )
        test_corpus.extend_from_candidates(
            noise,
            tier="noise",
            target=1,
            candidates=candidates,
            excluded=excluded,
        )

        self.assertEqual([capture.offset for capture in known], [0, 1])
        self.assertEqual([capture.offset for capture in noise], [2])


if __name__ == "__main__":
    unittest.main()
