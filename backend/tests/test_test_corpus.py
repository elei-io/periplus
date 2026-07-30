import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import httpx


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "test_corpus.py"
SPEC = importlib.util.spec_from_file_location("atlas_test_corpus", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
test_corpus = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = test_corpus
SPEC.loader.exec_module(test_corpus)


class TestCorpusTests(unittest.TestCase):
    @staticmethod
    def candidate(
        *,
        url: str,
        status: int,
        offset: int,
    ) -> dict[str, object]:
        return {
            "url": url,
            "hostname": url.split("/", 3)[2],
            "registered_domain": url.split("/", 3)[2],
            "status": status,
            "timestamp": "20260101000000",
            "filename": "crawl-data/example.warc.gz",
            "offset": str(offset),
            "length": "10",
            "mime": "text/html",
            "encoding": "UTF-8",
        }

    def test_failure_rate_is_taken_from_noise_without_growing_total(self):
        targets = test_corpus.targets_for(known=1_000, noise=100_000, rate=0.02)

        self.assertEqual(targets.known, 1_000)
        self.assertEqual(targets.noise, 97_980)
        self.assertEqual(targets.failure, 2_020)
        self.assertEqual(targets.total, 101_000)

    def test_dataset_identity_pins_version_crawl_and_seed(self):
        self.assertEqual(
            test_corpus.dataset_name("CC-MAIN-2026-25", 42),
            "atlas-test-corpus/v3/CC-MAIN-2026-25/42",
        )

    def test_known_domain_exclusion_includes_subdomains(self):
        domains = ["example.com", "docs.python.org"]

        self.assertTrue(test_corpus.domain_is_known("example.com", domains))
        self.assertTrue(test_corpus.domain_is_known("www.example.com", domains))
        self.assertTrue(
            test_corpus.domain_is_known("docs.python.org", domains)
        )
        self.assertFalse(
            test_corpus.domain_is_known("packages.python.org", domains)
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

    def test_addition_bounds_start_after_each_tier_high_water_mark(self):
        bounds = test_corpus.addition_bounds(
            {
                "known": {0, 9, 10},
                "noise": {0, 19, 20, 21},
                "failure": {0, 3},
            },
            test_corpus.Targets(known=10, noise=20, failure=3),
        )

        self.assertEqual(
            bounds,
            {
                "known": (11, 21),
                "noise": (22, 42),
                "failure": (4, 7),
            },
        )

    def test_existing_corpus_query_uses_the_public_visit_contract(self):
        response = mock.Mock()
        response.json.return_value = {
            "rows": [["known", [0, 2]], ["failure", [1]]]
        }
        client = mock.MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = response

        with mock.patch.object(
            test_corpus.httpx,
            "Client",
            return_value=client,
        ):
            existing = test_corpus.query_existing(
                "http://atlas.example",
                "corpus-v1",
            )

        request = client.post.call_args
        self.assertEqual(
            request.args[0],
            "http://atlas.example/sql/query",
        )
        self.assertIn("FROM web.visit", request.kwargs["json"]["sql"])
        self.assertEqual(existing["known"], {0, 2})
        self.assertEqual(existing["noise"], set())
        self.assertEqual(existing["failure"], {1})

    def test_ingestion_waits_until_every_selected_ordinal_is_visible(self):
        selected = [
            test_corpus.Capture(
                tier="known",
                ordinal=4,
                url="https://example.com/4",
                status=200,
                observed_at="2026-01-01T00:00:00+00:00",
                filename="crawl-data/example.warc.gz",
                offset=4,
                length=10,
                declared_media_type="text/html",
                charset=None,
            ),
            test_corpus.Capture(
                tier="known",
                ordinal=5,
                url="https://example.com/5",
                status=200,
                observed_at="2026-01-01T00:00:00+00:00",
                filename="crawl-data/example.warc.gz",
                offset=5,
                length=10,
                declared_media_type="text/html",
                charset=None,
            ),
        ]
        empty = {"known": {4}, "noise": set(), "failure": set()}
        complete = {"known": {4, 5}, "noise": set(), "failure": set()}

        with mock.patch.object(
            test_corpus,
            "query_existing",
            side_effect=[empty, complete],
        ) as query:
            test_corpus.wait_for_ingestion(
                "http://atlas.example",
                "corpus-v1",
                selected,
                timeout_seconds=1,
                poll_seconds=0,
            )

        self.assertEqual(query.call_count, 2)

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

    def test_shortage_advice_names_the_owning_domain_pool(self):
        self.assertEqual(
            test_corpus.shortage_advice("known"),
            "add known domains or reduce --known",
        )
        self.assertEqual(
            test_corpus.shortage_advice("noise"),
            "increase --noise-index-shards or reduce --noise",
        )
        self.assertEqual(
            test_corpus.shortage_advice("failure"),
            "increase --noise-index-shards or reduce --fail",
        )

    def test_dry_run_selects_and_caches_without_querying_atlas(self):
        known = self.candidate(
            url="https://known.example/page",
            status=200,
            offset=1,
        )
        noise = self.candidate(
            url="https://noise.example/page",
            status=200,
            offset=2,
        )
        failure = self.candidate(
            url="https://failure.example/missing",
            status=404,
            offset=3,
        )
        with tempfile.TemporaryDirectory() as directory:
            arguments = test_corpus.parse_arguments(
                [
                    "--known",
                    "1",
                    "--noise",
                    "2",
                    "--fail",
                    str(1 / 3),
                    "--cache-dir",
                    directory,
                    "--dry-run",
                ]
            )
            with (
                mock.patch.object(
                    test_corpus,
                    "query_existing",
                    side_effect=AssertionError("Atlas must not be queried"),
                ),
                mock.patch.object(
                    test_corpus,
                    "query_domain_candidates",
                    return_value=[known],
                ),
                mock.patch.object(
                    test_corpus,
                    "query_noise_index_candidates",
                    return_value=([noise], [failure]),
                ),
            ):
                result = test_corpus.reconcile(arguments)

            manifest = test_corpus.load_manifest(
                test_corpus.manifest_path(
                    Path(directory),
                    arguments.crawl,
                    arguments.seed,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            [capture.tier for capture in manifest],
            ["known", "noise", "failure"],
        )


if __name__ == "__main__":
    unittest.main()
