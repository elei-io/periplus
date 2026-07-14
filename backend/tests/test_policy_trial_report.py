from __future__ import annotations

from types import SimpleNamespace
import unittest
from uuid import uuid4

import duckdb

from repository.catalogue.policy_trials import (
    get_policy_trial_application_domain,
    get_policy_trial_report,
)


class PolicyTrialReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect()
        self.connection.execute(
            """
            CREATE TABLE crawls (
                crawl_id UUID NOT NULL,
                document_id VARCHAR,
                purpose VARCHAR NOT NULL,
                trial_id UUID,
                requested_url VARCHAR NOT NULL,
                url_scheme VARCHAR NOT NULL,
                url_host VARCHAR NOT NULL,
                url_port INTEGER NOT NULL,
                url_registrable_domain VARCHAR NOT NULL,
                captured_at TIMESTAMPTZ NOT NULL,
                status_code INTEGER,
                duration_ms BIGINT,
                crawl_profile_slug VARCHAR NOT NULL,
                trial_candidate_profile_slug VARCHAR,
                config_hash VARCHAR NOT NULL,
                trial_candidate_profile_config_hash VARCHAR,
                failure_code VARCHAR
            );
            CREATE TABLE documents (
                document_id VARCHAR NOT NULL,
                html_size_bytes BIGINT NOT NULL,
                visible_text_chars BIGINT NOT NULL,
                element_count BIGINT NOT NULL,
                quality_flags_json JSON NOT NULL
            );
            """
        )
        self.catalogue = SimpleNamespace(
            connection=self.connection,
            config=SimpleNamespace(alias="memory", schema="main"),
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_summarizes_pairs_and_keeps_incomplete_samples_visible(self) -> None:
        self.connection.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
            [
                ("sample-a", 2000, 800, 20, '["interaction_required"]'),
                ("same-b", 1000, 400, 10, "[]"),
            ],
        )
        recovered_trial, identical_trial, pending_trial = uuid4(), uuid4(), uuid4()
        rows = [
            self._crawl(
                purpose="use",
                trial_id=recovered_trial,
                url="https://example.com/a",
                document_id=None,
                status_code=None,
                duration_ms=100,
                error="HTTP failed",
            ),
            self._crawl(
                purpose="sample",
                trial_id=recovered_trial,
                url="https://example.com/a",
                document_id="sample-a",
                status_code=200,
                duration_ms=400,
            ),
            self._crawl(
                purpose="use",
                trial_id=identical_trial,
                url="https://example.com/b",
                document_id="same-b",
                status_code=200,
                duration_ms=90,
            ),
            self._crawl(
                purpose="sample",
                trial_id=identical_trial,
                url="https://example.com/b",
                document_id="same-b",
                status_code=200,
                duration_ms=190,
            ),
            self._crawl(
                purpose="use",
                trial_id=pending_trial,
                url="https://other.test/pending",
                document_id="same-b",
                status_code=200,
                duration_ms=80,
            ),
            self._crawl(
                purpose="use",
                trial_id=None,
                url="https://other.test/ordinary",
                document_id="same-b",
                status_code=200,
                duration_ms=70,
            ),
        ]
        self.connection.executemany(
            "INSERT INTO crawls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )

        report = get_policy_trial_report(self.catalogue, limit=100, offset=0)

        self.assertEqual(report.summary.use_crawls, 4)
        self.assertEqual(report.summary.selected_trials, 3)
        self.assertEqual(report.summary.sample_crawls, 2)
        self.assertEqual(report.summary.completed_pairs, 2)
        self.assertEqual(report.summary.awaiting_samples, 1)
        self.assertEqual(report.summary.pairs_with_failure, 1)
        self.assertEqual(report.summary.observed_sample_rate, 0.75)
        self.assertEqual(report.total_comparisons, 2)
        example = next(
            item for item in report.comparisons if item.host == "example.com"
        )
        self.assertEqual(example.selected_trials, 2)
        self.assertEqual(example.completed_pairs, 2)
        self.assertEqual(example.recovered_crawls, 1)
        self.assertEqual(example.identical_documents, 1)
        self.assertEqual(example.use_profile, "direct")
        self.assertEqual(example.candidate_profile, "rendered")
        self.assertEqual(example.median_use_quality_flag_count, 0)
        self.assertEqual(example.median_sample_quality_flag_count, 0.5)
        self.assertEqual(example.use_acquisition_failure_count, 1)
        self.assertEqual(example.sample_acquisition_failure_count, 0)
        self.assertEqual(example.verdict, "promising")
        self.assertEqual(
            get_policy_trial_application_domain(
                self.catalogue,
                scheme="https",
                host="example.com",
                port=443,
                profile_slug="rendered",
                profile_config_hash=self._hash("rendered"),
            ),
            "example.com",
        )
        self.assertIsNone(
            get_policy_trial_application_domain(
                self.catalogue,
                scheme="https",
                host="example.com",
                port=443,
                profile_slug="rendered",
                profile_config_hash=self._hash("changed-rendered"),
            )
        )

    def test_keeps_policy_comparison_cohorts_separate_for_one_origin(self) -> None:
        self.connection.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
            [
                ("http-use", 1000, 400, 10, "[]"),
                ("static-sample", 2000, 800, 20, "[]"),
                ("static-use", 1500, 600, 12, "[]"),
                ("stable-sample", 1800, 720, 18, "[]"),
            ],
        )
        first_trial, second_trial = uuid4(), uuid4()
        rows = [
            self._crawl(
                purpose="use",
                trial_id=first_trial,
                url="https://example.com/first",
                document_id="http-use",
                status_code=200,
                duration_ms=100,
                use_profile="direct",
                candidate_profile="rendered",
            ),
            self._crawl(
                purpose="sample",
                trial_id=first_trial,
                url="https://example.com/first",
                document_id="static-sample",
                status_code=200,
                duration_ms=300,
                use_profile="direct",
                candidate_profile="rendered",
            ),
            self._crawl(
                purpose="use",
                trial_id=second_trial,
                url="https://example.com/second",
                document_id="static-use",
                status_code=200,
                duration_ms=200,
                use_profile="rendered",
                candidate_profile="settled",
            ),
            self._crawl(
                purpose="sample",
                trial_id=second_trial,
                url="https://example.com/second",
                document_id="stable-sample",
                status_code=200,
                duration_ms=500,
                use_profile="rendered",
                candidate_profile="settled",
            ),
        ]
        self.connection.executemany(
            "INSERT INTO crawls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

        report = get_policy_trial_report(self.catalogue, limit=100, offset=0)

        self.assertEqual(report.total_comparisons, 2)
        comparisons = {
            (item.use_profile, item.candidate_profile): item
            for item in report.comparisons
        }
        self.assertEqual(
            comparisons[("direct", "rendered")].median_element_delta_percent,
            100.0,
        )
        self.assertEqual(
            comparisons[("rendered", "settled")].median_element_delta_percent,
            50.0,
        )
        self.assertEqual(
            comparisons[("rendered", "settled")].median_duration_delta_ms,
            300.0,
        )
        self.assertEqual(
            comparisons[("rendered", "settled")].verdict,
            "insufficient_evidence",
        )

    def test_computes_query_time_content_features(self) -> None:
        self.connection.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
            [
                ("shared-shell", 1000, 100, 10, "[]"),
                ("sample-1", 1200, 100, 12, "[]"),
                ("sample-2", 1800, 200, 18, "[]"),
                ("sample-3", 2400, 300, 24, "[]"),
            ],
        )
        rows = []
        for index, sample_document in enumerate(
            ("sample-1", "sample-2", "sample-3"), start=1
        ):
            trial_id = uuid4()
            url = f"https://example.com/products/{index}"
            rows.extend(
                [
                    self._crawl(
                        purpose="use",
                        trial_id=trial_id,
                        url=url,
                        document_id="shared-shell",
                        status_code=200,
                        duration_ms=100,
                    ),
                    self._crawl(
                        purpose="sample",
                        trial_id=trial_id,
                        url=url,
                        document_id=sample_document,
                        status_code=200,
                        duration_ms=300,
                    ),
                ]
            )
        self.connection.executemany(
            "INSERT INTO crawls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

        comparison = get_policy_trial_report(
            self.catalogue, limit=100, offset=0
        ).comparisons[0]

        self.assertAlmostEqual(comparison.mean_use_visible_text_chars or 0, 100.0)
        self.assertAlmostEqual(comparison.mean_sample_visible_text_chars or 0, 200.0)
        self.assertAlmostEqual(comparison.use_visible_text_stddev or 0, 0.0)
        self.assertAlmostEqual(comparison.sample_visible_text_stddev or 0, 100.0)
        self.assertAlmostEqual(comparison.use_visible_text_cv or 0, 0.0)
        self.assertAlmostEqual(comparison.sample_visible_text_cv or 0, 0.5)
        self.assertAlmostEqual(comparison.use_distinct_document_ratio or 0, 1 / 3)
        self.assertAlmostEqual(comparison.sample_distinct_document_ratio or 0, 1.0)
        self.assertEqual(comparison.verdict, "promising")
        self.assertIn("distinct documents", comparison.verdict_reason)

    @staticmethod
    def _crawl(
        *,
        purpose: str,
        trial_id,
        url: str,
        document_id: str | None,
        status_code: int | None,
        duration_ms: int,
        error: str | None = None,
        use_profile: str = "direct",
        candidate_profile: str = "rendered",
    ) -> tuple:
        host = url.split("/", 3)[2]
        return (
            uuid4(),
            document_id,
            purpose,
            trial_id,
            url,
            "https",
            host,
            443,
            host,
            "2026-07-14T00:00:00Z",
            status_code,
            duration_ms,
            use_profile if purpose == "use" else candidate_profile,
            candidate_profile if trial_id is not None else None,
            PolicyTrialReportTests._hash(
                use_profile if purpose == "use" else f"{candidate_profile}-fresh"
            ),
            (
                PolicyTrialReportTests._hash(candidate_profile)
                if trial_id is not None
                else None
            ),
            "expected_failure" if error else None,
        )

    @staticmethod
    def _hash(value: str) -> str:
        return (value + "0" * 64)[:64]
