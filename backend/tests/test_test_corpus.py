from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "test_corpus.py"
SPEC = importlib.util.spec_from_file_location("periplus_test_corpus", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
test_corpus = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = test_corpus
SPEC.loader.exec_module(test_corpus)


class TestCorpusTests(unittest.TestCase):
    @staticmethod
    def capture(*, ordinal: int, url: str | None = None) -> object:
        return test_corpus.Capture(
            ordinal=ordinal,
            url=url or f"https://example.com/{ordinal}",
            status=200,
            observed_at="2026-01-01T00:00:00+00:00",
            filename="crawl-data/example.warc.gz",
            offset=ordinal,
            length=10,
            declared_media_type="text/html",
            charset="UTF-8",
        )

    @staticmethod
    def candidate(*, url: str, offset: int) -> dict[str, object]:
        return {
            "url": url,
            "fetch_time": datetime(2026, 1, 1, tzinfo=UTC),
            "status": 200,
            "mime": "text/html",
            "encoding": "UTF-8",
            "filename": "crawl-data/example.warc.gz",
            "offset": offset,
            "length": 10,
        }

    def test_cli_is_only_a_page_count(self):
        arguments = test_corpus.parse_arguments(["15000"])

        self.assertEqual(arguments.pages, 15_000)
        self.assertFalse(hasattr(arguments, "known"))
        self.assertFalse(hasattr(arguments, "noise"))
        self.assertFalse(hasattr(arguments, "failure_rate"))

    def test_dataset_identity_pins_version_crawl_and_seed(self):
        self.assertEqual(
            test_corpus.dataset_name("CC-MAIN-2026-25", 42),
            "periplus-test-corpus/v4/CC-MAIN-2026-25/42",
        )

    def test_manifest_round_trip_preserves_capture(self):
        capture = self.capture(ordinal=3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.jsonl"
            test_corpus.save_manifest(path, [capture])

            self.assertEqual(test_corpus.load_manifest(path), [capture])

    def test_manifest_rejects_duplicate_normalized_urls(self):
        captures = [
            self.capture(ordinal=0, url="HTTPS://EXAMPLE.COM"),
            self.capture(ordinal=1, url="https://example.com/"),
        ]

        with self.assertRaisesRegex(ValueError, "duplicate manifest URL"):
            test_corpus.validate_manifest(captures)

    def test_existing_query_uses_single_page_identity(self):
        with mock.patch.object(
            test_corpus,
            "query_periplus",
            return_value={"rows": [[None]]},
        ) as query:
            empty = test_corpus.query_existing(
                "http://periplus.example",
                "corpus-v4",
            )

        self.assertEqual(empty, set())
        sql = query.call_args.args[1]
        self.assertIn("FROM web.observation", sql)
        self.assertIn("starts_with(source_record_id, 'page:')", sql)

        with mock.patch.object(
            test_corpus,
            "query_periplus",
            return_value={"rows": [[[0, 2, 9]]]},
        ):
            existing = test_corpus.query_existing(
                "http://periplus.example",
                "corpus-v4",
            )
        self.assertEqual(existing, {0, 2, 9})

    def test_lake_url_scan_is_bounded_and_paginated(self):
        with mock.patch.object(
            test_corpus,
            "query_periplus",
            side_effect=[
                {"rows": [["https://a.example/"], ["https://b.example/"]]},
                {"rows": [["https://c.example/"]]},
            ],
        ) as query:
            urls = test_corpus.query_lake_urls(
                "http://periplus.example",
                page_size=2,
            )

        self.assertEqual(
            urls,
            {
                "https://a.example/",
                "https://b.example/",
                "https://c.example/",
            },
        )
        self.assertIn("LIMIT 2 OFFSET 0", query.call_args_list[0].args[1])
        self.assertIn("LIMIT 2 OFFSET 2", query.call_args_list[1].args[1])

    def test_pending_pages_retry_manifest_gaps_before_adding_more(self):
        captures = [self.capture(ordinal=ordinal) for ordinal in range(5)]

        selected = test_corpus.pending_captures(captures, {0, 2, 4}, 2)

        self.assertEqual([capture.ordinal for capture in selected], [1, 3])

    def test_pending_pages_skip_urls_already_in_the_lake(self):
        captures = [self.capture(ordinal=0), self.capture(ordinal=1)]

        selected = test_corpus.pending_captures(
            captures,
            set(),
            2,
            existing_urls={"https://example.com/0"},
        )

        self.assertEqual([capture.ordinal for capture in selected], [1])

    def test_selection_avoids_lake_and_manifest_url_duplicates(self):
        captures = [self.capture(ordinal=0, url="https://cached.example/")]
        candidates = [
            self.candidate(url="https://cached.example/", offset=10),
            self.candidate(url="https://lake.example/", offset=11),
            self.candidate(url="HTTPS://NEW.EXAMPLE", offset=12),
        ]
        with (
            mock.patch.object(
                test_corpus,
                "load_index_paths",
                return_value=["crawl=x/subset=warc/part.parquet"],
            ),
            mock.patch.object(
                test_corpus,
                "download_index_shard",
                return_value=Path("part.parquet"),
            ),
            mock.patch.object(
                test_corpus,
                "sample_index_shard",
                return_value=candidates,
            ),
        ):
            result = test_corpus.select_captures(
                captures=captures,
                existing_ordinals={0},
                existing_urls={"https://lake.example/"},
                count=1,
                crawl="CC-MAIN-test",
                seed=1,
                cache_dir=Path("cache"),
            )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[-1].ordinal, 1)
        self.assertEqual(result[-1].url, "https://new.example/")

    def test_dry_run_does_not_query_or_mutate_periplus(self):
        candidate = self.candidate(url="https://new.example/", offset=1)
        with tempfile.TemporaryDirectory() as directory:
            arguments = test_corpus.parse_arguments(
                ["1", "--cache-dir", directory, "--dry-run"]
            )
            with (
                mock.patch.object(
                    test_corpus,
                    "query_existing",
                    side_effect=AssertionError("Periplus must not be queried"),
                ),
                mock.patch.object(
                    test_corpus,
                    "query_lake_urls",
                    side_effect=AssertionError("Periplus must not be queried"),
                ),
                mock.patch.object(
                    test_corpus,
                    "load_index_paths",
                    return_value=["crawl=x/subset=warc/part.parquet"],
                ),
                mock.patch.object(
                    test_corpus,
                    "download_index_shard",
                    return_value=Path("part.parquet"),
                ),
                mock.patch.object(
                    test_corpus,
                    "sample_index_shard",
                    return_value=[candidate],
                ),
                mock.patch.object(
                    test_corpus,
                    "ingest_capture",
                    side_effect=AssertionError("Periplus must not be changed"),
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
        self.assertEqual(len(manifest), 1)

    def test_ingestion_waits_for_all_selected_ordinals(self):
        selected = [self.capture(ordinal=4), self.capture(ordinal=5)]
        with mock.patch.object(
            test_corpus,
            "query_existing",
            side_effect=[{4}, {4, 5}],
        ) as query:
            test_corpus.wait_for_ingestion(
                "http://periplus.example",
                "corpus-v4",
                selected,
                timeout_seconds=1,
                poll_seconds=0,
            )

        self.assertEqual(query.call_count, 2)


if __name__ == "__main__":
    unittest.main()
